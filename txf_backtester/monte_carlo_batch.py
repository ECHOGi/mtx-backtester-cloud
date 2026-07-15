# -*- coding: utf-8 -*-
"""多策略、多隨機路徑批次回測；每個 seed 的模擬 K 線由所有策略共用。"""
from __future__ import annotations

import copy
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
        "扣除期末強制平倉後損益(元)": m.get("扣除期末強制平倉後損益(元)", m.get("總損益(元)", 0.0)),
        "最大回撤率(%)": m.get("策略標準最大回撤率(%)", m.get("最大回撤(%)", np.nan)),
        "最大有效槓桿(倍)": m.get("最大有效槓桿(倍)", np.nan),
        "歷史最低運作資金(元)": m.get("歷史最低運作資金(元)", np.nan),
        "回撤煞車觸發次數": m.get("回撤煞車觸發次數", 0),
        "煞車狀態交易日數": m.get("煞車狀態交易日數", 0),
        "煞車狀態交易日占比(%)": m.get("煞車狀態交易日占比(%)", 0.0),
        "平均每日回撤煞車倍率": m.get("平均每日回撤煞車倍率", 1.0),
        "最低每日回撤煞車倍率": m.get("最低每日回撤煞車倍率", 1.0),
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
        "總損益P75": round(q("總損益(元)", .75), 0),
        "總損益P10": round(q("總損益(元)", .10), 0),
        "報酬回撤比中位數": round(q("報酬回撤比", .50), 3),
        "報酬回撤比P25": round(q("報酬回撤比", .25), 3),
        "報酬回撤比P75": round(q("報酬回撤比", .75), 3),
        "最大回撤中位數": round(q("最大回撤(元)", .50), 0),
        "最差路徑最大回撤": round(float(pd.to_numeric(group["最大回撤(元)"], errors="coerce").min()), 0),
        "年化報酬率中位數(%)": round(q("年化報酬率(%)", .50), 2),
        "獲利因子中位數": round(q("獲利因子", .50), 2),
        "交易次數中位數": round(q("交易次數", .50), 1),
        "期末強制平倉損益中位數": round(q("期末強制平倉損益(元)", .50), 0),
        "扣除期末強制平倉後損益中位數": round(q("扣除期末強制平倉後損益(元)", .50), 0),
        "最大回撤率中位數(%)": round(q("最大回撤率(%)", .50), 2),
        "最大有效槓桿中位數(倍)": round(q("最大有效槓桿(倍)", .50), 2),
        "歷史最低運作資金中位數": round(q("歷史最低運作資金(元)", .50), 0),
        "最差路徑歷史最低運作資金": round(q("歷史最低運作資金(元)", 1.0), 0),
        "斷頭路徑數": int((margin_calls > 0).sum()),
        "煞車觸發次數中位數": round(q("回撤煞車觸發次數", .50), 1),
        "煞車狀態交易日數中位數": round(q("煞車狀態交易日數", .50), 1),
        "煞車狀態交易日占比中位數(%)": round(q("煞車狀態交易日占比(%)", .50), 2),
        "平均每日回撤煞車倍率中位數": round(q("平均每日回撤煞車倍率", .50, 1.0), 4),
        "最低每日回撤煞車倍率中位數": round(q("最低每日回撤煞車倍率", .50, 1.0), 4),
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



def _normalize_event_windows(session_bars: pd.DataFrame, event_windows: list[dict]) -> list[dict]:
    if not event_windows:
        raise ValueError("事件區間模式缺少 event_windows")
    dates = pd.to_datetime(session_bars["trade_date"], errors="coerce").dt.normalize()
    data_min, data_max = dates.min(), dates.max()
    out = []
    for i, item in enumerate(event_windows, 1):
        if not isinstance(item, dict):
            raise ValueError(f"event_windows 第{i}項必須是物件")
        start = pd.Timestamp(item.get("start")).normalize()
        end = pd.Timestamp(item.get("end")).normalize()
        if pd.isna(start) or pd.isna(end) or start > end:
            raise ValueError(f"event_windows 第{i}項日期無效")
        start = max(start, data_min)
        end = min(end, data_max)
        if start > end:
            continue
        out.append({
            "label": str(item.get("label") or f"事件{i}"),
            "start": start,
            "end": end,
        })
    out.sort(key=lambda x: x["start"])
    if not out:
        raise ValueError("事件區間與現有資料沒有交集")
    for prev, cur in zip(out, out[1:]):
        if cur["start"] <= prev["end"]:
            raise ValueError(f"事件區間不可重疊：{prev['label']}／{cur['label']}")
    return out


def _event_source_slice(session_bars: pd.DataFrame, event: dict, warmup_trade_days: int) -> pd.DataFrame:
    src = session_bars.copy()
    src["_trade_day"] = pd.to_datetime(src["trade_date"], errors="coerce").dt.normalize()
    unique_days = pd.Index(sorted(src["_trade_day"].dropna().unique()))
    start_pos = int(unique_days.searchsorted(event["start"], side="left"))
    warm_pos = max(start_pos - max(int(warmup_trade_days), 0), 0)
    warm_start = pd.Timestamp(unique_days[warm_pos]) if len(unique_days) else event["start"]
    mask = (src["_trade_day"] >= warm_start) & (src["_trade_day"] <= event["end"])
    out = src.loc[mask].drop(columns=["_trade_day"]).reset_index(drop=True)
    if out.empty:
        raise ValueError(f"事件區間沒有資料：{event['label']}")
    return out


def _slice_event_frame(frame: pd.DataFrame, event: dict) -> pd.DataFrame:
    out = frame.copy()
    if "trade_date" in out.columns:
        days = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
    else:
        days = pd.to_datetime(out["datetime"], errors="coerce").dt.normalize()
    out = out[(days >= event["start"]) & (days <= event["end"])].reset_index(drop=True)
    if len(out) < 2:
        raise ValueError(f"事件區間執行資料不足：{event['label']}")
    return out


def _strategy_event_run(session_bars: pd.DataFrame, name: str, cfg: dict, cost: CostModel,
                        seed: int, initial_capital: float, event_windows: list[dict],
                        warmup_trade_days: int, simulation_config: SimulationConfig | None,
                        required_timeframes: set[str], timeframe_cache: dict,
                        validation: dict) -> dict:
    current_capital = float(initial_capital)
    all_trades, all_equity, market_parts, event_rows = [], [], [], []
    last_frame = pd.DataFrame()
    last_mt_info = {}

    for event_idx, event in enumerate(event_windows, 1):
        cache_key = (int(seed), int(event_idx), tuple(sorted(required_timeframes)))
        if cache_key not in timeframe_cache:
            source = _event_source_slice(session_bars, event, warmup_trade_days)
            tfs = build_simulated_timeframes(
                source, int(seed), simulation_config, required=required_timeframes)
            timeframe_cache[cache_key] = (source, tfs)
            if "30m" in tfs:
                errors = validate_simulation(source, tfs["30m"])
                validation["checked_event_paths"] += 1
                if errors:
                    validation["valid"] = False
                    validation["error_count"] += len(errors)
                    validation["errors"].extend(errors[:10])
        source, tfs = timeframe_cache[cache_key]
        frame, params, mt_info = prepare_execution_frame(tfs, cfg)
        event_frame = _slice_event_frame(frame, event)
        params.position_sizing_capital = float(current_capital)
        trades, equity = run_backtest(event_frame, cost, params)

        daily_event = _slice_event_frame(tfs["1D"], event)
        event_metrics = compute_metrics(
            trades, equity, margin_reference=cost.original_margin_amount,
            quantity=cost.quantity, initial_capital=current_capital,
            market_data=daily_event)
        pnl = float(event_metrics.get("總損益(元)", 0.0) or 0.0)

        trades = trades.copy()
        if not trades.empty:
            trades.insert(0, "事件", event["label"])
            trades.insert(1, "事件序號", event_idx)
            all_trades.append(trades)
        equity = equity.copy()
        if not equity.empty:
            equity.insert(0, "事件", event["label"])
            equity.insert(1, "事件序號", event_idx)
            # 每個事件承接前一事件的期末資金；全域 equity 以原始本金為基準。
            equity["equity"] = pd.to_numeric(equity["account_equity"], errors="coerce") - float(initial_capital)
            all_equity.append(equity)
        market_parts.append(daily_event)
        event_rows.append({
            "策略名稱": name,
            "seed": int(seed),
            "事件": event["label"],
            "事件序號": int(event_idx),
            "開始日": str(event["start"].date()),
            "結束日": str(event["end"].date()),
            "事件起始資金(元)": round(current_capital, 0),
            "事件期末資金(元)": round(current_capital + pnl, 0),
            "總損益(元)": round(pnl, 0),
            "最大回撤(元)": event_metrics.get("最大回撤(元)", 0.0),
            "最大回撤率(%)": event_metrics.get("策略標準最大回撤率(%)", event_metrics.get("最大回撤(%)", np.nan)),
            "報酬回撤比": event_metrics.get("報酬回撤比", np.nan),
            "獲利因子": event_metrics.get("獲利因子", np.nan),
            "交易次數": event_metrics.get("交易次數", 0),
            "勝率(%)": event_metrics.get("勝率(%)", 0.0),
            "期末強制平倉損益(元)": event_metrics.get("期末強制平倉損益(元)", 0.0),
            "斷頭次數": event_metrics.get("斷頭次數", 0),
        })
        current_capital += pnl
        last_frame = event_frame
        last_mt_info = mt_info

    trades_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    equity_all = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()
    market_all = pd.concat(market_parts, ignore_index=True) if market_parts else pd.DataFrame()
    metrics = compute_metrics(
        trades_all, equity_all, margin_reference=cost.original_margin_amount,
        quantity=cost.quantity, initial_capital=float(initial_capital),
        market_data=market_all)
    return {
        "seed": int(seed), "config": cfg, "frame": last_frame,
        "trades": trades_all, "equity": equity_all, "metrics": metrics,
        "yearly": yearly_stats(trades_all, equity_all),
        "multi_timeframe": last_mt_info,
        "event_rows": event_rows,
    }


def run_batch_event_monte_carlo(session_bars: pd.DataFrame, items: list[tuple[str, dict]],
                                cost: CostModel, seeds: list[int], initial_capital: float,
                                event_windows: list[dict], warmup_trade_days: int = 140,
                                simulation_config: SimulationConfig | None = None,
                                progress_callback=None) -> dict:
    """只在指定事件區間回測；暖機資料僅供指標形成，不允許在事件外進場。"""
    windows = _normalize_event_windows(session_bars, event_windows)
    requested_seeds = [int(x) for x in seeds] or [42]
    strategy_requirements = {
        name: set(required_timeframes_for_config(cfg)) | {"1D"}
        for name, cfg in items
    }
    stochastic_names = {
        name for name, req in strategy_requirements.items() if req != {"1D"}
    }
    total_runs = sum((len(requested_seeds) if name in stochastic_names else 1) * len(windows)
                     for name, _ in items)
    done = 0
    started = time.perf_counter()
    rows, event_rows, representatives = [], [], {}
    timeframe_cache = {}
    validation = {
        "status": "不適用" if not stochastic_names else "待檢查",
        "valid": None if not stochastic_names else True,
        "checked_seeds": 0,
        "checked_event_paths": 0,
        "error_count": 0,
        "errors": [],
    }

    for name, cfg in items:
        actual_seeds = requested_seeds if name in stochastic_names else requested_seeds[:1]
        actual_results = []
        for seed in actual_seeds:
            result = _strategy_event_run(
                session_bars, name, cfg, cost, seed, initial_capital, windows,
                warmup_trade_days, simulation_config, strategy_requirements[name],
                timeframe_cache, validation)
            actual_results.append(result)
            row = _metric_row(name, seed, result["metrics"])
            mt = result.get("multi_timeframe") or {}
            row.update({
                "執行週期": mt.get("execution_timeframe", cfg.get("timeframe", "1D")),
                "多單訊號週期": mt.get("long_signal_timeframe", cfg.get("timeframe", "1D")),
                "空單訊號週期": mt.get("short_signal_timeframe", cfg.get("timeframe", "1D")),
            })
            rows.append(row)
            event_rows.extend(result["event_rows"])
            done += len(windows)
            if progress_callback:
                elapsed = max(time.perf_counter() - started, 1e-9)
                remaining = (elapsed / max(done, 1)) * (total_runs - done)
                progress_callback(
                    done / max(total_runs, 1),
                    f"{done}/{total_runs}｜{name}｜seed {seed}｜事件區間｜預估剩餘 {_format_remaining(remaining)}")

        # 代表路徑選總損益最接近該策略實際seed中位數者。
        pnl_values = [float(x["metrics"].get("總損益(元)", 0.0) or 0.0) for x in actual_results]
        median_pnl = float(np.median(pnl_values)) if pnl_values else 0.0
        rep_index = int(np.argmin([abs(x - median_pnl) for x in pnl_values])) if pnl_values else 0
        representatives[name] = actual_results[rep_index]

        # 純日K策略不受seed影響；將同一結果複製成共同20條路徑，方便同表比較。
        if name not in stochastic_names and len(requested_seeds) > 1:
            template_row = rows[-1].copy()
            template_events = [x.copy() for x in actual_results[0]["event_rows"]]
            for seed in requested_seeds[1:]:
                cloned = template_row.copy()
                cloned["seed"] = int(seed)
                rows.append(cloned)
                for event_row in template_events:
                    e = event_row.copy()
                    e["seed"] = int(seed)
                    event_rows.append(e)

    if stochastic_names:
        validation["checked_seeds"] = len(requested_seeds)
        validation["status"] = "通過" if validation["valid"] else "失敗"

    dist = pd.DataFrame(rows)
    summaries = []
    for name, _ in items:
        grp = dist[dist["策略名稱"] == name].copy()
        summaries.append({"策略名稱": name, **_summary(grp)})
    compare = pd.DataFrame(summaries).sort_values(
        ["報酬回撤比中位數", "總損益中位數", "最差路徑最大回撤"],
        ascending=[False, False, False], kind="mergesort").reset_index(drop=True)

    return {
        "comparison": compare,
        "distribution": dist,
        "event_distribution": pd.DataFrame(event_rows),
        "representatives": representatives,
        "seeds": requested_seeds,
        "requested_seeds": requested_seeds,
        "required_timeframes": sorted(set().union(*strategy_requirements.values())),
        "deterministic_1d_fast_mode": False,
        "simulation_validation": validation,
        "event_mode": True,
        "event_windows": [
            {"label": x["label"], "start": str(x["start"].date()), "end": str(x["end"].date())}
            for x in windows
        ],
        "event_warmup_trade_days": int(warmup_trade_days),
        "actual_strategy_event_runs": int(total_runs),
    }
