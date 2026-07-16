# -*- coding: utf-8 -*-
"""v0.8.8.0：50萬元滾動起點敏感度回測。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from backtester import CostModel, run_backtest
from benchmark_00631l import historical_buy_hold_curve
from metrics import compute_metrics, yearly_stats
from multi_timeframe import prepare_execution_frame


@dataclass(frozen=True)
class StartSpec:
    date: pd.Timestamp
    frame_index: int
    regime: str
    features: dict


def _date_series(frame: pd.DataFrame) -> pd.Series:
    if "trade_date" in frame.columns:
        return pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    return pd.to_datetime(frame["datetime"], errors="coerce").dt.normalize()


def _true_range(frame: pd.DataFrame) -> pd.Series:
    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    prev = close.shift(1)
    return pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def _rolling_last_percentile(series: pd.Series, lookback: int = 252) -> pd.Series:
    def rank_last(values):
        arr = np.asarray(values, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) == 0:
            return np.nan
        return float((arr <= arr[-1]).mean() * 100.0)
    return series.rolling(lookback, min_periods=min(60, lookback)).apply(rank_last, raw=True)


def _feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    close = pd.to_numeric(frame["close"], errors="coerce")
    out["close"] = close
    out["sma240"] = close.rolling(240, min_periods=240).mean()
    out["close_vs_sma240_pct"] = (close / out["sma240"] - 1.0) * 100.0
    out["sma240_slope_20_pct"] = (out["sma240"] / out["sma240"].shift(20) - 1.0) * 100.0
    atr14 = _true_range(frame).rolling(14, min_periods=14).mean()
    out["atr14_percentile_252"] = _rolling_last_percentile(atr14, 252)
    high252 = close.rolling(252, min_periods=60).max()
    out["drawdown_from_252_high_pct"] = (close / high252 - 1.0) * 100.0
    return out


def _match(rule: dict, features: dict) -> bool:
    value = features.get(str(rule.get("feature")))
    if value is None or pd.isna(value):
        return False
    target = float(rule.get("value", 0.0))
    op = str(rule.get("op", "=="))
    value = float(value)
    return {
        ">": value > target,
        ">=": value >= target,
        "<": value < target,
        "<=": value <= target,
        "==": value == target,
        "!=": value != target,
    }.get(op, False)


def _classify(features: dict, rules: list[dict]) -> str:
    for item in rules:
        if item.get("default"):
            return str(item.get("label") or "未分類")
        all_ok = all(_match(x, features) for x in (item.get("all") or []))
        any_rules = item.get("any") or []
        any_ok = True if not any_rules else any(_match(x, features) for x in any_rules)
        if all_ok and any_ok:
            return str(item.get("label") or "未分類")
    return "未分類"


def _monthly_starts(frame: pd.DataFrame, cfg: dict) -> list[StartSpec]:
    dates = _date_series(frame)
    feature_data = _feature_frame(frame)
    start_cfg = cfg.get("start_generation") or {}
    start_from = pd.Timestamp(start_cfg.get("start_from")).normalize()
    start_to = pd.Timestamp(start_cfg.get("start_to")).normalize()
    warmup = max(int(start_cfg.get("warmup_bars", 300) or 300), 0)
    rules = (cfg.get("start_regime_classifier") or {}).get("classification_order") or []

    candidates = pd.DataFrame({"date": dates, "idx": frame.index}).dropna().drop_duplicates("date")
    candidates = candidates[(candidates["date"] >= start_from) & (candidates["date"] <= start_to)]
    if candidates.empty:
        return []
    candidates["month"] = candidates["date"].dt.to_period("M")
    first = candidates.groupby("month", as_index=False).first()
    out = []
    for _, row in first.iterrows():
        idx = int(row["idx"])
        if idx < max(warmup, 1):
            continue
        feature_idx = idx - 1
        features = {
            col: (None if pd.isna(feature_data.loc[feature_idx, col]) else float(feature_data.loc[feature_idx, col]))
            for col in feature_data.columns
        }
        if any(features.get(x) is None for x in ("sma240", "sma240_slope_20_pct", "atr14_percentile_252")):
            continue
        out.append(StartSpec(
            date=pd.Timestamp(row["date"]).normalize(), frame_index=idx,
            regime=_classify(features, rules), features=features,
        ))
    return out


def _max_drawdown(account: pd.Series) -> tuple[float, float]:
    s = pd.to_numeric(account, errors="coerce").dropna()
    if s.empty:
        return 0.0, 0.0
    peak = s.cummax()
    dd_amt = s - peak
    dd_pct = (s / peak.replace(0, np.nan) - 1.0) * 100.0
    return float(dd_amt.min()), float(dd_pct.min())


def _recovery_days(account: pd.Series, initial: float) -> tuple[float | None, bool]:
    s = pd.to_numeric(account, errors="coerce").reset_index(drop=True)
    below = s < float(initial) - 1e-9
    if not below.any():
        return 0.0, False
    first_below = int(np.flatnonzero(below.to_numpy())[0])
    recovered = np.flatnonzero((s.iloc[first_below + 1:] >= float(initial)).to_numpy())
    if len(recovered) == 0:
        return None, True
    return float(first_below + 1 + int(recovered[0])), False


def _benchmark_result(benchmark_df: pd.DataFrame | None, start: pd.Timestamp, end: pd.Timestamp,
                      initial_capital: float, fee_rate: float) -> dict:
    """同日起點50萬元盡量全押00631L並持有至觀察期末。

    以起點當日或其後第一個可交易日的收盤價買進整數股；未能買進零股的
    剩餘現金留在帳戶。這個口徑與平台既有00631L基準一致。
    """
    empty = {
        "terminal": None, "return_pct": None, "annualized_pct": None,
        "minimum_value": None, "max_drawdown_pct": None,
        "buy_date": None, "buy_price": None, "buy_shares": None,
        "buy_fee": None, "cash": None, "end_date": None,
        "end_price": None, "end_shares": None,
    }
    if benchmark_df is None or benchmark_df.empty:
        return empty
    frame = benchmark_df.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    part = frame[(frame["date"] >= pd.Timestamp(start).normalize()) &
                 (frame["date"] <= pd.Timestamp(end).normalize())]
    if part.empty:
        return empty
    curve = historical_buy_hold_curve(part, initial_capital, fee_rate)
    if curve.empty:
        return empty
    terminal = float(curve["account_value"].iloc[-1])
    buy_date = pd.Timestamp(curve["date"].iloc[0]).normalize()
    end_date = pd.Timestamp(curve["date"].iloc[-1]).normalize()
    days = max((end_date - buy_date).days, 1)
    ratio = terminal / float(initial_capital)
    annualized = (ratio ** (365.25 / days) - 1.0) * 100.0 if ratio > 0 else np.nan
    buy_price = float(curve["raw_close"].iloc[0])
    buy_shares = int(curve["shares"].iloc[0])
    cash = float(curve["cash"].iloc[0])
    buy_fee = float(initial_capital) - buy_shares * buy_price - cash
    return {
        "terminal": terminal,
        "return_pct": (ratio - 1.0) * 100.0,
        "annualized_pct": float(annualized),
        "minimum_value": float(curve["account_value"].min()),
        "max_drawdown_pct": float(curve["drawdown_pct"].min()),
        "buy_date": buy_date,
        "buy_price": buy_price,
        "buy_shares": buy_shares,
        "buy_fee": buy_fee,
        "cash": cash,
        "end_date": end_date,
        "end_price": float(curve["raw_close"].iloc[-1]),
        "end_shares": int(curve["shares"].iloc[-1]),
    }


def _result_row(name: str, start: StartSpec, horizon: dict, horizon_frame: pd.DataFrame,
                trades: pd.DataFrame, equity: pd.DataFrame, metrics: dict,
                initial_capital: float, benchmark_df: pd.DataFrame | None,
                benchmark_fee_rate: float) -> dict:
    account = pd.to_numeric(equity.get("account_equity"), errors="coerce")
    terminal = float(account.iloc[-1]) if len(account) else float(initial_capital)
    min_equity = float(account.min()) if len(account) else float(initial_capital)
    dd_amount, dd_pct = _max_drawdown(account)
    margin_trades = trades[trades.get("exit_reason", pd.Series(dtype=str)).astype(str) == "margin_call"] if not trades.empty else trades
    margin_call = bool(len(margin_trades))
    liquidation_date = ""
    before_liq = after_liq = None
    if margin_call:
        liquidation_ts = pd.to_datetime(margin_trades["exit_date"], errors="coerce").min()
        liquidation_date = str(liquidation_ts.date()) if pd.notna(liquidation_ts) else ""
        eq_dates = pd.to_datetime(equity["datetime"], errors="coerce")
        matches = equity.loc[eq_dates == liquidation_ts, "account_equity"]
        after_liq = float(matches.iloc[-1]) if len(matches) else terminal
        before_matches = equity.loc[eq_dates < liquidation_ts, "account_equity"]
        before_liq = float(before_matches.iloc[-1]) if len(before_matches) else float(initial_capital)
    bankruptcy = bool((account <= 0).any()) if len(account) else False
    first_entry_wait = None
    if not trades.empty:
        entry = pd.to_datetime(trades["entry_date"], errors="coerce").min().normalize()
        dates = _date_series(horizon_frame)
        locs = np.flatnonzero((dates >= entry).to_numpy())
        first_entry_wait = int(locs[0]) if len(locs) else None
    recovery, never_recovered = _recovery_days(account, initial_capital)
    end_date = _date_series(horizon_frame).iloc[-1]
    benchmark = _benchmark_result(
        benchmark_df, start.date, end_date, initial_capital, benchmark_fee_rate)
    benchmark_terminal = benchmark.get("terminal")
    benchmark_return = benchmark.get("return_pct")
    forced_pnl = float(metrics.get("期末強制平倉損益(元)", 0.0) or 0.0)
    natural_pnl = float(metrics.get("扣除期末強制平倉後損益(元)", 0.0) or 0.0)
    row = {
        "策略名稱": name,
        "啟動日": str(start.date.date()),
        "觀察期限": str(horizon.get("label")),
        "觀察交易日": int(len(horizon_frame)),
        "期末日": str(pd.Timestamp(end_date).date()),
        "起點市場狀態": start.regime,
        "起始資產(元)": round(initial_capital, 0),
        "期末資產(元)": round(terminal, 0),
        "期末報酬率(%)": round((terminal / initial_capital - 1.0) * 100.0, 2),
        "年化報酬率(%)": metrics.get("年化報酬率(%)"),
        "最低帳戶權益(元)": round(min_equity, 0),
        "最大回撤(元)": round(dd_amount, 0),
        "最大回撤率(%)": round(dd_pct, 2),
        "交易次數": int(metrics.get("交易次數", 0) or 0),
        "勝率(%)": float(metrics.get("勝率(%)", 0.0) or 0.0),
        "平均持有K棒": float(metrics.get("平均持倉K棒數", 0.0) or 0.0),
        "第一筆交易等待交易日": first_entry_wait,
        "回到50萬元所需交易日": recovery,
        "觀察期內未恢復50萬元": bool(never_recovered),
        "維持保證金強制平倉": margin_call,
        "斷頭日期": liquidation_date,
        "斷頭前帳戶權益(元)": None if before_liq is None else round(before_liq, 0),
        "斷頭後剩餘資產(元)": None if after_liq is None else round(after_liq, 0),
        "帳戶權益曾小於等於0": bankruptcy,
        "期末強制平倉損益(元)": round(forced_pnl, 0),
        "扣除期末強制平倉後損益(元)": round(natural_pnl, 0),
        "同期00631L實際買進日": "" if benchmark.get("buy_date") is None else str(pd.Timestamp(benchmark["buy_date"]).date()),
        "同期00631L買進收盤價": None if benchmark.get("buy_price") is None else round(float(benchmark["buy_price"]), 4),
        "同期00631L買進股數": benchmark.get("buy_shares"),
        "同期00631L買進手續費(元)": None if benchmark.get("buy_fee") is None else round(float(benchmark["buy_fee"]), 0),
        "同期00631L起始剩餘現金(元)": None if benchmark.get("cash") is None else round(float(benchmark["cash"]), 0),
        "同期00631L期末日": "" if benchmark.get("end_date") is None else str(pd.Timestamp(benchmark["end_date"]).date()),
        "同期00631L期末收盤價": None if benchmark.get("end_price") is None else round(float(benchmark["end_price"]), 4),
        "同期00631L期末股數": benchmark.get("end_shares"),
        "同期00631L期末資產(元)": None if benchmark_terminal is None else round(benchmark_terminal, 0),
        "同期00631L報酬率(%)": None if benchmark_return is None else round(benchmark_return, 2),
        "同期00631L年化報酬率(%)": None if benchmark.get("annualized_pct") is None else round(float(benchmark["annualized_pct"]), 2),
        "同期00631L最低資產(元)": None if benchmark.get("minimum_value") is None else round(float(benchmark["minimum_value"]), 0),
        "同期00631L最大回撤率(%)": None if benchmark.get("max_drawdown_pct") is None else round(float(benchmark["max_drawdown_pct"]), 2),
        "策略期末資產減00631L(元)": None if benchmark_terminal is None else round(terminal - benchmark_terminal, 0),
        "策略最大回撤相對00631L改善(百分點)": None if benchmark.get("max_drawdown_pct") is None else round(dd_pct - float(benchmark["max_drawdown_pct"]), 2),
        "期末資產超越00631L": None if benchmark_terminal is None else bool(terminal > benchmark_terminal),
        "資產與回撤同時優於00631L": None if benchmark_terminal is None or benchmark.get("max_drawdown_pct") is None else bool(terminal > benchmark_terminal and dd_pct > float(benchmark["max_drawdown_pct"])),
        **{k: (None if v is None else round(v, 4)) for k, v in start.features.items()},
    }
    return row


def _q(series, pct):
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.quantile(pct)) if len(s) else np.nan


def _summary_rows(detail: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    rows = []
    for keys, grp in detail.groupby(groups, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(groups, keys))
        terminal = pd.to_numeric(grp["期末資產(元)"], errors="coerce")
        min_eq = pd.to_numeric(grp["最低帳戶權益(元)"], errors="coerce")
        dd = pd.to_numeric(grp["最大回撤率(%)"], errors="coerce")
        row.update({
            "起點數": int(len(grp)),
            "期末資產P10": round(_q(terminal, .10), 0),
            "期末資產P25": round(_q(terminal, .25), 0),
            "期末資產P50": round(_q(terminal, .50), 0),
            "期末資產P75": round(_q(terminal, .75), 0),
            "期末資產P90": round(_q(terminal, .90), 0),
            "最低期末資產": round(float(terminal.min()), 0),
            "最高期末資產": round(float(terminal.max()), 0),
            "最低權益P10": round(_q(min_eq, .10), 0),
            "最低權益P50": round(_q(min_eq, .50), 0),
            "歷史最低權益": round(float(min_eq.min()), 0),
            "最大回撤率P50": round(_q(dd, .50), 2),
            "最大回撤率P75": round(_q(dd, .25), 2),
            "最大回撤率P90": round(_q(dd, .10), 2),
            "最差最大回撤率": round(float(dd.min()), 2),
            "斷頭比例(%)": round(float(grp["維持保證金強制平倉"].fillna(False).mean() * 100), 2),
            "歸零比例(%)": round(float(grp["帳戶權益曾小於等於0"].fillna(False).mean() * 100), 2),
            "未恢復50萬元比例(%)": round(float(grp["觀察期內未恢復50萬元"].fillna(False).mean() * 100), 2),
            "獲利起點比例(%)": round(float((terminal > pd.to_numeric(grp["起始資產(元)"], errors="coerce")).mean() * 100), 2),
            "年化報酬率P50(%)": round(_q(grp["年化報酬率(%)"], .50), 2),
            "最大回撤金額P50": round(_q(grp["最大回撤(元)"], .50), 0),
            "勝過00631L比例(%)": round(float(grp["期末資產超越00631L"].dropna().mean() * 100), 2)
                if grp["期末資產超越00631L"].notna().any() else np.nan,
            "交易次數P50": round(_q(grp["交易次數"], .50), 1),
            "第一筆交易等待P50": round(_q(grp["第一筆交易等待交易日"], .50), 1),
            "第一筆交易等待P75": round(_q(grp["第一筆交易等待交易日"], .75), 1),
            "第一筆交易等待P90": round(_q(grp["第一筆交易等待交易日"], .90), 1),
            "回到50萬日數P50": round(_q(grp["回到50萬元所需交易日"], .50), 1),
            "回到50萬日數P75": round(_q(grp["回到50萬元所需交易日"], .75), 1),
            "回到50萬日數P90": round(_q(grp["回到50萬元所需交易日"], .90), 1),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def _threshold_table(detail: pd.DataFrame, thresholds: list[float]) -> pd.DataFrame:
    rows = []
    for (name, horizon, regime), grp in detail.groupby(["策略名稱", "觀察期限", "起點市場狀態"], dropna=False):
        terminal = pd.to_numeric(grp["期末資產(元)"], errors="coerce")
        row = {"策略名稱": name, "觀察期限": horizon, "起點市場狀態": regime, "起點數": len(grp)}
        for threshold in thresholds:
            label = f"期末低於{int(threshold/10000)}萬元比例(%)" if threshold > 0 else "期末小於等於0比例(%)"
            row[label] = round(float(((terminal < threshold) if threshold > 0 else (terminal <= 0)).mean() * 100), 2)
        for threshold in (750000, 1000000, 1500000):
            row[f"期末高於等於{int(threshold/10000)}萬元比例(%)"] = round(float((terminal >= threshold).mean() * 100), 2)
        rows.append(row)
    return pd.DataFrame(rows)


def _benchmark_tables(detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """產生00631L全押逐起點、分組彙總，以及策略對正二金額比較。"""
    benchmark_cols = [
        "啟動日", "觀察期限", "觀察交易日", "期末日", "起點市場狀態",
        "起始資產(元)", "同期00631L實際買進日", "同期00631L買進收盤價",
        "同期00631L買進股數", "同期00631L買進手續費(元)",
        "同期00631L起始剩餘現金(元)", "同期00631L期末日",
        "同期00631L期末收盤價", "同期00631L期末股數",
        "同期00631L期末資產(元)", "同期00631L報酬率(%)",
        "同期00631L年化報酬率(%)", "同期00631L最低資產(元)",
        "同期00631L最大回撤率(%)",
        "close", "sma240", "close_vs_sma240_pct", "sma240_slope_20_pct",
        "atr14_percentile_252", "drawdown_from_252_high_pct",
    ]
    available = [c for c in benchmark_cols if c in detail.columns]
    benchmark_detail = detail[available].drop_duplicates(["啟動日", "觀察期限"]).copy()
    benchmark_detail = benchmark_detail[benchmark_detail["同期00631L期末資產(元)"].notna()].reset_index(drop=True)

    summary_rows = []
    for (horizon, regime), grp in benchmark_detail.groupby(["觀察期限", "起點市場狀態"], dropna=False):
        terminal = pd.to_numeric(grp["同期00631L期末資產(元)"], errors="coerce")
        min_asset = pd.to_numeric(grp["同期00631L最低資產(元)"], errors="coerce")
        dd = pd.to_numeric(grp["同期00631L最大回撤率(%)"], errors="coerce")
        annual = pd.to_numeric(grp["同期00631L年化報酬率(%)"], errors="coerce")
        row = {
            "觀察期限": horizon, "起點市場狀態": regime, "起點數": int(len(grp)),
            "正二期末資產P10": round(_q(terminal, .10), 0),
            "正二期末資產P25": round(_q(terminal, .25), 0),
            "正二期末資產P50": round(_q(terminal, .50), 0),
            "正二期末資產P75": round(_q(terminal, .75), 0),
            "正二期末資產P90": round(_q(terminal, .90), 0),
            "正二最低期末資產": round(float(terminal.min()), 0),
            "正二最高期末資產": round(float(terminal.max()), 0),
            "正二最低持有資產P10": round(_q(min_asset, .10), 0),
            "正二最低持有資產P50": round(_q(min_asset, .50), 0),
            "正二歷史最低持有資產": round(float(min_asset.min()), 0),
            "正二最大回撤率P50": round(_q(dd, .50), 2),
            "正二最差最大回撤率": round(float(dd.min()), 2),
            "正二年化報酬率P50(%)": round(_q(annual, .50), 2),
        }
        for threshold in (100000, 250000, 300000, 400000, 500000):
            row[f"正二期末低於{int(threshold/10000)}萬元比例(%)"] = round(float((terminal < threshold).mean() * 100), 2)
        for threshold in (750000, 1000000, 1500000):
            row[f"正二期末高於等於{int(threshold/10000)}萬元比例(%)"] = round(float((terminal >= threshold).mean() * 100), 2)
        summary_rows.append(row)
    benchmark_summary = pd.DataFrame(summary_rows)

    compare_rows = []
    for (name, horizon, regime), grp in detail.groupby(["策略名稱", "觀察期限", "起點市場狀態"], dropna=False):
        strategy_asset = pd.to_numeric(grp["期末資產(元)"], errors="coerce")
        benchmark_asset = pd.to_numeric(grp["同期00631L期末資產(元)"], errors="coerce")
        diff = strategy_asset - benchmark_asset
        strategy_dd = pd.to_numeric(grp["最大回撤率(%)"], errors="coerce")
        benchmark_dd = pd.to_numeric(grp["同期00631L最大回撤率(%)"], errors="coerce")
        dd_improvement = strategy_dd - benchmark_dd
        valid = benchmark_asset.notna()
        compare_rows.append({
            "策略名稱": name, "觀察期限": horizon, "起點市場狀態": regime,
            "共同起點數": int(valid.sum()),
            "策略期末資產P50": round(_q(strategy_asset[valid], .50), 0),
            "正二期末資產P50": round(_q(benchmark_asset[valid], .50), 0),
            "策略減正二資產差P10": round(_q(diff[valid], .10), 0),
            "策略減正二資產差P50": round(_q(diff[valid], .50), 0),
            "策略減正二資產差P90": round(_q(diff[valid], .90), 0),
            "策略資產勝正二比例(%)": round(float((diff[valid] > 0).mean() * 100), 2) if valid.any() else np.nan,
            "策略回撤小於正二比例(%)": round(float((dd_improvement[valid] > 0).mean() * 100), 2) if valid.any() else np.nan,
            "策略資產與回撤雙勝比例(%)": round(float(((diff[valid] > 0) & (dd_improvement[valid] > 0)).mean() * 100), 2) if valid.any() else np.nan,
        })
    return benchmark_detail, benchmark_summary, pd.DataFrame(compare_rows)


def _paired_comparison(detail: pd.DataFrame) -> pd.DataFrame:
    names = detail["策略名稱"].drop_duplicates().tolist()
    if len(names) < 2:
        return pd.DataFrame()
    pivot_cols = ["啟動日", "觀察期限"]
    left, right = names[0], names[1]
    a = detail[detail["策略名稱"] == left].set_index(pivot_cols)
    b = detail[detail["策略名稱"] == right].set_index(pivot_cols)
    common = a.index.intersection(b.index)
    rows = []
    for horizon in detail["觀察期限"].drop_duplicates():
        idx = [x for x in common if x[1] == horizon]
        if not idx:
            continue
        aa, bb = a.loc[idx], b.loc[idx]
        asset_diff = pd.to_numeric(aa["期末資產(元)"], errors="coerce") - pd.to_numeric(bb["期末資產(元)"], errors="coerce")
        dd_diff = pd.to_numeric(aa["最大回撤率(%)"], errors="coerce") - pd.to_numeric(bb["最大回撤率(%)"], errors="coerce")
        rows.append({
            "觀察期限": horizon,
            "左策略": left,
            "右策略": right,
            "共同起點數": len(idx),
            "左策略資產較高": int((asset_diff > 0).sum()),
            "左策略資產較低": int((asset_diff < 0).sum()),
            "資產相同": int((asset_diff == 0).sum()),
            "左策略回撤較小": int((dd_diff > 0).sum()),
            "左策略回撤較大": int((dd_diff < 0).sum()),
            "回撤相同": int((dd_diff == 0).sum()),
            "左策略資產與回撤雙勝": int(((asset_diff > 0) & (dd_diff > 0)).sum()),
            "左策略斷頭但右策略未斷頭": int((aa["維持保證金強制平倉"].astype(bool) & ~bb["維持保證金強制平倉"].astype(bool)).sum()),
            "右策略斷頭但左策略未斷頭": int((bb["維持保證金強制平倉"].astype(bool) & ~aa["維持保證金強制平倉"].astype(bool)).sum()),
            "資產差P10": round(_q(asset_diff, .10), 0),
            "資產差P50": round(_q(asset_diff, .50), 0),
            "資產差P90": round(_q(asset_diff, .90), 0),
        })
    return pd.DataFrame(rows)


def _standard_comparison(summary: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    first_horizon = summary["觀察期限"].drop_duplicates().iloc[0] if not summary.empty else ""
    preferred = summary[summary["觀察期限"] == first_horizon].copy()
    rows = []
    for _, r in preferred.iterrows():
        rows.append({
            "策略名稱": r["策略名稱"],
            "路徑數": int(r["起點數"]),
            "獲利路徑比例(%)": r.get("獲利起點比例(%)", np.nan),
            "總損益中位數": round(float(r["期末資產P50"] - initial_capital), 0),
            "總損益P25": round(float(r["期末資產P25"] - initial_capital), 0),
            "總損益P75": round(float(r["期末資產P75"] - initial_capital), 0),
            "總損益P10": round(float(r["期末資產P10"] - initial_capital), 0),
            "報酬回撤比中位數": np.nan,
            "最大回撤中位數": r.get("最大回撤金額P50", np.nan),
            "最差路徑最大回撤": np.nan,
            "年化報酬率中位數(%)": r.get("年化報酬率P50(%)", np.nan),
            "獲利因子中位數": np.nan,
            "交易次數中位數": r["交易次數P50"],
            "期末強制平倉損益中位數": np.nan,
            "扣除期末強制平倉後損益中位數": np.nan,
            "最大回撤率中位數(%)": r["最大回撤率P50"],
            "最大有效槓桿中位數(倍)": np.nan,
            "歷史最低運作資金中位數": r["最低權益P50"],
            "最差路徑歷史最低運作資金": r["歷史最低權益"],
            "斷頭路徑數": round(r["起點數"] * r["斷頭比例(%)"] / 100),
        })
    return pd.DataFrame(rows).sort_values("總損益中位數", ascending=False).reset_index(drop=True)


def _source_slice_for_start(session_bars: pd.DataFrame, start_date: pd.Timestamp,
                            max_end_date: pd.Timestamp, warmup_bars: int) -> pd.DataFrame:
    src = session_bars.copy()
    src_dates = pd.to_datetime(src["trade_date"], errors="coerce").dt.normalize()
    unique_dates = pd.Index(sorted(src_dates.dropna().unique()))
    start_pos = int(unique_dates.searchsorted(start_date, side="left"))
    warm_pos = max(start_pos - max(int(warmup_bars), 0), 0)
    warm_date = pd.Timestamp(unique_dates[warm_pos])
    mask = (src_dates >= warm_date) & (src_dates <= pd.Timestamp(max_end_date).normalize())
    return src.loc[mask].reset_index(drop=True)


def run_rolling_start_sensitivity(session_bars: pd.DataFrame, items: list[tuple[str, dict]],
                                  cost: CostModel, initial_capital: float, batch_meta: dict,
                                  benchmark_df: pd.DataFrame | None = None,
                                  benchmark_fee_rate: float = 0.001425,
                                  progress_callback: Callable | None = None) -> dict:
    cfg = copy.deepcopy(batch_meta.get("rolling_start_config") or {})
    cfg["start_regime_classifier"] = copy.deepcopy(batch_meta.get("start_regime_classifier") or {})
    horizons = cfg.get("horizons") or [{"label": "啟動後1年", "trading_days": 252}]
    max_horizon = max(int(x.get("trading_days", 252) or 252) for x in horizons)
    warmup_bars = int((cfg.get("start_generation") or {}).get("warmup_bars", 300) or 300)

    if not items:
        raise ValueError("起點敏感度沒有策略")
    # 只用第一個策略建立完整日K與起點分類。策略訊號不參與分類。
    first_cfg = copy.deepcopy(items[0][1])
    full_frame, _, full_mt = prepare_execution_frame({"1D": session_bars}, first_cfg)
    if str(full_mt.get("execution_timeframe", "1D")) != "1D":
        raise ValueError("起點敏感度目前只支援日K執行策略")
    full_frame = full_frame.reset_index(drop=True)
    starts = _monthly_starts(full_frame, cfg)
    if not starts:
        raise ValueError("指定期間沒有符合暖機條件的有效起點")
    full_dates = _date_series(full_frame)

    rows = []
    representatives_candidates = {}
    total_units = len(items) * len(starts) * len(horizons)
    done = 0
    for name, strategy_cfg in items:
        for start_spec in starts:
            matches = np.flatnonzero((full_dates >= start_spec.date).to_numpy())
            if len(matches) == 0:
                continue
            global_start_idx = int(matches[0])
            max_end_idx = min(global_start_idx + max_horizon - 1, len(full_frame) - 1)
            max_end_date = pd.Timestamp(full_dates.iloc[max_end_idx])
            source = _source_slice_for_start(
                session_bars, start_spec.date, max_end_date, warmup_bars)
            local_cfg = copy.deepcopy(strategy_cfg)
            local_cfg["signal_state_reset_date"] = str(start_spec.date.date())
            local_frame, params, mt_info = prepare_execution_frame({"1D": source}, local_cfg)
            if str(mt_info.get("execution_timeframe", "1D")) != "1D":
                raise ValueError("起點敏感度目前只支援日K執行策略")
            local_frame = local_frame.reset_index(drop=True)
            local_dates = _date_series(local_frame)
            local_matches = np.flatnonzero((local_dates >= start_spec.date).to_numpy())
            if len(local_matches) == 0:
                continue
            local_start_idx = int(local_matches[0])

            for horizon in horizons:
                requested = max(int(horizon.get("trading_days", 252) or 252), 1)
                end_idx = local_start_idx + requested
                if end_idx > len(local_frame):
                    done += 1
                    if progress_callback:
                        progress_callback(done / max(total_units, 1),
                                          f"{done}/{total_units}｜{name}｜{start_spec.date.date()}｜資料不足略過")
                    continue
                part = local_frame.iloc[local_start_idx:end_idx].reset_index(drop=True)
                params_copy = copy.deepcopy(params)
                params_copy.position_sizing_capital = float(initial_capital)
                trades, equity = run_backtest(part, cost, params_copy)
                metrics = compute_metrics(
                    trades, equity, margin_reference=cost.original_margin_amount,
                    quantity=cost.quantity, initial_capital=initial_capital,
                    market_data=part,
                )
                row = _result_row(
                    name, start_spec, horizon, part, trades, equity, metrics,
                    initial_capital, benchmark_df, benchmark_fee_rate,
                )
                rows.append(row)
                representatives_candidates.setdefault(name, []).append({
                    "start": start_spec, "horizon": horizon, "frame": part,
                    "trades": trades, "equity": equity, "metrics": metrics,
                    "config": strategy_cfg,
                })
                done += 1
                if progress_callback:
                    progress_callback(done / max(total_units, 1),
                                      f"{done}/{total_units}｜{name}｜{start_spec.date.date()}｜{horizon.get('label')}")

    detail = pd.DataFrame(rows)
    if detail.empty:
        raise ValueError("起點敏感度沒有可用結果；請檢查起點與資料期限")
    state_summary = _summary_rows(detail, ["策略名稱", "觀察期限", "起點市場狀態"])
    strategy_summary = _summary_rows(detail, ["策略名稱", "觀察期限"])
    thresholds = _threshold_table(detail, batch_meta.get("ruin_and_asset_thresholds", {}).get(
        "asset_thresholds", [0, 100000, 250000, 300000, 400000, 500000]))
    worst = detail.sort_values(["策略名稱", "觀察期限", "期末資產(元)"], ascending=[True, True, True]).groupby(
        ["策略名稱", "觀察期限"], group_keys=False).head(20).reset_index(drop=True)
    best = detail.sort_values(["策略名稱", "觀察期限", "期末資產(元)"], ascending=[True, True, False]).groupby(
        ["策略名稱", "觀察期限"], group_keys=False).head(20).reset_index(drop=True)
    ruin = detail[(detail["維持保證金強制平倉"] == True) | (detail["帳戶權益曾小於等於0"] == True)].copy()  # noqa: E712
    wait = detail[["策略名稱", "啟動日", "觀察期限", "起點市場狀態", "第一筆交易等待交易日", "交易次數"]].copy()
    paired = _paired_comparison(detail)
    benchmark_detail, benchmark_summary, strategy_vs_benchmark = _benchmark_tables(detail)
    comparison = _standard_comparison(strategy_summary, initial_capital)

    representatives = {}
    for name, candidates in representatives_candidates.items():
        first_label = str(horizons[0].get("label")) if horizons else ""
        first_horizon_candidates = [x for x in candidates if str(x["horizon"].get("label")) == first_label] or candidates
        terminals = np.array([
            float(pd.to_numeric(x["equity"]["account_equity"], errors="coerce").iloc[-1])
            for x in first_horizon_candidates
        ])
        median = float(np.median(terminals))
        chosen = first_horizon_candidates[int(np.argmin(np.abs(terminals - median)))]
        representatives[name] = {
            "seed": str(chosen["start"].date.date()),
            "config": chosen["config"],
            "frame": chosen["frame"],
            "trades": chosen["trades"],
            "equity": chosen["equity"],
            "metrics": chosen["metrics"],
            "yearly": yearly_stats(chosen["trades"], chosen["equity"]),
            "timeframes": {"1D": chosen["frame"]},
            "multi_timeframe": {"execution_timeframe": "1D", "long_signal_timeframe": "1D", "short_signal_timeframe": "1D"},
        }

    return {
        "comparison": comparison,
        "distribution": detail,
        "representatives": representatives,
        "seeds": [0],
        "requested_seeds": [0],
        "required_timeframes": ["1D"],
        "deterministic_1d_fast_mode": True,
        "simulation_validation": {"status": "不適用", "valid": None, "checked_seeds": 0, "error_count": 0, "errors": []},
        "rolling_start_mode": True,
        "rolling_start": {
            "detail": detail,
            "state_summary": state_summary,
            "strategy_summary": strategy_summary,
            "thresholds": thresholds,
            "worst": worst,
            "best": best,
            "ruin": ruin,
            "wait": wait,
            "paired": paired,
            "benchmark_detail": benchmark_detail,
            "benchmark_summary": benchmark_summary,
            "strategy_vs_benchmark": strategy_vs_benchmark,
            "start_count": len(starts),
            "horizons": [str(x.get("label")) for x in horizons],
        },
    }
