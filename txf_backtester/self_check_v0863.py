# -*- coding: utf-8 -*-
"""v0.8.6.3起部署根目錄、版本顯示與部署包潔淨度自檢。"""
from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    info = json.loads((root / "version.json").read_text(encoding="utf-8"))
    app_text = (root / "app.py").read_text(encoding="utf-8")
    assert info["version"] in {"v0.8.6.3", "v0.8.6.4", "v0.8.6.5", "v0.8.6.6", "v0.8.6.7", "v0.8.6.8"}
    assert str(info["build_id"]).startswith("20260714-")
    assert "APP_BUILD_ID" in app_text
    assert "建置：{APP_BUILD_ID}" in app_text
    assert (root / "app.py").is_file()
    forbidden = [
        p.name for p in root.iterdir()
        if p.is_file() and (
            "CHANGELOG" in p.name.upper()
            or "FINAL_VERIFICATION_REPORT" in p.name.upper()
            or (p.name.startswith("README_v") and "改版說明" in p.name)
        )
    ]
    assert not forbidden, forbidden
    print("PASS 4 v0.8.6.3+ deployment cases")
    print("- root_app_present")
    print("- centralized_version_info")
    print("- visible_build_id")
    print("- no_release_notes_in_deployment")


if __name__ == "__main__":
    main()
