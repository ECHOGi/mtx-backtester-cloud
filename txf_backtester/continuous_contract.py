# -*- coding: utf-8 -*-
"""continuous_contract.py - 連續契約與日夜盤資料層（v0.8.0）。

v0.8.0 修正：
- session='all' 不再從「一般／盤後」中只取成交量較大的一列。
- 換倉判斷以同一交易日、同一契約的兩時段彙總量進行。
- 選定主力契約後，可輸出：
  1) session bars：保留盤後與一般盤兩列；
  2) daily：依真實交易時序「盤後→一般」合成完整交易日日 K。
- 舊的 build_continuous() API 保持相容，預設仍回傳每日一列。
"""
from __future__ import annotations

import pandas as pd

VALID_METHODS = ("volume_max_daily", "oi_max_daily", "stable_rollover")
METHOD_ALIASES = {"volume": "volume_max_daily", "open_interest": "oi_max_daily"}
LOG_COLUMNS = ["rollover_date", "old_contract", "new_contract", "reason",
               "old_volume", "new_volume", "old_open_interest", "new_open_interest"]
SESSION_OUT_COLUMNS = ["datetime", "trade_date", "symbol", "contract_month", "session",
                       "open", "high", "low", "close", "volume", "open_interest"]
OUT_COLUMNS = ["datetime", "symbol", "contract_month", "session",
               "open", "high", "low", "close", "volume", "open_interest"]


def _prepare(df: pd.DataFrame, exclude_weekly: bool, monthly_only: bool) -> pd.DataFrame:
    """排除週契約，但完整保留一般盤與盤後盤。"""
    out = df.copy()
    if monthly_only:
        out = out[out["contract_month"].astype(str).str.fullmatch(r"\d{6}", na=False)]
    elif exclude_weekly:
        out = out[~out["contract_month"].astype(str).str.upper().str.contains("W", na=False)]
    if out.empty:
        raise ValueError("排除週契約後沒有資料")
    if "session" not in out.columns:
        out["session"] = "regular"
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["date"])
    return out.sort_values(["date", "contract_month", "session"]).reset_index(drop=True)


def _daily_contract_summary(df: pd.DataFrame) -> pd.DataFrame:
    """換倉只看契約層彙總，避免任一時段被丟掉。"""
    return (df.groupby(["date", "contract_month"], as_index=False)
            .agg(symbol=("symbol", "first"),
                 volume=("volume", "sum"),
                 open_interest=("open_interest", "max")))


def _log_row(dt, old_row, new_row, reason) -> dict:
    return {
        "rollover_date": dt,
        "old_contract": None if old_row is None else old_row["contract_month"],
        "new_contract": new_row["contract_month"],
        "reason": reason,
        "old_volume": None if old_row is None else old_row["volume"],
        "new_volume": new_row["volume"],
        "old_open_interest": None if old_row is None else old_row["open_interest"],
        "new_open_interest": new_row["open_interest"],
    }


def _daily_max_selection(summary: pd.DataFrame, field: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    pick = summary.groupby("date")[field].idxmax()
    selected = summary.loc[pick].sort_values("date").reset_index(drop=True)
    logs, prev = [], None
    for _, row in selected.iterrows():
        if prev is not None and row["contract_month"] != prev["contract_month"]:
            logs.append(_log_row(row["date"], prev, row, f"daily_max({field})"))
        prev = row
    return selected[["date", "contract_month"]], pd.DataFrame(logs, columns=LOG_COLUMNS)


def _stable_selection(summary: pd.DataFrame, n_confirm: int, trigger: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trigger not in ("volume", "open_interest"):
        raise ValueError("trigger 必須是 'volume' 或 'open_interest'")
    groups = {dt: day.set_index("contract_month", drop=False)
              for dt, day in summary.groupby("date")}
    dates = sorted(groups)
    current, streak = None, 0
    picks, logs = [], []
    for dt in dates:
        day = groups[dt]
        months = sorted(day["contract_month"].astype(str).tolist())
        if current is None:
            current = months[0]
            logs.append(_log_row(dt, None, day.loc[current], "initial"))
        elif current not in months:
            later = [m for m in months if m > current]
            new = later[0] if later else months[-1]
            logs.append(_log_row(dt, None, day.loc[new], "expired"))
            current, streak = new, 0
        else:
            later = [m for m in months if m > current]
            nxt = later[0] if later else None
            if nxt is not None and float(day.loc[nxt, trigger]) > float(day.loc[current, trigger]):
                streak += 1
            else:
                streak = 0
            if nxt is not None and streak >= int(n_confirm):
                logs.append(_log_row(dt, day.loc[current], day.loc[nxt],
                                     f"{trigger}>current x{int(n_confirm)}d"))
                current, streak = nxt, 0
        picks.append({"date": dt, "contract_month": current})
    return pd.DataFrame(picks), pd.DataFrame(logs, columns=LOG_COLUMNS)


def _select_contracts(prep: pd.DataFrame, method: str, n_confirm: int,
                      trigger: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary = _daily_contract_summary(prep)
    if method == "volume_max_daily":
        return _daily_max_selection(summary, "volume")
    if method == "oi_max_daily":
        return _daily_max_selection(summary, "open_interest")
    return _stable_selection(summary, n_confirm, trigger)


def _attach_selected_sessions(prep: pd.DataFrame, selection: pd.DataFrame) -> pd.DataFrame:
    selected = prep.merge(selection, on=["date", "contract_month"], how="inner")
    selected["trade_date"] = pd.to_datetime(selected["date"]).dt.normalize()
    # 同一交易日的真實時序：前一交易日盤後，再接本交易日一般盤。
    selected["_session_order"] = selected["session"].map({"after_hours": 0, "regular": 1}).fillna(1)
    selected = selected.sort_values(["trade_date", "_session_order"]).reset_index(drop=True)
    selected["datetime"] = selected["trade_date"]
    return selected


def _aggregate_full_session(selected: pd.DataFrame) -> pd.DataFrame:
    """把主力契約的一般／盤後合成一根完整交易日日 K。"""
    if selected.empty:
        return pd.DataFrame(columns=OUT_COLUMNS)
    src = selected.sort_values(["trade_date", "_session_order"])
    out = src.groupby("trade_date", sort=True).agg(
        datetime=("trade_date", "first"),
        symbol=("symbol", "first"),
        contract_month=("contract_month", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        open_interest=("open_interest", "last"),
    ).reset_index(drop=True)
    out["datetime"] = pd.to_datetime(out["datetime"]) + pd.Timedelta(hours=13, minutes=45)
    out["session"] = "full_session"
    return out[OUT_COLUMNS]


def _finalize_single_session(selected: pd.DataFrame) -> pd.DataFrame:
    out = selected.sort_values(["trade_date", "_session_order"]).copy()
    out["datetime"] = out["trade_date"]
    return out[[c for c in OUT_COLUMNS if c in out.columns]].reset_index(drop=True)


def build_session_continuous(df: pd.DataFrame, method: str = "stable_rollover",
                             n_confirm: int = 3, trigger: str = "volume",
                             exclude_weekly: bool = True,
                             price_mode: str = "unadjusted") -> tuple[pd.DataFrame, pd.DataFrame]:
    """建立連續契約並保留盤後／一般盤兩列。"""
    method = METHOD_ALIASES.get(method, method)
    if method not in VALID_METHODS:
        raise ValueError(f"method 需為 {VALID_METHODS}，收到 {method}")
    if price_mode != "unadjusted":
        raise NotImplementedError("adjusted 連續契約尚未實作")
    prep = _prepare(df, exclude_weekly=exclude_weekly,
                    monthly_only=(method == "stable_rollover"))
    selection, log = _select_contracts(prep, method, int(n_confirm), trigger)
    selected = _attach_selected_sessions(prep, selection)
    out = selected[[c for c in SESSION_OUT_COLUMNS if c in selected.columns]].reset_index(drop=True)
    return out, log


def build_continuous(df: pd.DataFrame, method: str = "stable_rollover",
                     n_confirm: int = 3, trigger: str = "volume",
                     exclude_weekly: bool = True,
                     price_mode: str = "unadjusted",
                     output_mode: str = "daily") -> tuple[pd.DataFrame, pd.DataFrame]:
    """建立連續契約。

    output_mode:
    - daily（預設）：同日有盤後與一般盤時合成完整交易日日 K；只有單一時段時保持原值。
    - session：保留盤後與一般盤兩列，供隨機 30/60 分 K 模擬。
    """
    sessions, log = build_session_continuous(
        df, method=method, n_confirm=n_confirm, trigger=trigger,
        exclude_weekly=exclude_weekly, price_mode=price_mode)
    if output_mode == "session":
        return sessions, log
    if output_mode != "daily":
        raise ValueError("output_mode 需為 daily 或 session")
    if sessions["trade_date"].duplicated().any():
        temp = sessions.copy()
        temp["_session_order"] = temp["session"].map({"after_hours": 0, "regular": 1}).fillna(1)
        return _aggregate_full_session(temp), log
    return _finalize_single_session(sessions.assign(_session_order=1)), log


def rollover_dates(cont: pd.DataFrame) -> pd.DataFrame:
    chg = cont["contract_month"] != cont["contract_month"].shift(1)
    cols = [c for c in ["datetime", "trade_date", "contract_month", "session"] if c in cont.columns]
    return cont.loc[chg, cols].reset_index(drop=True)
