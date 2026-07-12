# -*- coding: utf-8 -*-
"""歷史截止日與未來日K情境延伸。

情境參數由歷史日K的報酬、波動、跳空與K棒形狀抽樣，不依策略績效挑選。
三個策略與00631L基準共用相同來源區段，避免比較時行情不一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from backtester import CostModel, run_backtest
from benchmark_00631l import adjusted_returns_by_date
from metrics import compute_metrics
from multi_timeframe import prepare_execution_frame, required_timeframes_for_config
from synthetic_timeframes import aggregate_full_session_daily

SCENARIO_STATES = (
    "低波動上漲", "高波動上漲", "低波動盤整",
    "高波動震盪", "緩慢下跌", "崩跌後反彈",
)


@dataclass(frozen=True)
class ScenarioConfig:
    paths_per_state: int = 3
    block_days: int = 20
    min_future_days: int = 126
    max_future_days: int = 504
    seed: int = 20260712


def _daily_features(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy().sort_values("trade_date").reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    open_ = pd.to_numeric(d["open"], errors="coerce")
    high = pd.to_numeric(d["high"], errors="coerce")
    low = pd.to_numeric(d["low"], errors="coerce")
    prev = close.shift(1)
    d["close_return"] = close.pct_change().replace([np.inf, -np.inf], np.nan)
    d["gap_return"] = (open_ / prev - 1.0).replace([np.inf, -np.inf], np.nan)
    d["intraday_return"] = (close / open_ - 1.0).replace([np.inf, -np.inf], np.nan)
    d["upper_wick"] = (high / pd.concat([open_, close], axis=1).max(axis=1) - 1.0).clip(lower=0)
    d["lower_wick"] = (1.0 - low / pd.concat([open_, close], axis=1).min(axis=1)).clip(lower=0)
    d["trend60"] = close.pct_change(60)
    d["vol20"] = d["close_return"].rolling(20).std() * np.sqrt(252)
    d["ret20"] = close.pct_change(20)
    d["future20"] = close.shift(-20) / close - 1.0
    return d


def _candidate_starts(features: pd.DataFrame, block_days: int) -> dict[str, list[int]]:
    valid = features.dropna(subset=["trend60", "vol20", "ret20"]).copy()
    if valid.empty:
        return {s: [] for s in SCENARIO_STATES}
    trend_hi = valid["trend60"].quantile(.65)
    trend_lo = valid["trend60"].quantile(.35)
    abs_side = valid["trend60"].abs().quantile(.40)
    vol_med = valid["vol20"].median()
    vol_hi = valid["vol20"].quantile(.70)
    crash_q = valid["ret20"].quantile(.10)
    rebound_q = valid["future20"].quantile(.70)
    masks = {
        "低波動上漲": (valid["trend60"] >= trend_hi) & (valid["vol20"] <= vol_med),
        "高波動上漲": (valid["trend60"] >= trend_hi) & (valid["vol20"] > vol_med),
        "低波動盤整": (valid["trend60"].abs() <= abs_side) & (valid["vol20"] <= vol_med),
        "高波動震盪": (valid["trend60"].abs() <= abs_side) & (valid["vol20"] >= vol_hi),
        "緩慢下跌": (valid["trend60"] <= trend_lo) & (valid["vol20"] <= vol_hi),
        "崩跌後反彈": (valid["ret20"] <= crash_q) & (valid["future20"] >= rebound_q),
    }
    max_start = len(features) - block_days
    out = {}
    for state, mask in masks.items():
        idxs = valid.index[mask].tolist()
        if state == "崩跌後反彈":
            # 事件點代表「過去20日崩跌、未來20日反彈」，區塊置中才能同時含兩段。
            starts = sorted({max(1, min(int(i - block_days // 2), max_start)) for i in idxs if max_start >= 1})
        else:
            # 其餘狀態以指標所在日為區段終點。
            starts = sorted({max(1, min(int(i - block_days + 1), max_start)) for i in idxs if max_start >= 1})
        out[state] = starts
    required_cols = ["gap_return", "intraday_return", "upper_wick", "lower_wick"]
    all_starts = []
    for start in range(1, max(max_start, 1)):
        block = features.iloc[start:start + block_days]
        valid_ratio = block[required_cols].notna().all(axis=1).mean() if len(block) else 0.0
        if valid_ratio >= 0.8:
            all_starts.append(start)
    if not all_starts:
        all_starts = list(range(1, max(max_start, 1)))
    for state in SCENARIO_STATES:
        if len(out[state]) < 3:
            out[state] = all_starts
    return out


def infer_future_days(full_daily: pd.DataFrame, items: list[tuple[str, dict]],
                      cost: CostModel, initial_capital: float,
                      min_days: int = 126, max_days: int = 504) -> int:
    """用完整歷史交易的持有期第99百分位×2決定延伸長度。"""
    tfs = {"1D": full_daily}
    holds = []
    for _, cfg in items:
        if required_timeframes_for_config(cfg) != {"1D"}:
            continue
        frame, params, _ = prepare_execution_frame(tfs, cfg)
        trades, _ = run_backtest(frame, cost, params)
        if trades is not None and not trades.empty and "holding_bars" in trades.columns:
            s = pd.to_numeric(trades["holding_bars"], errors="coerce").dropna()
            if len(s):
                holds.append(float(s.quantile(.99)))
    estimate = int(np.ceil(max(holds) * 2)) if holds else 252
    return int(min(max(estimate, int(min_days)), int(max_days)))


def generate_future_daily(library_daily: pd.DataFrame, last_close: float,
                          start_date, state: str, days: int, seed: int,
                          block_days: int = 20,
                          prepared_features: pd.DataFrame | None = None,
                          candidate_map: dict[str, list[int]] | None = None) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    """以狀態條件式歷史區段抽樣，重建未來OHLC；回傳來源日期供正二配對。"""
    if state not in SCENARIO_STATES:
        raise ValueError(f"未知未來情境：{state}")
    features = prepared_features if prepared_features is not None else _daily_features(library_daily)
    candidates_by_state = candidate_map if candidate_map is not None else _candidate_starts(features, block_days)
    candidates = candidates_by_state[state]
    if not candidates:
        raise ValueError("歷史資料不足以產生未來情境")
    rng = np.random.default_rng(int(seed))
    sampled = []
    source_dates = []
    attempts = 0
    max_attempts = max(days * 20, 1000)
    while len(sampled) < days and attempts < max_attempts:
        attempts += 1
        start = int(rng.choice(candidates))
        block = features.iloc[start:start + block_days].copy()
        if block.empty:
            continue
        for _, row in block.iterrows():
            if len(sampled) >= days:
                break
            if pd.isna(row.get("gap_return")) or pd.isna(row.get("intraday_return")):
                continue
            sampled.append(row)
            source_dates.append(pd.Timestamp(row["trade_date"]).normalize())
    if len(sampled) < days:
        valid_rows = features.dropna(subset=["gap_return", "intraday_return"])
        if valid_rows.empty:
            raise ValueError("歷史資料沒有足夠有效K棒可生成未來情境")
        while len(sampled) < days:
            row = valid_rows.iloc[int(rng.integers(0, len(valid_rows)))]
            sampled.append(row)
            source_dates.append(pd.Timestamp(row["trade_date"]).normalize())
    dates = pd.bdate_range(pd.Timestamp(start_date) + pd.offsets.BDay(1), periods=len(sampled))
    rows = []
    prev_close = float(last_close)
    base_volume = float(pd.to_numeric(library_daily.get("volume", pd.Series([1])), errors="coerce").median() or 1)
    for date, src in zip(dates, sampled):
        gap = float(np.clip(src["gap_return"], -0.20, 0.20))
        intraday = float(np.clip(src["intraday_return"], -0.20, 0.20))
        open_ = max(prev_close * (1.0 + gap), 0.01)
        close = max(open_ * (1.0 + intraday), 0.01)
        upper = float(np.clip(src.get("upper_wick", 0.0) or 0.0, 0.0, 0.20))
        lower = float(np.clip(src.get("lower_wick", 0.0) or 0.0, 0.0, 0.20))
        high = max(open_, close) * (1.0 + upper)
        low = max(min(open_, close) * (1.0 - lower), 0.01)
        volume = float(src.get("volume", base_volume) or base_volume)
        rows.append({
            "datetime": pd.Timestamp(date) + pd.Timedelta(hours=13, minutes=45),
            "trade_date": pd.Timestamp(date).normalize(),
            "symbol": str(library_daily.get("symbol", pd.Series(["MTX"])).iloc[0]),
            "contract_month": "FUTURE",
            "session": "full_session", "timeframe": "1D",
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "open_interest": float(src.get("open_interest", 0) or 0),
            "scenario_state": state, "source_trade_date": pd.Timestamp(src["trade_date"]).normalize(),
            "simulated_future": True,
        })
        prev_close = close
    return pd.DataFrame(rows), source_dates


def _benchmark_future_returns(benchmark_df: pd.DataFrame | None,
                              source_dates: list[pd.Timestamp],
                              underlying_future: pd.DataFrame) -> pd.Series:
    if benchmark_df is not None and not benchmark_df.empty:
        by_date = adjusted_returns_by_date(benchmark_df)
        values = [by_date.get(pd.Timestamp(d).normalize(), np.nan) for d in source_dates]
        s = pd.Series(values, dtype=float)
    else:
        s = pd.Series(np.nan, index=range(len(underlying_future)), dtype=float)
    underlying_ret = pd.to_numeric(underlying_future["close"], errors="coerce").pct_change()
    if len(underlying_ret):
        underlying_ret.iloc[0] = (float(underlying_future["close"].iloc[0]) /
                                  float(underlying_future["open"].iloc[0]) - 1.0)
    fallback = (2.0 * underlying_ret).clip(lower=-0.95, upper=0.95)
    return s.fillna(fallback).fillna(0.0).clip(lower=-0.95)


def _benchmark_historical_value(benchmark_df: pd.DataFrame | None, start, cutoff,
                                initial_capital: float, buy_fee_rate: float) -> tuple[float, float]:
    if benchmark_df is None or benchmark_df.empty:
        return float(initial_capital), 0.0
    from benchmark_00631l import historical_buy_hold_curve, benchmark_metrics
    part = benchmark_df[(benchmark_df["date"] >= pd.Timestamp(start)) &
                        (benchmark_df["date"] <= pd.Timestamp(cutoff))]
    if part.empty:
        return float(initial_capital), 0.0
    curve = historical_buy_hold_curve(part, initial_capital, buy_fee_rate)
    metrics = benchmark_metrics(curve, initial_capital)
    return float(curve["account_value"].iloc[-1]), float(metrics.get("最大回撤率(%)", 0.0))


def _cagr(value: float, initial: float, start, end) -> float:
    days = max((pd.Timestamp(end) - pd.Timestamp(start)).days, 1)
    ratio = float(value) / float(initial)
    return (ratio ** (365.25 / days) - 1.0) * 100 if ratio > 0 else np.nan


def run_cutoff_scenarios(session_bars: pd.DataFrame, items: list[tuple[str, dict]],
                         cost: CostModel, initial_capital: float,
                         cutoff_dates: Iterable, benchmark_df: pd.DataFrame | None = None,
                         benchmark_buy_fee_rate: float = 0.001425,
                         config: ScenarioConfig | None = None,
                         progress_callback=None) -> dict:
    """共同截止日×六種未來狀態。

    v0.8.4 主排名改採「共同路徑期末總權益」對「00631L期末市值」。
    未自然出場仍保留為資訊欄位，但不再要求未來必須終止，因為真實市場本來
    就永遠有下一天。已實現損益仍作為輔助診斷。
    """
    config = config or ScenarioConfig()
    for name, cfg in items:
        if required_timeframes_for_config(cfg) != {"1D"}:
            raise ValueError(f"未來日K情境模式目前只支援完整日K策略：{name}")
    full_daily = aggregate_full_session_daily(session_bars).sort_values("trade_date").reset_index(drop=True)
    prepared_features = _daily_features(full_daily)
    candidate_map = _candidate_starts(prepared_features, config.block_days)
    future_days = infer_future_days(full_daily, items, cost, initial_capital,
                                    config.min_future_days, config.max_future_days)
    cutoffs = [pd.Timestamp(x).normalize() for x in cutoff_dates]
    cutoffs = [x for x in cutoffs if full_daily["trade_date"].min() < x <= full_daily["trade_date"].max()]
    if not cutoffs:
        raise ValueError("沒有可用的共同截止日")
    total = len(cutoffs) * len(SCENARIO_STATES) * config.paths_per_state * len(items)
    done = 0
    rows = []
    rng = np.random.default_rng(int(config.seed))
    start_date = pd.Timestamp(full_daily["trade_date"].min()).normalize()

    for cutoff in cutoffs:
        hist = full_daily[full_daily["trade_date"] <= cutoff].copy()
        if len(hist) < 120:
            continue
        last_close = float(hist["close"].iloc[-1])
        bench_at_cutoff, benchmark_hist_dd = _benchmark_historical_value(
            benchmark_df, start_date, cutoff, initial_capital, benchmark_buy_fee_rate)
        for state in SCENARIO_STATES:
            for path_idx in range(config.paths_per_state):
                path_seed = int(rng.integers(1, np.iinfo(np.int32).max))
                future, source_dates = generate_future_daily(
                    full_daily, last_close, cutoff, state, future_days, path_seed, config.block_days,
                    prepared_features=prepared_features, candidate_map=candidate_map)
                combined = pd.concat([hist, future], ignore_index=True, sort=False)
                benchmark_returns = _benchmark_future_returns(benchmark_df, source_dates, future)
                bench_values = bench_at_cutoff * (1.0 + benchmark_returns).cumprod()
                benchmark_end = float(bench_values.iloc[-1]) if len(bench_values) else bench_at_cutoff
                bench_curve = pd.concat([pd.Series([bench_at_cutoff]), bench_values], ignore_index=True)
                bench_future_peak = bench_curve.cummax()
                bench_future_dd = ((bench_curve / bench_future_peak) - 1.0).min() * 100
                benchmark_dd = min(float(benchmark_hist_dd), float(bench_future_dd))
                end_date = pd.Timestamp(combined["trade_date"].iloc[-1])
                benchmark_annual = _cagr(benchmark_end, initial_capital, start_date, end_date)

                for name, cfg in items:
                    frame, params, _ = prepare_execution_frame({"1D": combined}, cfg)
                    trades, equity = run_backtest(frame, cost, params)
                    metrics = compute_metrics(trades, equity,
                                              margin_reference=cost.original_margin_amount,
                                              quantity=cost.quantity,
                                              initial_capital=initial_capital,
                                              market_data=combined)
                    total_pnl = float(metrics.get("總損益(元)", 0.0) or 0.0)
                    strategy_end = float(initial_capital) + total_pnl
                    strategy_annual = _cagr(strategy_end, initial_capital, start_date, end_date)
                    realized_pnl = float(metrics.get("扣除期末強制平倉後損益(元)", total_pnl) or 0.0)
                    realized_end = float(initial_capital) + realized_pnl
                    realized_annual = _cagr(realized_end, initial_capital, start_date, end_date)
                    strategy_dd = float(metrics.get("策略標準最大回撤率(%)", metrics.get("最大回撤(%)", np.nan)))
                    unresolved = int(metrics.get("期末強制平倉交易數", 0) or 0) > 0
                    asset_diff = strategy_end - benchmark_end
                    asset_relative = ((strategy_end / benchmark_end) - 1.0) * 100.0 if benchmark_end > 0 else np.nan
                    annual_diff = strategy_annual - benchmark_annual if pd.notna(strategy_annual) else np.nan
                    drawdown_improvement = strategy_dd - benchmark_dd if pd.notna(strategy_dd) else np.nan
                    beat = bool(strategy_end > benchmark_end)
                    rows.append({
                        "截止日": str(cutoff.date()), "未來情境": state,
                        "路徑編號": path_idx + 1, "seed": path_seed, "策略名稱": name,
                        "延伸交易日": future_days,
                        "策略期末總權益(元)": round(strategy_end, 0),
                        "正二期末資產(元)": round(benchmark_end, 0),
                        "期末資產差(元)": round(asset_diff, 0),
                        "期末資產相對正二(%)": round(float(asset_relative), 2) if pd.notna(asset_relative) else np.nan,
                        "策略總權益年化報酬率(%)": round(float(strategy_annual), 2) if pd.notna(strategy_annual) else np.nan,
                        "正二年化報酬率(%)": round(float(benchmark_annual), 2),
                        "相對正二年化差(百分點)": round(float(annual_diff), 2) if pd.notna(annual_diff) else np.nan,
                        "最大回撤率(%)": round(float(strategy_dd), 2) if pd.notna(strategy_dd) else np.nan,
                        "正二最大回撤率(%)": round(float(benchmark_dd), 2),
                        "相對正二回撤改善(百分點)": round(float(drawdown_improvement), 2) if pd.notna(drawdown_improvement) else np.nan,
                        "期末總權益超越正二": beat,
                        # 向下相容舊欄位；v0.8.4起語意改為期末總權益比較。
                        "超越正二": beat,
                        "已實現損益(元)": round(realized_pnl, 0),
                        "已實現年化報酬率(%)": round(float(realized_annual), 2) if pd.notna(realized_annual) else np.nan,
                        "總損益含期末浮動(元)": round(total_pnl, 0),
                        "最大回撤(元)": metrics.get("最大回撤(元)", 0.0),
                        "斷頭次數": metrics.get("斷頭次數", 0),
                        "尚未自然出場": unresolved,
                    })
                    done += 1
                    if progress_callback:
                        progress_callback(done / max(total, 1),
                                          f"情境驗證 {done}/{total}｜{cutoff.date()}｜{state}｜{name}")
    dist = pd.DataFrame(rows)
    if dist.empty:
        return {"distribution": dist, "comparison": pd.DataFrame(), "future_days": future_days}

    summaries = []
    for name, grp in dist.groupby("策略名稱", sort=False):
        end_eq = pd.to_numeric(grp["策略期末總權益(元)"], errors="coerce")
        bench_eq = pd.to_numeric(grp["正二期末資產(元)"], errors="coerce")
        asset_diff = pd.to_numeric(grp["期末資產差(元)"], errors="coerce")
        annual = pd.to_numeric(grp["策略總權益年化報酬率(%)"], errors="coerce")
        bench_annual = pd.to_numeric(grp["正二年化報酬率(%)"], errors="coerce")
        annual_diff = pd.to_numeric(grp["相對正二年化差(百分點)"], errors="coerce")
        dd = pd.to_numeric(grp["最大回撤率(%)"], errors="coerce")
        bench_dd = pd.to_numeric(grp["正二最大回撤率(%)"], errors="coerce")
        dd_improve = pd.to_numeric(grp["相對正二回撤改善(百分點)"], errors="coerce")
        realized_pnl = pd.to_numeric(grp["已實現損益(元)"], errors="coerce")
        summaries.append({
            "策略名稱": name,
            "情境路徑數": len(grp),
            "期末總權益超越正二比例(%)": round(float(grp["期末總權益超越正二"].astype(bool).mean() * 100), 2),
            "策略期末總權益中位數": round(float(end_eq.median()), 0),
            "策略期末總權益P25": round(float(end_eq.quantile(.25)), 0),
            "策略期末總權益P10": round(float(end_eq.quantile(.10)), 0),
            "正二期末資產中位數": round(float(bench_eq.median()), 0),
            "正二期末資產P25": round(float(bench_eq.quantile(.25)), 0),
            "正二期末資產P10": round(float(bench_eq.quantile(.10)), 0),
            "期末資產差中位數": round(float(asset_diff.median()), 0),
            "期末資產差P10": round(float(asset_diff.quantile(.10)), 0),
            "策略總權益年化中位數(%)": round(float(annual.median()), 2),
            "正二年化中位數(%)": round(float(bench_annual.median()), 2),
            "相對正二年化差中位數(百分點)": round(float(annual_diff.median()), 2),
            "策略最大回撤率中位數(%)": round(float(dd.median()), 2),
            "正二最大回撤率中位數(%)": round(float(bench_dd.median()), 2),
            "相對正二回撤改善中位數(百分點)": round(float(dd_improve.median()), 2),
            "策略最差最大回撤率(%)": round(float(dd.min()), 2),
            "正二最差最大回撤率(%)": round(float(bench_dd.min()), 2),
            "已實現損益中位數": round(float(realized_pnl.median()), 0),
            "已實現損益P10": round(float(realized_pnl.quantile(.10)), 0),
            "斷頭路徑比例(%)": round(float((pd.to_numeric(grp["斷頭次數"], errors="coerce") > 0).mean() * 100), 2),
            "尚未自然出場比例(%)": round(float(grp["尚未自然出場"].astype(bool).mean() * 100), 2),
            # 舊欄名保留，避免舊匯出閱讀程式中斷。
            "超越正二路徑比例(%)": round(float(grp["期末總權益超越正二"].astype(bool).mean() * 100), 2),
            "已實現年化中位數(%)": round(float(pd.to_numeric(grp["已實現年化報酬率(%)"], errors="coerce").median()), 2),
            "最大回撤率中位數(%)": round(float(dd.median()), 2),
            "最差最大回撤率(%)": round(float(dd.min()), 2),
        })
    compare = pd.DataFrame(summaries).sort_values(
        ["期末總權益超越正二比例(%)", "相對正二年化差中位數(百分點)", "期末資產差P10"],
        ascending=[False, False, False], kind="mergesort").reset_index(drop=True)

    state_summary = dist.groupby(["策略名稱", "未來情境"], as_index=False).agg(
        策略總權益年化中位數=("策略總權益年化報酬率(%)", "median"),
        正二年化中位數=("正二年化報酬率(%)", "median"),
        相對正二年化差中位數=("相對正二年化差(百分點)", "median"),
        策略最大回撤率中位數=("最大回撤率(%)", "median"),
        正二最大回撤率中位數=("正二最大回撤率(%)", "median"),
        相對正二回撤改善中位數=("相對正二回撤改善(百分點)", "median"),
        期末總權益超越正二比例=("期末總權益超越正二", "mean"),
        斷頭比例=("斷頭次數", lambda x: (pd.to_numeric(x, errors="coerce") > 0).mean()),
        尚未自然出場比例=("尚未自然出場", "mean"),
    )
    for col in ("期末總權益超越正二比例", "斷頭比例", "尚未自然出場比例"):
        state_summary[col] = (state_summary[col] * 100).round(2)
    numeric_cols = [c for c in state_summary.columns if c not in {"策略名稱", "未來情境",
                    "期末總權益超越正二比例", "斷頭比例", "尚未自然出場比例"}]
    for col in numeric_cols:
        state_summary[col] = pd.to_numeric(state_summary[col], errors="coerce").round(2)
    return {
        "distribution": dist, "comparison": compare,
        "state_summary": state_summary, "future_days": future_days,
        "cutoff_dates": [str(x.date()) for x in cutoffs],
        "scenario_states": list(SCENARIO_STATES),
        "ranking_basis": "共同路徑期末總權益對00631L期末市值",
    }

