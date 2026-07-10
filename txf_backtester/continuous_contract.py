# -*- coding: utf-8 -*-
"""
continuous_contract.py - 建立近月連續契約（三種模式）。

模式：
A. volume_max_daily : 每日成交量最大（簡單模式，MVP 保留）
B. oi_max_daily     : 每日未沖銷契約數最大
C. stable_rollover  : 穩定換倉（預設）
   1. 只用月契約（排除週契約，到期月份需為 6 位數字）
   2. 契約月份只能往後換，不會跳回近月
   3. 「下一個月契約」的 trigger 欄位（volume 或 open_interest）
      連續 n_confirm 天大於目前契約時，才換倉（預設 3 天）
   4. 目前契約到期消失時強制換倉
   5. 換倉記錄輸出 rollover_log（可存成 rollover_log.csv）

價格調整：
- price_mode="unadjusted"（預設）：不做換倉價差回溯調整
- price_mode="adjusted"：設計位置已保留（_apply_adjustment），第一版未實作

回傳：(continuous_df, rollover_log_df)
rollover_log 欄位：rollover_date, old_contract, new_contract, reason,
                   old_volume, new_volume, old_open_interest, new_open_interest
"""
import pandas as pd

VALID_METHODS = ("volume_max_daily", "oi_max_daily", "stable_rollover")
# 舊版名稱相容（v1 用 'volume' / 'open_interest'）
METHOD_ALIASES = {"volume": "volume_max_daily", "open_interest": "oi_max_daily"}

LOG_COLUMNS = ["rollover_date", "old_contract", "new_contract", "reason",
               "old_volume", "new_volume", "old_open_interest", "new_open_interest"]

OUT_COLUMNS = ["datetime", "symbol", "contract_month", "session",
               "open", "high", "low", "close", "volume", "open_interest"]


def _prepare(df: pd.DataFrame, exclude_weekly: bool,
             monthly_only: bool) -> pd.DataFrame:
    """共用前處理：排除週契約、同(日,月份)多時段時取成交量大者。"""
    df = df.copy()
    if monthly_only:
        # 月契約 = 到期月份為 6 位數字（如 202501）；週契約如 202501W1 一律排除
        df = df[df["contract_month"].str.fullmatch(r"\d{6}", na=False)]
    elif exclude_weekly:
        df = df[~df["contract_month"].str.upper().str.contains("W", na=False)]
    if df.empty:
        raise ValueError("排除週契約後沒有資料")
    # session='all' 時同一(日, 月份)會有一般盤+盤後盤兩列，取成交量大者代表
    idx = df.groupby(["date", "contract_month"])["volume"].idxmax()
    return df.loc[idx]


def _finalize(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.sort_values("date").reset_index(drop=True)
    out = out.rename(columns={"date": "datetime"})
    return out[[c for c in OUT_COLUMNS if c in out.columns]]


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


def _daily_max(df: pd.DataFrame, field: str) -> tuple:
    """A/B 模式：每日取 field 最大的契約。"""
    pick = df.groupby("date")[field].idxmax()
    cont = df.loc[pick].sort_values("date").reset_index(drop=True)
    # 從契約切換日回推 rollover_log
    logs = []
    prev = None
    for _, row in cont.iterrows():
        if prev is not None and row["contract_month"] != prev["contract_month"]:
            logs.append(_log_row(row["date"], prev, row, f"daily_max({field})"))
        prev = row
    return _finalize(cont), pd.DataFrame(logs, columns=LOG_COLUMNS)


def _stable_rollover(df: pd.DataFrame, n_confirm: int, trigger: str) -> tuple:
    """C 模式：穩定換倉。"""
    if trigger not in ("volume", "open_interest"):
        raise ValueError("trigger 必須是 'volume' 或 'open_interest'")
    groups = {dt: day.set_index("contract_month", drop=False)
              for dt, day in df.groupby("date")}
    dates = sorted(groups.keys())

    rows, logs = [], []
    current = None   # 目前契約月份 (str，如 '202501')
    streak = 0       # 下一契約 trigger 連續大於目前契約的天數

    for dt in dates:
        day = groups[dt]
        months = sorted(day["contract_month"])

        if current is None:
            # 初始：取當日最近月
            current = months[0]
            logs.append(_log_row(dt, None, day.loc[current], "initial"))
            streak = 0
        elif current not in months:
            # 目前契約已到期下市 -> 強制換到「往後」最近的月份（不回頭）
            later = [m for m in months if m > current]
            new = later[0] if later else months[-1]
            logs.append(_log_row(dt, None, day.loc[new], "expired"))
            current, streak = new, 0
        else:
            # 找下一個月契約，比較 trigger 欄位
            later = [m for m in months if m > current]
            nxt = later[0] if later else None
            if nxt is not None and day.loc[nxt, trigger] > day.loc[current, trigger]:
                streak += 1
            else:
                streak = 0
            if nxt is not None and streak >= n_confirm:
                logs.append(_log_row(
                    dt, day.loc[current], day.loc[nxt],
                    f"{trigger}>current x{n_confirm}d"))
                current, streak = nxt, 0

        rows.append(day.loc[current])

    cont = pd.DataFrame(rows)
    return _finalize(cont), pd.DataFrame(logs, columns=LOG_COLUMNS)


def _apply_adjustment(cont: pd.DataFrame, log: pd.DataFrame) -> pd.DataFrame:
    """
    價格回溯調整的設計位置（第一版未實作）。
    未來做法：對每個換倉日計算新舊契約價差，往回累加調整 open/high/low/close。
    """
    raise NotImplementedError(
        "adjusted 連續契約尚未實作，請使用 price_mode='unadjusted'")


def build_continuous(df: pd.DataFrame, method: str = "stable_rollover",
                     n_confirm: int = 3, trigger: str = "volume",
                     exclude_weekly: bool = True,
                     price_mode: str = "unadjusted") -> tuple:
    """
    建立連續契約。輸入 clean_data() 的結果。
    回傳 (continuous_df, rollover_log_df)。
    """
    method = METHOD_ALIASES.get(method, method)
    if method not in VALID_METHODS:
        raise ValueError(f"method 需為 {VALID_METHODS}，收到 {method}")
    if price_mode not in ("unadjusted", "adjusted"):
        raise ValueError("price_mode 需為 'unadjusted' 或 'adjusted'")

    # stable_rollover 一律只用月契約；A/B 模式依 exclude_weekly 參數
    prep = _prepare(df, exclude_weekly=exclude_weekly,
                    monthly_only=(method == "stable_rollover"))

    if method == "volume_max_daily":
        cont, log = _daily_max(prep, "volume")
    elif method == "oi_max_daily":
        cont, log = _daily_max(prep, "open_interest")
    else:
        cont, log = _stable_rollover(prep, n_confirm=int(n_confirm),
                                     trigger=trigger)

    if price_mode == "adjusted":
        cont = _apply_adjustment(cont, log)
    return cont, log


def rollover_dates(cont: pd.DataFrame) -> pd.DataFrame:
    """列出連續契約中契約月份切換的日期，供快速檢查。"""
    chg = cont["contract_month"] != cont["contract_month"].shift(1)
    return cont.loc[chg, ["datetime", "contract_month"]].reset_index(drop=True)
