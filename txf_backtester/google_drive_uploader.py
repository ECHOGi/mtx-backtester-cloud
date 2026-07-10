# -*- coding: utf-8 -*-
"""Google Drive uploader for MTX backtester cloud results.

This module is optional. If Google API packages or Streamlit secrets are not
configured, the main app can still run; only automatic Drive upload is skipped.
"""
from __future__ import annotations

import io
import mimetypes
import zipfile
from typing import Dict, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]


def build_drive_service(service_account_info: Dict):
    """Create a Google Drive v3 service from a service-account JSON dict."""
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


def _upload_bytes(service, name: str, data: bytes, parent_id: str,
                  mime_type: Optional[str] = None) -> Dict:
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


def upload_zip_result_to_drive(service_account_info: Dict, parent_folder_id: str,
                               result_folder_name: str, zip_name: str,
                               zip_bytes: bytes) -> Dict:
    """Upload a result ZIP and its expanded contents into a new Drive folder.

    Returns a dict with the created folder id/url and uploaded file count.
    """
    service = build_drive_service(service_account_info)
    root = _create_folder(service, result_folder_name, parent_folder_id)
    root_id = root["id"]

    # Keep a complete ZIP backup in the result folder.
    _upload_bytes(service, zip_name, zip_bytes, root_id, "application/zip")

    uploaded_count = 1
    folder_cache = {"": root_id}

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
            if not parts:
                continue
            filename = parts[-1]
            parent_key = ""
            parent_id = root_id
            # Preserve subfolder hierarchy from the ZIP.
            for folder_name in parts[:-1]:
                next_key = f"{parent_key}/{folder_name}" if parent_key else folder_name
                if next_key not in folder_cache:
                    created = _create_folder(service, folder_name, parent_id)
                    folder_cache[next_key] = created["id"]
                parent_key = next_key
                parent_id = folder_cache[next_key]
            _upload_bytes(service, filename, z.read(info.filename), parent_id,
                          _mime_for_zip_member(filename))
            uploaded_count += 1

    return {
        "folder_id": root_id,
        "folder_name": result_folder_name,
        "folder_url": root.get("webViewLink") or f"https://drive.google.com/drive/folders/{root_id}",
        "uploaded_count": uploaded_count,
    }
