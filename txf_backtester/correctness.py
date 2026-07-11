# -*- coding: utf-8 -*-
"""
correctness.py - 回測正確性檢查工具。

這個模組不改變交易邏輯，只在回測完成後檢查交易明細是否符合 v0.3 規則：
- 訊號日與實際進場日必須分離：signal_bar_index + 1 == entry_bar_index
- 進場價必須是下一根開盤價加/減滑價
- 出場原因、出場價必須能由進場後的 OHLC 與出場規則重算得到
- 損益點數與損益金額必須能重算吻合
- 移動停損只使用前一根以前的最高/最低，避免偷看當根高低點
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd

from backtester import CostModel


ROUND_TOL = 0.011
AMOUNT_TOL = 0.11


def _get_dt(df: pd.DataFrame, idx: int):
    return df.iloc[int(idx)]["datetime"]


def _direction_value(direction: str) -> int:
    return 1 if direction == "long" else -1


def _margin_call_line(entry_price: float, direction: int, cost: CostModel, p: Any = None) -> float | None:
    # 動態部位採帳戶權益/維持保證金模型；逐筆驗證另由交易金額與斷頭結果檢查，
    # 不套用舊版固定安全緩衝線。
    if p is not None and (getattr(p, "use_dynamic_position_sizing", False)
                          or getattr(p, "use_regime_position_sizing", False)
                          or getattr(p, "use_account_margin_model", False)):
        return None
    if not getattr(cost, "use_margin_call_check", False):
        return None
    if float(getattr(cost, "safety_buffer_amount", 0.0) or 0.0) <= 0:
        return None
    q = max(int(getattr(cost, "quantity", 1) or 1), 1)
    buffer_points = float(cost.safety_buffer_amount) / (float(cost.point_value) * q)
    return entry_price - direction * buffer_points


def _positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number) or number <= 0:
        return None
    return number


def _stop_threshold_mode(p) -> str:
    mode = str(getattr(p, "stop_threshold_mode", "points") or "points").lower()
    return "entry_atr" if mode in {"entry_atr", "atr", "atr_multiple", "normalized_atr"} else "points"


def _stop_distance_points(p, entry_atr) -> float | None:
    if _stop_threshold_mode(p) == "entry_atr":
        atr_value = _positive_float(entry_atr)
        multiple = _positive_float(getattr(p, "stop_atr_multiple", None))
        if atr_value is None or multiple is None:
            return None
        return atr_value * multiple
    return _positive_float(getattr(p, "stop_points", None))


def _favorable_points(entry_price: float, direction: int, row: pd.Series) -> float:
    if direction == 1:
        return max(0.0, float(row["high"]) - float(entry_price))
    return max(0.0, float(entry_price) - float(row["low"]))


def _profit_tier_mode(p) -> str:
    mode = str(getattr(p, "profit_tier_threshold_mode", "amount") or "amount").lower()
    return "entry_atr" if mode in {"entry_atr", "atr", "atr_multiple", "normalized_atr"} else "amount"


def _tier_reference_value(max_favorable_points: float, entry_price: float, direction: int,
                          row: pd.Series, cost: CostModel, p, entry_atr: float | None) -> float:
    ref = str(getattr(p, "profit_tier_reference", "current_unrealized") or "current_unrealized").lower()
    if ref in {"max_favorable", "max_floating_profit", "max_profit", "peak_profit"}:
        points = float(max_favorable_points)
    else:
        points = (float(row["close"]) - float(entry_price)) * int(direction)
    if _profit_tier_mode(p) == "entry_atr":
        return points / entry_atr if entry_atr else 0.0
    return points * float(cost.point_value) * int(cost.quantity)


def _select_profit_tier_mult(reference_value: float, thresholds_raw, mults, fallback: float) -> float:
    try:
        thresholds = [float(x) for x in (thresholds_raw or [])]
        values = [float(x) for x in (mults or [])]
    except Exception:
        return float(fallback)
    if not values or len(values) != len(thresholds) + 1:
        return float(fallback)
    tier = 0
    for th in thresholds:
        if float(reference_value) >= th:
            tier += 1
        else:
            break
    return values[min(tier, len(values) - 1)]


def _chandelier_suffix(mult: float) -> str:
    return str(float(mult)).replace(".", "_").replace("-", "m")


def _expected_exit(df: pd.DataFrame, entry_i: int, direction: str,
                   entry_price: float, cost: CostModel, p: Any) -> dict:
    """依 backtester 規則從 entry_i 開始重算第一個應該出場的位置。"""
    d = _direction_value(direction)
    highest = df.iloc[entry_i]["high"]
    lowest = df.iloc[entry_i]["low"]
    max_favorable_points = _favorable_points(entry_price, d, df.iloc[entry_i])
    # 下一根開盤進場，因此當時能確定的 ATR 只能來自訊號根（entry_i - 1）。
    entry_atr = _positive_float(df.iloc[entry_i - 1].get("atr")) if entry_i > 0 else None
    n = len(df)
    ch_count = 0
    macd_count = 0
    signal_count = 0

    for i in range(entry_i, n):
        row = df.iloc[i]
        exit_price, exit_reason = None, None
        max_favorable_points = max(max_favorable_points, _favorable_points(entry_price, d, row))
        margin_line = _margin_call_line(entry_price, d, cost, p)

        if margin_line is not None:
            if d == 1 and row["open"] <= margin_line:
                exit_price = row["open"]
                exit_reason = "margin_call"
            elif d == -1 and row["open"] >= margin_line:
                exit_price = row["open"]
                exit_reason = "margin_call"

        if exit_price is None and p.use_fixed_stop:
            stop_distance = _stop_distance_points(p, entry_atr)
            stop = entry_price - d * float(stop_distance or 0.0)
            if d == 1 and row["low"] <= stop:
                exit_price = min(row["open"], stop)
                exit_reason = "fixed_stop"
            elif d == -1 and row["high"] >= stop:
                exit_price = max(row["open"], stop)
                exit_reason = "fixed_stop"

        if exit_price is None and p.use_take_profit:
            tp = entry_price + d * p.take_profit_points
            if d == 1 and row["high"] >= tp:
                exit_price = max(row["open"], tp)
                exit_reason = "take_profit"
            elif d == -1 and row["low"] <= tp:
                exit_price = min(row["open"], tp)
                exit_reason = "take_profit"

        if exit_price is None and p.use_trailing_stop and i > entry_i:
            if d == 1:
                trail = highest - p.trailing_points
                if row["low"] <= trail:
                    exit_price = min(row["open"], trail)
                    exit_reason = "trailing_stop"
            else:
                trail = lowest + p.trailing_points
                if row["high"] >= trail:
                    exit_price = max(row["open"], trail)
                    exit_reason = "trailing_stop"

        if exit_price is None and margin_line is not None:
            if d == 1 and row["low"] <= margin_line:
                exit_price = margin_line
                exit_reason = "margin_call"
            elif d == -1 and row["high"] >= margin_line:
                exit_price = margin_line
                exit_reason = "margin_call"

        if exit_price is None and (p.use_chandelier or getattr(p, "use_profit_tier_chandelier", False)):
            exit_label = "chandelier"
            if getattr(p, "use_profit_tier_chandelier", False):
                reference_value = _tier_reference_value(
                    max_favorable_points, entry_price, d, row, cost, p, entry_atr)
                thresholds = (getattr(p, "profit_tier_atr_multiples", ())
                              if _profit_tier_mode(p) == "entry_atr"
                              else getattr(p, "profit_tier_amounts", ()))
                mult = _select_profit_tier_mult(
                    reference_value, thresholds, getattr(p, "profit_tier_mults", ()),
                    getattr(p, "chandelier_mult", 3.0))
                suffix = _chandelier_suffix(mult)
                long_col = f"chandelier_long_m_{suffix}"
                short_col = f"chandelier_short_m_{suffix}"
                exit_label = f"profit_tier_chandelier_{mult:g}"
            else:
                long_col = "chandelier_long"
                short_col = "chandelier_short"
            holding_now = i - entry_i + 1
            min_hold = max(int(getattr(p, "minimum_holding_bars", 0) or 0), 0)
            discretionary_ok = holding_now > min_hold
            if d == 1:
                ch = row.get(long_col)
                raw_ch = bool(pd.notna(ch) and row["close"] < ch)
            else:
                ch = row.get(short_col)
                raw_ch = bool(pd.notna(ch) and row["close"] > ch)
            ch_count = ch_count + 1 if raw_ch and discretionary_ok else 0
            if ch_count >= max(int(getattr(p, "chandelier_exit_confirmation_bars", 1) or 1), 1):
                exit_price, exit_reason = row["close"], exit_label

        holding_now = i - entry_i + 1
        min_hold = max(int(getattr(p, "minimum_holding_bars", 0) or 0), 0)
        discretionary_ok = holding_now > min_hold

        macd_reverse_blocked = False
        if exit_price is None and getattr(p, "use_profit_scaled_macd_exclusion", False):
            reference_value = _tier_reference_value(
                max_favorable_points, entry_price, d, row, cost, p, entry_atr)
            block_at = (float(getattr(p, "macd_reverse_exclude_atr_multiple", 0.0) or 0.0)
                        if _profit_tier_mode(p) == "entry_atr"
                        else float(getattr(p, "macd_reverse_exclude_profit_amount", 0.0) or 0.0))
            macd_reverse_blocked = block_at > 0 and reference_value >= block_at

        raw_macd = False
        if p.use_macd_reverse and (not macd_reverse_blocked) and "macd_hist" in df.columns:
            h = row["macd_hist"]
            raw_macd = bool(pd.notna(h) and ((d == 1 and h < 0) or (d == -1 and h > 0)))
        macd_count = macd_count + 1 if raw_macd and discretionary_ok else 0
        if exit_price is None and macd_count >= max(int(getattr(p, "macd_exit_confirmation_bars", 1) or 1), 1):
            exit_price, exit_reason = row["close"], "macd_reverse"

        raw_signal = False
        if getattr(p, "use_signal_exit", False):
            sig_col = "exit_long_signal" if d == 1 else "exit_short_signal"
            raw_signal = bool(sig_col in df.columns and row.get(sig_col, False))
        signal_count = signal_count + 1 if raw_signal and discretionary_ok else 0
        if exit_price is None and signal_count >= max(int(getattr(p, "signal_exit_confirmation_bars", 1) or 1), 1):
            exit_price, exit_reason = row["close"], "signal_exit"

        if exit_price is None and i == n - 1:
            exit_price, exit_reason = row["close"], "end_of_data"

        if exit_price is not None:
            executed_exit = exit_price - d * cost.slippage_points
            return {"exit_bar_index": i,
                    "exit_date": row["datetime"],
                    "exit_price": float(executed_exit),
                    "exit_reason": exit_reason}

        highest = max(highest, row["high"])
        lowest = min(lowest, row["low"])

    raise AssertionError("資料結束仍無法重算出場，這不應該發生")


def validate_trades(df: pd.DataFrame, trades: pd.DataFrame,
                    cost: CostModel, p: Any) -> pd.DataFrame:
    """回傳檢查表。status=OK 表示該項通過，FAIL 表示需人工檢查。"""
    rows: list[dict[str, Any]] = []
    required_trade_cols = [
        "signal_date", "signal_bar_index", "entry_execution_date", "entry_bar_index",
        "exit_date", "exit_bar_index", "direction", "entry_price", "exit_price",
        "pnl_points", "pnl_amount", "exit_reason", "entry_reason",
        "entry_atr", "planned_stop_points", "planned_stop_risk_amount",
        "entry_risk_cap_amount", "max_favorable_atr_multiple",
    ]
    for c in required_trade_cols:
        rows.append({"check": f"required_column:{c}",
                     "status": "OK" if c in trades.columns else "FAIL",
                     "details": "" if c in trades.columns else "missing"})
    if trades.empty or any(r["status"] == "FAIL" for r in rows):
        return pd.DataFrame(rows)

    for tno, t in trades.reset_index(drop=True).iterrows():
        prefix = f"trade_{tno + 1:04d}"
        sig_i = int(t["signal_bar_index"])
        ent_i = int(t["entry_bar_index"])
        exit_i = int(t["exit_bar_index"])
        d = _direction_value(t["direction"])

        def add(name: str, ok: bool, details: str = ""):
            rows.append({"check": f"{prefix}:{name}",
                         "status": "OK" if ok else "FAIL",
                         "details": details})

        add("entry_is_next_bar", ent_i == sig_i + 1,
            f"signal_i={sig_i}, entry_i={ent_i}")
        add("entry_after_signal_date", pd.to_datetime(t["entry_execution_date"]) > pd.to_datetime(t["signal_date"]),
            f"signal={t['signal_date']}, entry={t['entry_execution_date']}")
        add("exit_not_before_entry", exit_i >= ent_i,
            f"entry_i={ent_i}, exit_i={exit_i}")

        sig_col = "long_entry" if t["direction"] == "long" else "short_entry"
        sig_ok = sig_col in df.columns and bool(df.iloc[sig_i][sig_col])
        add("signal_column_true", sig_ok, f"{sig_col}@{sig_i}")
        add("entry_reason_present", isinstance(t["entry_reason"], str) and len(t["entry_reason"].strip()) > 0,
            str(t["entry_reason"])[:160])

        expected_entry = float(df.iloc[ent_i]["open"] + d * cost.slippage_points)
        add("entry_price", abs(float(t["entry_price"]) - expected_entry) < ROUND_TOL,
            f"actual={t['entry_price']}, expected={expected_entry}")
        add("entry_date_matches_index", pd.to_datetime(t["entry_execution_date"]) == pd.to_datetime(_get_dt(df, ent_i)),
            f"actual={t['entry_execution_date']}, index_dt={_get_dt(df, ent_i)}")
        expected_entry_atr = _positive_float(df.iloc[sig_i].get("atr"))
        actual_entry_atr = _positive_float(t.get("entry_atr"))
        add("entry_atr_uses_signal_bar",
            (expected_entry_atr is None and actual_entry_atr is None) or
            (expected_entry_atr is not None and actual_entry_atr is not None and
             abs(actual_entry_atr - expected_entry_atr) < ROUND_TOL),
            f"actual={actual_entry_atr}, expected_signal_bar_atr={expected_entry_atr}")

        expected_exit = _expected_exit(df, ent_i, t["direction"], float(t["entry_price"]), cost, p)
        add("exit_bar_index", exit_i == expected_exit["exit_bar_index"],
            f"actual={exit_i}, expected={expected_exit['exit_bar_index']}")
        add("exit_reason", t["exit_reason"] == expected_exit["exit_reason"],
            f"actual={t['exit_reason']}, expected={expected_exit['exit_reason']}")
        add("exit_price", abs(float(t["exit_price"]) - expected_exit["exit_price"]) < ROUND_TOL,
            f"actual={t['exit_price']}, expected={expected_exit['exit_price']}")

        expected_pts = (float(t["exit_price"]) - float(t["entry_price"])) * d
        add("pnl_points", abs(float(t["pnl_points"]) - expected_pts) < ROUND_TOL,
            f"actual={t['pnl_points']}, expected={expected_pts}")
        point_value_total = float(t.get("point_value_total", float(cost.point_value) * int(cost.quantity)))
        if "small_quantity" in t and "micro_quantity" in t:
            small_qty = int(t.get("small_quantity", 0) or 0)
            micro_qty = int(t.get("micro_quantity", 0) or 0)
            if str(t.get("position_sizing_mode", "fixed")) in {"dynamic_risk", "core_regime"}:
                fee_per_side = (small_qty * float(getattr(p, "position_small_fee", 20.0))
                                + micro_qty * float(getattr(p, "position_micro_fee", 12.0)))
            else:
                fee_per_side = float(cost.fee) * int(cost.quantity)
        else:
            fee_per_side = float(cost.fee) * int(cost.quantity)
        tax = (float(t["entry_price"]) + float(t["exit_price"])) * point_value_total * cost.tax_rate
        expected_amount = expected_pts * point_value_total - 2 * fee_per_side - tax
        amount_tol = max(AMOUNT_TOL, point_value_total * ROUND_TOL)
        add("pnl_amount", abs(float(t["pnl_amount"]) - round(expected_amount, 1)) < amount_tol,
            f"actual={t['pnl_amount']}, expected={round(expected_amount, 1)}, tol={round(amount_tol, 3)}")
        favorable = 0.0
        for j in range(ent_i, exit_i + 1):
            favorable = max(favorable, _favorable_points(float(t["entry_price"]), d, df.iloc[j]))
        expected_fav_atr = favorable / expected_entry_atr if expected_entry_atr else None
        try:
            actual_fav_atr = float(t.get("max_favorable_atr_multiple"))
            if pd.isna(actual_fav_atr):
                actual_fav_atr = None
        except (TypeError, ValueError):
            actual_fav_atr = None
        add("max_favorable_atr_multiple",
            (expected_fav_atr is None and actual_fav_atr is None) or
            (expected_fav_atr is not None and actual_fav_atr is not None and
             abs(actual_fav_atr - expected_fav_atr) < ROUND_TOL),
            f"actual={actual_fav_atr}, expected={expected_fav_atr}")

    return pd.DataFrame(rows)


def summarize_validation(checks: pd.DataFrame) -> dict:
    total = len(checks)
    fail = int((checks["status"] == "FAIL").sum()) if total else 0
    return {"total_checks": int(total), "failed_checks": fail,
            "passed_checks": int(total - fail), "status": "PASS" if fail == 0 else "FAIL"}


def checks_to_markdown(checks: pd.DataFrame, title: str = "回測正確性檢查報告") -> str:
    summary = summarize_validation(checks)
    lines = [f"# {title}", "",
             f"- 總檢查項目：{summary['total_checks']}",
             f"- 通過：{summary['passed_checks']}",
             f"- 失敗：{summary['failed_checks']}",
             f"- 結論：{summary['status']}", ""]
    fails = checks[checks["status"] == "FAIL"]
    if fails.empty:
        lines.append("全部檢查通過。")
    else:
        lines.append("## 失敗項目")
        lines.append("")
        for _, r in fails.iterrows():
            lines.append(f"- {r['check']}：{r['details']}")
    return "\n".join(lines) + "\n"


def strategy_params_snapshot(p: Any) -> dict:
    """轉出 StrategyParams 內容，供報告留存。"""
    try:
        return asdict(p)
    except TypeError:
        return dict(vars(p))
