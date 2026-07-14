# -*- coding: utf-8 -*-
"""v0.8.6.4 長回測結案狀態、續跑與策略選檔自檢。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from checkpointing import (append_rows, checkpoint_paths, prepare_resume,
                           read_meta, write_meta)


def main() -> None:
    root = Path(__file__).resolve().parent
    app_text = (root / "app.py").read_text(encoding="utf-8")
    info = json.loads((root / "version.json").read_text(encoding="utf-8"))
    passed = []

    assert info["version"] in {"v0.8.6.4", "v0.8.6.5", "v0.8.6.6", "v0.8.6.7"}
    assert info["build_id"] in {"20260714-2", "20260714-3", "20260714-4", "20260714-5"}
    passed.append("version_updated")

    # 完成回測後不得再用完整檢查點續跑。
    with tempfile.TemporaryDirectory() as td:
        old = os.environ.get("MTX_CHECKPOINT_DIR")
        os.environ["MTX_CHECKPOINT_DIR"] = td
        try:
            sig = "completed-case"
            rows_path, meta_path = checkpoint_paths(sig)
            append_rows(rows_path, [{"策略名稱": "A", "seed": 1}])
            write_meta(meta_path, {"complete": True, "status": "complete", "done": 1, "total": 1})
            rows, meta, cleared = prepare_resume(sig)
            assert cleared is True
            assert rows.empty and meta == {}
            assert not rows_path.exists() and not meta_path.exists()
            passed.append("completed_checkpoint_cleared")

            # 真正未完成的檢查點仍可續跑。
            sig = "interrupted-case"
            rows_path, meta_path = checkpoint_paths(sig)
            append_rows(rows_path, [{"策略名稱": "A", "seed": 1}])
            write_meta(meta_path, {"complete": False, "status": "running", "done": 1, "total": 10})
            rows, meta, cleared = prepare_resume(sig)
            assert cleared is False
            assert len(rows) == 1 and meta.get("done") == 1
            passed.append("incomplete_checkpoint_resumes")

            # 使用者要求從頭重跑時，未完成檢查點也必須清除。
            rows, meta, cleared = prepare_resume(sig, restart=True)
            assert rows.empty and meta == {}
            assert not rows_path.exists() and not meta_path.exists()
            passed.append("explicit_restart_clears")
        finally:
            if old is None:
                os.environ.pop("MTX_CHECKPOINT_DIR", None)
            else:
                os.environ["MTX_CHECKPOINT_DIR"] = old

    # 上傳完成後不可再用st.rerun重啟同批；只允許手動刷新策略清單時使用。
    assert 'st.session_state["v086_run_status"] = "complete"' in app_text
    assert 'clear_checkpoint(checkpoint_signature)' in app_text
    assert 'drive_strategy_file_id_v0864' in app_text
    assert '重新整理策略清單' in app_text
    final_section = app_text[app_text.index('if auth:', app_text.index('zip_name =')):
                             app_text.index('state = st.session_state.get("v086_result")')]
    assert 'st.rerun()' not in final_section
    passed.append("no_completion_rerun")
    passed.append("drive_selection_uses_file_id")

    print(f"PASS {len(passed)} v0.8.6.4 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
