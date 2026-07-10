# -*- coding: utf-8 -*-
"""Google Drive integration for the MTX backtester cloud app.

Primary authentication uses a user's OAuth refresh token so uploads consume the
user's own Google Drive storage quota. Service-account credentials remain as a
backward-compatible fallback for Shared Drive deployments.
"""
from __future__ import annotations

import io
import json
import mimetypes
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]


def build_drive_service(auth_config: Dict):
    """Create a Google Drive v3 service from OAuth or service-account settings."""
    auth_type = str(auth_config.get("auth_type", "")).strip().lower()
    if auth_type == "oauth" or auth_config.get("refresh_token"):
        required = ["client_id", "client_secret", "refresh_token"]
        missing = [key for key in required if not auth_config.get(key)]
        if missing:
            raise ValueError(f"Google OAuth 設定缺少：{', '.join(missing)}")
        credentials = UserCredentials(
            token=None,
            refresh_token=auth_config["refresh_token"],
            token_uri=auth_config.get("token_uri") or "https://oauth2.googleapis.com/token",
            client_id=auth_config["client_id"],
            client_secret=auth_config["client_secret"],
            scopes=DRIVE_SCOPE,
        )
        # Refresh immediately so configuration errors appear before any folder is created.
        credentials.refresh(Request())
    else:
        service_account_info = auth_config.get("service_account_info") or auth_config
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=DRIVE_SCOPE,
        )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _create_folder(service, name: str, parent_id: str) -> Dict:
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    return service.files().create(
        body=metadata,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()


def _upload_bytes(
    service,
    name: str,
    data: bytes,
    parent_id: str,
    mime_type: Optional[str] = None,
) -> Dict:
    if mime_type is None:
        mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    metadata = {"name": name, "parents": [parent_id]}
    return service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()


def _mime_for_zip_member(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".md"):
        return "text/markdown"
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".txt"):
        return "text/plain"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _escape_drive_query(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def list_json_files_in_drive_folder(auth_config: Dict, folder_id: str) -> List[Dict]:
    """List JSON files in one Drive folder, newest first."""
    if not folder_id:
        raise ValueError("尚未設定 Google Drive 策略投放箱資料夾 ID。")
    service = build_drive_service(auth_config)
    escaped = _escape_drive_query(folder_id)
    response = service.files().list(
        q=(
            f"'{escaped}' in parents and trashed = false and "
            "mimeType != 'application/vnd.google-apps.folder'"
        ),
        fields="files(id,name,modifiedTime,size,webViewLink,mimeType)",
        orderBy="modifiedTime desc,name desc",
        pageSize=100,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = response.get("files", [])
    return [item for item in files if str(item.get("name", "")).lower().endswith(".json")]


def download_drive_file_bytes(auth_config: Dict, file_id: str) -> bytes:
    """Download one non-Google-native Drive file as raw bytes."""
    if not file_id:
        raise ValueError("Google Drive 檔案 ID 不可為空。")
    service = build_drive_service(auth_config)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    output = io.BytesIO()
    downloader = MediaIoBaseDownload(output, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return output.getvalue()


def upload_zip_result_to_drive(
    auth_config: Dict,
    parent_folder_id: str,
    result_folder_name: str,
    zip_name: str,
    zip_bytes: bytes,
) -> Dict:
    """Upload a result ZIP and expanded contents, publishing only when complete.

    A temporary ``__UPLOADING`` folder is used during transfer. On success, a
    completion marker is uploaded and the folder is renamed to its final name.
    On failure, the temporary folder is moved to Trash to avoid empty or partial
    result folders being mistaken for completed runs.
    """
    service = build_drive_service(auth_config)
    temporary_name = f"{result_folder_name}__UPLOADING"
    root = _create_folder(service, temporary_name, parent_folder_id)
    root_id = root["id"]

    try:
        _upload_bytes(service, zip_name, zip_bytes, root_id, "application/zip")
        uploaded_count = 1
        folder_cache = {"": root_id}

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
                if not parts:
                    continue
                filename = parts[-1]
                parent_key = ""
                parent_id = root_id
                for folder_name in parts[:-1]:
                    next_key = f"{parent_key}/{folder_name}" if parent_key else folder_name
                    if next_key not in folder_cache:
                        created = _create_folder(service, folder_name, parent_id)
                        folder_cache[next_key] = created["id"]
                    parent_key = next_key
                    parent_id = folder_cache[next_key]
                _upload_bytes(
                    service,
                    filename,
                    archive.read(info.filename),
                    parent_id,
                    _mime_for_zip_member(filename),
                )
                uploaded_count += 1

        marker = {
            "status": "complete",
            "folder_name": result_folder_name,
            "zip_name": zip_name,
            "uploaded_file_count": uploaded_count,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _upload_bytes(
            service,
            "_UPLOAD_COMPLETE.json",
            json.dumps(marker, ensure_ascii=False, indent=2).encode("utf-8"),
            root_id,
            "application/json",
        )
        uploaded_count += 1

        published = service.files().update(
            fileId=root_id,
            body={"name": result_folder_name},
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
        return {
            "folder_id": root_id,
            "folder_name": result_folder_name,
            "folder_url": published.get("webViewLink")
            or f"https://drive.google.com/drive/folders/{root_id}",
            "uploaded_count": uploaded_count,
        }
    except Exception:
        try:
            service.files().update(
                fileId=root_id,
                body={"trashed": True},
                supportsAllDrives=True,
            ).execute()
        except Exception:
            pass
        raise
