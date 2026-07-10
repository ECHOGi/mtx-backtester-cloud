# -*- coding: utf-8 -*-
"""utils.py - 共用小工具：JSON 參數存讀、CSV 匯出。"""
import json
import os
from dataclasses import asdict, is_dataclass

import pandas as pd


def ensure_dir(path: str):
    """確保資料夾存在。"""
    if path:
        os.makedirs(path, exist_ok=True)


def params_to_dict(params) -> dict:
    """dataclass 或 dict 皆轉成可序列化 dict。"""
    d = asdict(params) if is_dataclass(params) else dict(params)
    # tuple 轉 list 才能存 JSON
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}


def params_to_json_str(params) -> str:
    return json.dumps(params_to_dict(params), ensure_ascii=False, indent=2)


def save_params_json(params, path: str):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(params_to_json_str(params))
    return path


def load_params_json(path_or_file) -> dict:
    """path 或 file-like 物件皆可（配合 Streamlit file_uploader）。"""
    if hasattr(path_or_file, "read"):
        raw = path_or_file.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    with open(path_or_file, encoding="utf-8") as f:
        return json.load(f)


def export_csv(df: pd.DataFrame, path: str) -> str:
    """匯出 CSV（utf-8-sig，Excel 可直接開）。"""
    ensure_dir(os.path.dirname(path))
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    """給 Streamlit download_button 用。"""
    return df.to_csv(index=False).encode("utf-8-sig")
