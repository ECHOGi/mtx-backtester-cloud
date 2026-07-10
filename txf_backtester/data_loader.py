# -*- coding: utf-8 -*-
"""
data_loader.py - 讀取臺灣期交所每日行情 CSV，清理成標準格式。

流程：
1. load_folder()          讀資料夾內全部 CSV（自動嘗試編碼），合併多年資料
                          skip_bad=True 時壞檔跳過不崩潰，可收集報告
2. standardize_columns()  中文欄位 -> 標準欄位（缺必要欄位會報清楚錯誤）
3. clean_data()           選商品/時段、排除價差契約與缺漏列、轉數值

輸出欄位：
date, symbol, contract_month, session,
open, high, low, close, volume, open_interest, settlement(若有)
"""
import glob
import os
import re

import pandas as pd

from config import COLUMN_MAP, HARD_REQUIRED_COLUMNS, SESSION_MAP, SYMBOLS

# TAIFEX 檔案常見編碼（新檔多為 utf-8 BOM，舊檔多為 big5/cp950）
ENCODINGS = ["utf-8-sig", "cp950", "big5", "utf-8"]

# 某些期交所/轉檔 CSV 會殘留 DOS EOF（\x1a）或其他控制字元，
# 若不先清掉，會在日期解析時出現提示，甚至留下髒列。
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class DataError(Exception):
    """資料相關錯誤，訊息直接顯示給使用者。"""


def _strip_control_chars(s: pd.Series) -> pd.Series:
    """移除 CSV 儲存/轉檔殘留的控制字元，保留原本 dtype=str 的處理路徑。"""
    return s.astype(str).str.replace(CONTROL_CHARS_RE, "", regex=True).str.strip()


def _clean_text_cells(df: pd.DataFrame) -> pd.DataFrame:
    """輕量清理：只清欄名與完全空列。

    v0.2_patch1 曾對所有文字欄位逐格移除控制字元；在 2015~2025
    約 400 萬列真實資料上會非常慢。這裡改成先清欄名，內容則在
    standardize_columns()/clean_data() 只針對必要欄位處理。
    """
    df = df.copy()
    df.columns = [CONTROL_CHARS_RE.sub("", str(c)).strip() for c in df.columns]
    return df.dropna(how="all").reset_index(drop=True)


def _read_csv(path: str) -> pd.DataFrame:
    last_err = None
    for enc in ENCODINGS:
        try:
            # index_col=False 很重要：期交所部分年度檔案每列尾端多一個逗號，
            # 不加會導致 pandas 把第一欄當 index、整列欄位左移。
            df = pd.read_csv(path, encoding=enc, dtype=str, index_col=False,
                             skipinitialspace=True, low_memory=False)
            return _clean_text_cells(df)
        except (UnicodeDecodeError, pd.errors.ParserError) as e:
            last_err = e
    raise DataError(f"無法讀取 {os.path.basename(path)}（編碼皆失敗）: {last_err}")


def _parse_trade_dates(s: pd.Series) -> pd.Series:
    """
    解析交易日期，不依賴 pandas 自動猜格式，避免混入髒資料時跳出
    「Could not infer format」之類的日期解析提示。
    支援 YYYY/MM/DD、YYYY-M-D、YYYYMMDD，並容忍少量 ROC 年格式。
    無法辨識者回傳 NaT，後續由 clean_data() 剔除。
    """
    raw = _strip_control_chars(s).str.replace("／", "/", regex=False)
    raw = raw.str.replace("－", "-", regex=False).str.replace(".", "-", regex=False)
    raw = raw.str.replace("/", "-", regex=False)

    norm = pd.Series(pd.NA, index=raw.index, dtype="object")

    m = raw.str.extract(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
    ok = m[0].notna()
    norm.loc[ok] = (m.loc[ok, 0] + "-" +
                    m.loc[ok, 1].str.zfill(2) + "-" +
                    m.loc[ok, 2].str.zfill(2))

    m8 = raw.str.extract(r"^(\d{4})(\d{2})(\d{2})$")
    ok8 = norm.isna() & m8[0].notna()
    norm.loc[ok8] = m8.loc[ok8, 0] + "-" + m8.loc[ok8, 1] + "-" + m8.loc[ok8, 2]

    # 保守支援民國年，例如 114/01/02 -> 2025-01-02。
    roc = raw.str.extract(r"^(\d{2,3})-(\d{1,2})-(\d{1,2})$")
    ok_roc = norm.isna() & roc[0].notna()
    if ok_roc.any():
        y = roc.loc[ok_roc, 0].astype(int) + 1911
        norm.loc[ok_roc] = (y.astype(str) + "-" +
                            roc.loc[ok_roc, 1].str.zfill(2) + "-" +
                            roc.loc[ok_roc, 2].str.zfill(2))

    return pd.to_datetime(norm, format="%Y-%m-%d", errors="coerce")


def standardize_columns(df: pd.DataFrame, source: str = "") -> pd.DataFrame:
    """中文欄位改名為標準欄位；缺必要欄位丟出清楚錯誤。"""
    df = _clean_text_cells(df)
    df.columns = [CONTROL_CHARS_RE.sub("", str(c)).strip().replace("　", "")
                  for c in df.columns]
    rename = {c: COLUMN_MAP[c] for c in df.columns if c in COLUMN_MAP}
    df = df.rename(columns=rename)

    # 只清後續會用於篩選/日期解析的文字欄位，避免大檔逐格清理過慢。
    for c in ["date", "contract", "contract_month", "session"]:
        if c in df.columns:
            df[c] = _strip_control_chars(df[c])

    missing = [c for c in HARD_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataError(
            f"檔案 {source} 缺少必要欄位: {missing}\n"
            f"實際欄位: {list(df.columns)}\n"
            f"請確認是期交所「每日行情」CSV，或到 config.py 的 COLUMN_MAP 補上欄位對應。"
        )
    # 舊資料可能沒有交易時段（當時只有日盤）與未沖銷契約數
    if "session" not in df.columns:
        df["session"] = "一般"
    if "open_interest" not in df.columns:
        df["open_interest"] = "0"
    return df


def load_folder(folder: str, skip_bad: bool = True,
                report: list = None) -> pd.DataFrame:
    """
    讀取資料夾內所有 CSV 並合併（不做清理）。
    skip_bad=True（預設）：讀不進或缺必要欄位的檔案跳過並記錄，不直接崩潰。
    report: 傳入 list 可收集每個檔案的讀取結果
            {"file", "status": ok/skip, "rows", "message"}。
    """
    files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    if not files:
        raise DataError(f"資料夾 {folder} 內找不到任何 CSV 檔")
    dfs = []
    for f in files:
        name = os.path.basename(f)
        try:
            raw = _read_csv(f)
            raw_cols = {str(c).strip() for c in raw.columns}
            df = standardize_columns(raw, source=name)
            dfs.append(df)
            if report is not None:
                miss = [c for c in ("交易時段", "未沖銷契約數", "結算價")
                        if c not in raw_cols]
                report.append({"file": name, "status": "ok", "rows": len(df),
                               "message": f"缺選用欄位{miss}(已補預設)" if miss else ""})
        except DataError as e:
            if report is not None:
                report.append({"file": name, "status": "skip", "rows": 0,
                               "message": str(e).splitlines()[0]})
            if not skip_bad:
                raise
    if not dfs:
        raise DataError(f"資料夾 {folder} 內沒有任何可用的 CSV（全部讀取失敗）")
    return pd.concat(dfs, ignore_index=True)


def match_symbol(contract_series: pd.Series, symbol: str) -> pd.Series:
    """依 config 的 codes / keywords 比對「契約」欄，回傳布林遮罩。

    真實期交所檔案多半直接使用 MTX/TMF/TX 代碼；若 exact code
    已有命中，就不再對數百萬列做中文關鍵字 contains 掃描。
    """
    if symbol not in SYMBOLS:
        raise DataError(f"未知商品 {symbol}，請先在 config.py 的 SYMBOLS 中設定")
    spec = SYMBOLS[symbol]
    s = contract_series.astype(str).str.strip()
    mask = s.isin(spec["codes"])
    if bool(mask.any()):
        return mask
    for kw in spec.get("keywords", []):
        mask |= s.str.contains(kw, na=False, regex=False)
    return mask


def _to_num(s: pd.Series) -> pd.Series:
    """'23,150' / '-' / '' / '\x1a' -> 數值或 NaN。"""
    text = s.astype(str).str.replace(CONTROL_CHARS_RE, "", regex=True)
    return pd.to_numeric(text.str.replace(",", "").str.strip(), errors="coerce")


def clean_data(df: pd.DataFrame, symbol: str = "MTX",
               session: str = "regular") -> pd.DataFrame:
    """
    清理流程：
    - 選商品（MTX/TMF/TX...，由 config 決定）
    - 排除價差契約（到期月份含 '/'）
    - 選交易時段 session: 'regular' | 'after_hours' | 'all'
    - 排除 OHLC 為 '-' 或缺漏的列
    """
    # 1) 先選商品、再複製，避免複製整份原始資料（4 百萬列會吃爆記憶體）
    df = df[match_symbol(df["contract"], symbol)].copy()
    if df.empty:
        raise DataError(
            f"資料中找不到商品 {symbol}。請確認 CSV「契約」欄內容，"
            f"或在 config.py 的 SYMBOLS['{symbol}'] 增加 codes/keywords。"
        )
    for col in ["contract", "contract_month", "session"]:
        df[col] = _strip_control_chars(df[col])

    # 2) 排除價差契約
    df = df[~df["contract_month"].str.contains("/", na=False)]

    # 3) 交易時段
    df["session"] = df["session"].map(lambda x: SESSION_MAP.get(x, x))
    if session in ("regular", "after_hours"):
        df = df[df["session"] == session]
    elif session != "all":
        raise DataError(f"session 參數錯誤: {session}（需為 regular/after_hours/all）")

    # 4) 數值轉換 + 排除缺漏（'-' 會轉成 NaN 後被剔除）
    for col in ["open", "high", "low", "close"]:
        df[col] = _to_num(df[col])
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]

    df["volume"] = _to_num(df["volume"]).fillna(0)
    df["open_interest"] = _to_num(df["open_interest"]).fillna(0)
    if "settlement" in df.columns:
        df["settlement"] = _to_num(df["settlement"])

    # 5) 日期：固定格式解析，避免髒資料造成 pandas 自動猜格式提示
    df["date"] = _parse_trade_dates(df["date"])
    df = df.dropna(subset=["date"])

    if df.empty:
        raise DataError(f"{symbol} 清理後沒有有效資料（時段={session}）")

    df["symbol"] = symbol
    return df.sort_values(["date", "contract_month"]).reset_index(drop=True)
