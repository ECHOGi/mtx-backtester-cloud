# -*- coding: utf-8 -*-
"""多策略、多隨機路徑批次回測；每個 seed 的模擬 K 線由所有策略共用。"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from backtester import CostModel, run_backtest
from metrics import compute_metrics, yearly_stats
from multi_timeframe import prepare_execution_frame, required_timeframes_for_config
from synthetic_timeframes import (SimulationConfig, build_simulated_timeframes,
                                  validate_simulation)


def _metric_row(name, seed, m):
    """分布表統一使用中文欄名，避免UI與匯出表混用中英文。"""
    return {
        "策略名稱": name,
        "seed": int(seed),
        "總損益(元)": m.get("總損益(元)", 0.0),
        "報酬回撤比": m.get("報酬回撤比", np.nan),
        "最大回撤(元)": m.get("最大回撤(元)", 0.0),
        "年化報酬率(%)": m.get("年化報酬率(%)", np.nan),
        "獲利因子": m.get("獲利因子", np.nan),
        "交易次數": m.get("交易次數", 0),
        "勝率(%)": m.get("勝率(%)", 0.0),
        "斷頭次數": m.get("斷頭次數", 0),
        "期末強制平倉損益(元)": m.get("期末強制平倉損益(元)", 0.0),
        "歷史最低運作資金(元)": m.get("歷史最低運作資金(元)", np.nan),
    }


def _summary(group: pd.DataFrame) -> dict:
    def q(col, pct, default=0.0):
        if col not in group.columns:
            return default
        s = pd.to_numeric(group[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        return float(s.quantile(pct)) if len(s) else default

    pnl = pd.to_numeric(group["總損益(元)"], errors="coerce").fillna(0.0)
    margin_calls = pd.to_numeric(group["斷頭次數"], errors="coerce").fillna(0)
    return {
        "路徑數": int(len(group)),
        "獲利路徑比例(%)": round(float((pnl > 0).mean() * 100), 2),
        "總損益中位數": round(q("總損益(元)", .50), 0),
        "總損益P25": round(q("總損益(元)", .25), 0),
        "總損益P10": round(q("總損益(元)", .10), 0),
        "報酬回撤比中位數": round(q("報酬回撤比", .50), 3),
        "報酬回撤比P25": round(q("報酬回撤比", .25), 3),
        "最大回撤中位數": round(q("最大回撤(元)", .50), 0),
        "最差路徑最大回撤": round(float(pd.to_numeric(group["最大回撤(元)"], errors="coerce").min()), 0),
        "年化報酬率中位數(%)": round(q("年化報酬率(%)", .50), 2),
        "獲利因子中位數": round(q("獲利因子", .50), 2),
        "交易次數中位數": round(q("交易次數", .50), 1),
        "期末強制平倉損益中位數": round(q("期末強制平倉損益(元)", .50), 0),
        "歷史最低運作資金中位數": round(q("歷史最低運作資金(元)", .50), 0),
        "最差路徑歷史最低運作資金": round(q("歷史最低運作資金(元)", 1.0), 0),
        "斷頭路徑數": int((margin_calls > 0).sum()),
    }


def _format_remaining(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    if seconds < 60:
        return f"{seconds}秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{sec:02d}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}時{minutes:02d}分"


def run_batch_monte_carlo(session_bars: pd.DataFrame, items: list[tuple[str, dict]],
                          cost: CostModel, seeds: list[int], initial_capital: float,
                          simulation_config: SimulationConfig | None = None,
                          progress_callback=None) -> dict:
    rows = []
    required_timeframes = {"1D"}
    for _, cfg in items:
        required_timeframes.update(required_timeframes_for_config(cfg))

    requested_seeds = [int(x) for x in seeds] or [42]
    deterministic_1d = required_timeframes == {"1D"}
    effective_seeds = requested_seeds[:1] if deterministic_1d else requested_seeds

    total = max(len(effective_seeds) * len(items), 1)
    done = 0
    started = time.perf_counter()
    validation = {
        "status": "不適用" if deterministic_1d else "待檢查",
        "valid": None if deterministic_1d else True,
        "checked_seeds": 0,
        "error_count": 0,
        "errors": [],
    }

    for seed in effective_seeds:
        timeframes = build_simulated_timeframes(
            session_bars, int(seed), simulation_config, required=required_timeframes)
        if "30m" in timeframes:
            errors = validate_simulation(session_bars, timeframes["30m"])
            validation["checked_seeds"] += 1
            if errors:
                validation["valid"] = False
                validation["error_count"] += len(errors)
                validation["errors"].extend(errors[:10])
        for name, cfg in items:
            frame, params, mt_info = prepare_execution_frame(timeframes, cfg)
            trades, equity = run_backtest(frame, cost, params)
            m = compute_metrics(
                trades, equity, margin_reference=cost.original_margin_amount,
                quantity=cost.quantity, initial_capital=initial_capital,
                market_data=timeframes["1D"])
            row = _metric_row(name, seed, m)
            row.update({
                "執行週期": mt_info["execution_timeframe"],
                "多單訊號週期": mt_info["long_signal_timeframe"],
                "空單訊號週期": mt_info["short_signal_timeframe"],
            })
            rows.append(row)
            done += 1
            if progress_callback:
                elapsed = max(time.perf_counter() - started, 1e-9)
                remaining = (elapsed / done) * (total - done)
                progress_callback(
                    done / total,
                    f"{done}/{total}｜{name}｜seed {seed}｜預估剩餘 {_format_remaining(remaining)}")

    if not deterministic_1d:
        validation["status"] = "通過" if validation["valid"] else "失敗"

    dist = pd.DataFrame(rows)
    summaries = []
    representative_seeds = {}
    for name, _ in items:
        grp = dist[dist["策略名稱"] == name].copy()
        summaries.append({"策略名稱": name, **_summary(grp)})
        pnl = pd.to_numeric(grp["總損益(元)"], errors="coerce").fillna(0.0)
        median = float(pnl.median())
        representative_seeds[name] = int(grp.loc[(pnl - median).abs().idxmin(), "seed"])

    compare = pd.DataFrame(summaries).sort_values(
        ["報酬回撤比中位數", "總損益中位數", "最差路徑最大回撤"],
        ascending=[False, False, False], kind="mergesort").reset_index(drop=True)

    representatives = {}
    for name, cfg in items:
        seed = representative_seeds[name]
        tfs = build_simulated_timeframes(
            session_bars, seed, simulation_config, required=required_timeframes)
        frame, params, mt_info = prepare_execution_frame(tfs, cfg)
        trades, equity = run_backtest(frame, cost, params)
        m = compute_metrics(
            trades, equity, margin_reference=cost.original_margin_amount,
            quantity=cost.quantity, initial_capital=initial_capital,
            market_data=tfs["1D"])
        representatives[name] = {
            "seed": seed, "config": cfg, "frame": frame, "trades": trades,
            "equity": equity, "metrics": m, "yearly": yearly_stats(trades, equity),
            "timeframes": tfs, "multi_timeframe": mt_info,
        }

    return {
        "comparison": compare,
        "distribution": dist,
        "representatives": representatives,
        "seeds": effective_seeds,
        "requested_seeds": requested_seeds,
        "required_timeframes": sorted(required_timeframes),
        "deterministic_1d_fast_mode": deterministic_1d,
        "simulation_validation": validation,
    }
