# -*- coding: utf-8 -*-
"""00631L 元大台灣50正2基準資料與績效計算。

資料優先使用臺灣證券交易所月資料端點。分割事件採明確事件表處理，
避免把分割後價格誤判為單日暴跌；同時保留原始價格以模擬實際持股數。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import calendar
import time

import numpy as np
import pandas as pd
import requests

TWSE_MONTH_URLS = (
    "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY",
    "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
)
BENCHMARK_SYMBOL = "00631L"
BENCHMARK_NAME = "元大台灣50正2（00631L）"

# 依使用者提供且已公告的時程：2026-03-31 恢復交易，1單位分割為22單位。
# effective_date 是分割後第一個可交易日；raw_close 在此日前後會改變尺度。
SPLIT_EVENTS = (
    {"effective_date": "2026-03-31", "ratio": 22.0, "source": "announced"},
)


@dataclass(frozen=True)
class BenchmarkLoadInfo:
    source: str
    rows: int
    start: str
    end: str
    split_adjusted: bool
    warning: str = ""


def _month_starts(start, end) -> list[pd.Timestamp]:
    s = pd.Timestamp(start).to_period("M")
    e = pd.Timestamp(end).to_period("M")
    return [p.to_timestamp() for p in pd.period_range(s, e, freq="M")]


def _roc_date(value: str) -> pd.Timestamp:
    text = str(value).strip().replace("-", "/")
    parts = text.split("/")
    if len(parts) != 3:
        return pd.NaT
    try:
        return pd.Timestamp(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
    except Exception:
        return pd.NaT


def _num(value):
    text = str(value).replace(",", "").replace("--", "").strip()
    if not text:
        return np.nan
    try:
        return float(text)
    except Exception:
        return np.nan


def parse_twse_month(payload: dict) -> pd.DataFrame:
    """解析 TWSE STOCK_DAY JSON；欄位名稱可能因介面版本略有差異。"""
    if not isinstance(payload, dict):
        return pd.DataFrame()
    stat = str(payload.get("stat") or payload.get("status") or "")
    if stat and "OK" not in stat.upper() and "成功" not in stat:
        return pd.DataFrame()
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    if not rows:
        return pd.DataFrame()
    idx = {str(name): i for i, name in enumerate(fields)}

    def col(names: Iterable[str], fallback: int):
        for name in names:
            if name in idx:
                return idx[name]
        return fallback

    date_i = col(["日期"], 0)
    vol_i = col(["成交股數"], 1)
    open_i = col(["開盤價"], 3)
    high_i = col(["最高價"], 4)
    low_i = col(["最低價"], 5)
    close_i = col(["收盤價"], 6)
    value_i = col(["成交金額"], 2)
    out = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= close_i:
            continue
        out.append({
            "date": _roc_date(row[date_i]),
            "open": _num(row[open_i]),
            "high": _num(row[high_i]),
            "low": _num(row[low_i]),
            "close": _num(row[close_i]),
            "volume": _num(row[vol_i]),
            "turnover": _num(row[value_i]),
        })
    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")


def download_twse_history(start, end, timeout: float = 20.0,
                          sleep_seconds: float = 0.08,
                          session: requests.Session | None = None) -> pd.DataFrame:
    """逐月下載00631L官方日行情。網路失敗時拋出可讀錯誤。"""
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 MTX-Backtester/0.8.4"})
    frames = []
    errors = []
    for month in _month_starts(start, end):
        params = {
            "date": month.strftime("%Y%m01"),
            "stockNo": BENCHMARK_SYMBOL,
            "response": "json",
        }
        month_error = None
        frame = pd.DataFrame()
        for url in TWSE_MONTH_URLS:
            try:
                response = sess.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                frame = parse_twse_month(response.json())
                if not frame.empty:
                    break
            except Exception as exc:
                month_error = exc
        if not frame.empty:
            frames.append(frame)
        elif month_error is not None:
            errors.append(f"{month:%Y-%m}: {month_error}")
        if sleep_seconds:
            time.sleep(sleep_seconds)
    if not frames:
        detail = errors[0] if errors else "官方資料沒有回傳內容"
        raise RuntimeError(f"00631L官方資料下載失敗：{detail}")
    out = pd.concat(frames, ignore_index=True).sort_values("date").drop_duplicates("date")
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    out = out[(out["date"] >= start_ts) & (out["date"] <= end_ts)].reset_index(drop=True)
    out.attrs["download_warnings"] = errors
    return out


def _looks_raw_around_split(df: pd.DataFrame, event: dict) -> bool:
    date = pd.Timestamp(event["effective_date"])
    ratio = float(event["ratio"])
    before = df.loc[df["date"] < date, "close"].dropna()
    after = df.loc[df["date"] >= date, "close"].dropna()
    if before.empty or after.empty:
        return True
    observed = float(before.iloc[-1]) / float(after.iloc[0]) if float(after.iloc[0]) else np.inf
    return ratio * 0.55 <= observed <= ratio * 1.45


def apply_split_adjustment(df: pd.DataFrame,
                           events: Iterable[dict] = SPLIT_EVENTS) -> pd.DataFrame:
    """加入 adjusted_* 欄位；只在資料看起來是原始未調整價格時套用。"""
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")
    divisor = pd.Series(1.0, index=out.index)
    applied = []
    for event in sorted(events, key=lambda x: x["effective_date"]):
        if not _looks_raw_around_split(out, event):
            continue
        date = pd.Timestamp(event["effective_date"])
        ratio = float(event["ratio"])
        divisor.loc[out["date"] < date] *= ratio
        applied.append({"effective_date": str(date.date()), "ratio": ratio})
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[f"adjusted_{col}"] = pd.to_numeric(out[col], errors="coerce") / divisor
    out["split_divisor"] = divisor.astype(float)
    out["adjusted_return"] = out["adjusted_close"].pct_change()
    out.attrs["split_events_applied"] = applied
    return out.reset_index(drop=True)


def load_benchmark(start, end, cache_path: str | Path | None = None,
                   uploaded: pd.DataFrame | None = None,
                   refresh: bool = False) -> tuple[pd.DataFrame, BenchmarkLoadInfo]:
    """載入基準。順序：上傳資料 → 本機快取 → TWSE官方下載。"""
    source = ""
    warning = ""
    if uploaded is not None and not uploaded.empty:
        raw = uploaded.copy()
        source = "使用者上傳"
    else:
        path = Path(cache_path) if cache_path else None
        if path and path.exists() and not refresh:
            raw = pd.read_csv(path, encoding="utf-8-sig")
            source = "本機快取（來源TWSE）"
        else:
            raw = download_twse_history(start, end)
            source = "TWSE官方日行情"
            warnings = raw.attrs.get("download_warnings") or []
            if warnings:
                warning = f"部分月份下載失敗：{len(warnings)}個月"
            if path:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    raw.to_csv(path, index=False, encoding="utf-8-sig")
                except Exception as exc:
                    warning = (warning + "；" if warning else "") + f"快取寫入失敗：{exc}"
    rename = {
        "日期": "date", "Date": "date", "date": "date",
        "開盤價": "open", "最高價": "high", "最低價": "low", "收盤價": "close",
        "成交股數": "volume", "成交量": "volume",
    }
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})
    needed = {"date", "close"}
    if not needed.issubset(raw.columns):
        raise ValueError(f"00631L資料缺少欄位：{sorted(needed - set(raw.columns))}")
    for col in ("open", "high", "low"):
        if col not in raw.columns:
            raw[col] = raw["close"]
    if "volume" not in raw.columns:
        raw["volume"] = 0.0
    adjusted = apply_split_adjustment(raw)
    adjusted = adjusted[(adjusted["date"] >= pd.Timestamp(start)) &
                        (adjusted["date"] <= pd.Timestamp(end))].reset_index(drop=True)
    if adjusted.empty:
        raise ValueError("指定期間沒有00631L資料")
    info = BenchmarkLoadInfo(
        source=source, rows=len(adjusted),
        start=str(adjusted["date"].min().date()), end=str(adjusted["date"].max().date()),
        split_adjusted=bool(adjusted.attrs.get("split_events_applied")), warning=warning)
    return adjusted, info


def historical_buy_hold_curve(df: pd.DataFrame, initial_capital: float,
                              buy_fee_rate: float = 0.001425,
                              events: Iterable[dict] = SPLIT_EVENTS) -> pd.DataFrame:
    """以原始價格與整數股數模擬一次買進持有，分割日自動增加股數。"""
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.copy().sort_values("date").reset_index(drop=True)
    first_price = float(frame.loc[0, "close"])
    if first_price <= 0:
        raise ValueError("00631L起始價格無效")
    fee_rate = max(float(buy_fee_rate), 0.0)
    shares = int(float(initial_capital) // (first_price * (1.0 + fee_rate)))
    buy_cost = shares * first_price
    fee = buy_cost * fee_rate
    cash = float(initial_capital) - buy_cost - fee
    first_date = pd.Timestamp(frame["date"].iloc[0]).normalize()
    event_map = {pd.Timestamp(e["effective_date"]): float(e["ratio"])
                 for e in events
                 if pd.Timestamp(e["effective_date"]) > first_date and _looks_raw_around_split(frame, e)}
    applied_events = set()
    rows = []
    previous_date = None
    for _, row in frame.iterrows():
        date = pd.Timestamp(row["date"]).normalize()
        # 若停牌後第一個實際交易日晚於公告生效日，也在第一次跨過事件日時增加股數。
        for event_date, ratio in sorted(event_map.items()):
            if event_date in applied_events:
                continue
            crossed = date >= event_date and (previous_date is None or previous_date < event_date)
            if crossed:
                shares = int(round(shares * ratio))
                applied_events.add(event_date)
        price = float(row["close"])
        value = cash + shares * price
        rows.append({"date": date, "raw_close": price, "shares": shares,
                     "cash": cash, "account_value": value})
        previous_date = date
    out = pd.DataFrame(rows)
    peak = out["account_value"].cummax()
    out["drawdown_pct"] = (out["account_value"] / peak - 1.0) * 100
    return out


def benchmark_metrics(curve: pd.DataFrame, initial_capital: float) -> dict:
    if curve is None or curve.empty:
        return {}
    start = pd.Timestamp(curve["date"].iloc[0])
    end = pd.Timestamp(curve["date"].iloc[-1])
    days = max((end - start).days, 1)
    ending = float(curve["account_value"].iloc[-1])
    ratio = ending / float(initial_capital)
    annual = (ratio ** (365.25 / days) - 1.0) * 100 if ratio > 0 else np.nan
    return {
        "基準名稱": BENCHMARK_NAME,
        "期末資產(元)": round(ending, 0),
        "總損益(元)": round(ending - float(initial_capital), 0),
        "總報酬率(%)": round((ratio - 1.0) * 100, 2),
        "年化報酬率(%)": round(float(annual), 2),
        "最大回撤率(%)": round(float(curve["drawdown_pct"].min()), 2),
        "持有股數": int(curve["shares"].iloc[-1]),
        "剩餘現金(元)": round(float(curve["cash"].iloc[-1]), 0),
    }


def adjusted_returns_by_date(df: pd.DataFrame) -> pd.Series:
    frame = df.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    if "adjusted_return" not in frame.columns:
        frame = apply_split_adjustment(frame)
    return frame.set_index("date")["adjusted_return"].astype(float)
