# -*- coding: utf-8 -*-
"""事件區間回測的細粒度續跑檢查點。

每完成一個「策略 × seed × 事件」單元，就把該事件的交易、權益與摘要以
pickle+gzip原子寫入 /tmp。瀏覽器斷線或Streamlit工作階段重建後，只要設定
完全相同，即可略過已完成單元並從下一個事件續跑。
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import gzip
from pathlib import Path
from typing import Any

from checkpointing import checkpoint_root, read_meta, write_meta


def event_checkpoint_dir(signature: str) -> Path:
    safe = "".join(ch for ch in str(signature) if ch.isalnum() or ch in "-_")[:80]
    if not safe:
        raise ValueError("事件檢查點識別碼不可為空")
    root = checkpoint_root() / f"event_{safe}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def event_meta_path(signature: str) -> Path:
    return event_checkpoint_dir(signature) / "meta.json"


def _unit_token(strategy: str, seed: int, event_index: int) -> str:
    raw = json.dumps([str(strategy), int(seed), int(event_index)], ensure_ascii=False,
                     separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{int(event_index):03d}_{int(seed)}_{digest}.pkl.gz"


def save_event_unit(signature: str, payload: dict[str, Any]) -> Path:
    strategy = str(payload["strategy"])
    seed = int(payload["seed"])
    event_index = int(payload["event_index"])
    target = event_checkpoint_dir(signature) / _unit_token(strategy, seed, event_index)
    temp = target.with_suffix(target.suffix + ".tmp")
    with gzip.open(temp, "wb", compresslevel=4) as f:
        pickle.dump(dict(payload), f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temp, target)
    return target


def load_event_units(signature: str) -> dict[tuple[str, int, int], dict[str, Any]]:
    root = event_checkpoint_dir(signature)
    units: dict[tuple[str, int, int], dict[str, Any]] = {}
    for path in sorted(root.glob("*.pkl.gz")):
        try:
            with gzip.open(path, "rb") as f:
                payload = pickle.load(f)
            if not isinstance(payload, dict):
                continue
            key = (str(payload["strategy"]), int(payload["seed"]), int(payload["event_index"]))
            units[key] = payload
        except Exception:
            # 中斷時若留下損壞檔，略過該單元並於本次重算。
            continue
    return units


def clear_event_checkpoint(signature: str) -> None:
    root = checkpoint_root() / f"event_{''.join(ch for ch in str(signature) if ch.isalnum() or ch in '-_')[:80]}"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def prepare_event_resume(signature: str, restart: bool = False):
    if restart:
        clear_event_checkpoint(signature)
    meta_path = event_meta_path(signature)
    meta = read_meta(meta_path)
    completed = bool(meta.get("complete")) or str(meta.get("status", "")).lower() == "complete"
    if completed:
        clear_event_checkpoint(signature)
        return {}, {}, True
    return load_event_units(signature), meta, False


__all__ = [
    "event_checkpoint_dir", "event_meta_path", "save_event_unit",
    "load_event_units", "clear_event_checkpoint", "prepare_event_resume",
    "write_meta",
]
