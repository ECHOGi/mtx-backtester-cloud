# -*- coding: utf-8 -*-
"""長時間情境回測的本機續跑檢查點。

Streamlit 的 session_state 只屬於目前瀏覽器連線；長時間運算若因分頁休眠、
網路中斷或工作階段重建而終止，尚未完成的記憶體結果會消失。本模組把情境
結果逐批追加為 JSON Lines，下一次以相同設定執行時可自動略過已完成項目。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

import pandas as pd


def checkpoint_root() -> Path:
    custom = os.environ.get("MTX_CHECKPOINT_DIR", "").strip()
    root = Path(custom) if custom else Path("/tmp/txf_backtester_checkpoints")
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_signature(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def checkpoint_paths(signature: str) -> tuple[Path, Path]:
    safe = "".join(ch for ch in str(signature) if ch.isalnum() or ch in "-_" )[:80]
    if not safe:
        raise ValueError("檢查點識別碼不可為空")
    root = checkpoint_root()
    return root / f"{safe}.jsonl", root / f"{safe}.meta.json"


def read_rows(path: Path) -> pd.DataFrame:
    """讀取 JSONL；尾端若因中斷留下半行，略過該行而不使整批失敗。"""
    if not path.exists():
        return pd.DataFrame()
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return pd.DataFrame(rows)


def append_rows(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":"), default=str))
            f.write("\n")
            count += 1
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return count


def read_meta(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_meta(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def clear_checkpoint(signature: str) -> None:
    for path in checkpoint_paths(signature):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
