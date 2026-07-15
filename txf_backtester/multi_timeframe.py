# -*- coding: utf-8 -*-
"""multi_timeframe.py - 日K方向、60分K訊號、30分K執行的多週期回測橋接層。

本模組不做即時監控；它把受日/夜 OHLC 約束的模擬 30 分 K 當作最小事件流，
60 分 K 由 30 分 K 聚合，完整日 K 由原始日/夜資料合成。高週期訊號只會在該根
K 棒完成後傳到 30 分 K，並由下一根 30 分 K 開盤執行，避免未來函數。
"""
from __future__ import annotations

import copy
from dataclasses import asdict

import numpy as np
import pandas as pd

from backtester import CostModel, run_backtest
from metrics import compute_metrics
from strategies import (add_indicator_columns, evaluate_combo, params_from_config,
                        run_strategy_config)
from synthetic_timeframes import SimulationConfig, build_simulated_timeframes


def _tf_name(value: str | None, default: str = "1D") -> str:
    text = str(value or default).strip().lower().replace("min", "m")
    aliases = {
        "d": "1D", "1d": "1D", "day": "1D", "daily": "1D", "full_session": "1D",
        "30": "30m", "30m": "30m", "30分": "30m",
        "60": "60m", "60m": "60m", "1h": "60m", "60分": "60m",
    }
    return aliases.get(text, str(value or default))




def required_timeframes_for_config(cfg: dict) -> set[str]:
    """回傳單一策略真正需要的資料週期。

    市場比較與報表永遠需要完整日 K；若策略使用 30m/60m 或啟用多週期，
    再加入相應週期。
    """
    mt = cfg.get("multi_timeframe") or {}
    default_tf = _tf_name(cfg.get("timeframe"), "1D")
    required = {"1D"}
    if bool(mt.get("enabled", False)):
        execution_tf = _tf_name(mt.get("execution_timeframe"), default_tf)
        required.add(execution_tf)
        required.add(_tf_name(mt.get("long_signal_timeframe"), default_tf))
        required.add(_tf_name(mt.get("short_signal_timeframe"), default_tf))
        required.add(_tf_name(mt.get("long_exit_signal_timeframe"), execution_tf))
        required.add(_tf_name(mt.get("short_exit_signal_timeframe"), execution_tf))
        for key in ("entry_long_components", "entry_short_components",
                    "exit_long_components", "exit_short_components"):
            spec = mt.get(key) or {}
            for component in (spec.get("components") or []):
                if isinstance(component, dict):
                    required.add(_tf_name(component.get("timeframe"), default_tf))
    else:
        required.add(default_tf)
    return required

def _ensure_entry_blocks(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    empty = {"logic": "AND", "conditions": []}
    out.setdefault("entry_long", copy.deepcopy(empty))
    out.setdefault("entry_short", copy.deepcopy(empty))
    return out


def _direction_signal_frame(data: pd.DataFrame, cfg: dict, direction: str):
    local = _ensure_entry_blocks(cfg)
    local["direction"] = direction
    params = params_from_config(local)
    sig = run_strategy_config(data, local, params)
    col = "long_entry" if direction == "long" else "short_entry"
    reason_col = "long_entry_reasons" if direction == "long" else "short_entry_reasons"
    pos_units = "long_position_micro_units" if direction == "long" else "short_position_micro_units"
    pos_regime = "long_position_regime" if direction == "long" else "short_position_regime"
    keep = ["datetime", col, reason_col, "atr"]
    for c in (pos_units, pos_regime):
        if c in sig.columns:
            keep.append(c)
    return sig[keep].copy(), params


def _align_signals(execution: pd.DataFrame, signal_df: pd.DataFrame,
                   direction: str) -> pd.DataFrame:
    """把高週期收盤訊號放到第一根已完成的執行 K 棒；實際進場仍是下一根開盤。"""
    out = execution
    side_col = "long_entry" if direction == "long" else "short_entry"
    reason_col = "long_entry_reasons" if direction == "long" else "short_entry_reasons"
    atr_col = "long_entry_atr" if direction == "long" else "short_entry_atr"
    units_col = "long_position_micro_units" if direction == "long" else "short_position_micro_units"
    regime_col = "long_position_regime" if direction == "long" else "short_position_regime"
    out[side_col] = False
    out[reason_col] = ""
    out[atr_col] = np.nan
    if units_col not in out.columns:
        out[units_col] = 0
    if regime_col not in out.columns:
        out[regime_col] = "fixed"

    src = signal_df.loc[signal_df[side_col].fillna(False)].copy()
    if src.empty or out.empty:
        return out
    exec_times = pd.to_datetime(out["datetime"]).to_numpy(dtype="datetime64[ns]")
    for _, row in src.iterrows():
        t = np.datetime64(pd.Timestamp(row["datetime"]))
        idx = int(np.searchsorted(exec_times, t, side="left"))
        if idx >= len(out):
            continue
        out.at[idx, side_col] = True
        out.at[idx, reason_col] = str(row.get(reason_col, "") or f"{direction}_entry")
        out.at[idx, atr_col] = row.get("atr")
        if units_col in row.index and pd.notna(row.get(units_col)):
            out.at[idx, units_col] = int(row.get(units_col) or 0)
        if regime_col in row.index and pd.notna(row.get(regime_col)):
            out.at[idx, regime_col] = str(row.get(regime_col))
    return out


def _align_exit_signal(execution: pd.DataFrame, signal_df: pd.DataFrame,
                       column: str) -> pd.DataFrame:
    """把高週期條件出場對齊到其完成時刻的執行K棒。

    條件出場沿用既有「收盤確認、該收盤價出場」假設；固定停損、ATR停損、
    斷頭與移動停損仍由最小執行週期逐根監控。
    """
    out = execution
    out[column] = False
    if signal_df.empty or column not in signal_df.columns or out.empty:
        return out
    src = signal_df.loc[signal_df[column].fillna(False), ["datetime", column]].copy()
    if src.empty:
        return out
    exec_times = pd.to_datetime(out["datetime"]).to_numpy(dtype="datetime64[ns]")
    for t in pd.to_datetime(src["datetime"], errors="coerce").dropna():
        idx = int(np.searchsorted(exec_times, np.datetime64(t), side="left"))
        if idx < len(out):
            out.at[idx, column] = True
    return out


def _exit_signal_frame(data: pd.DataFrame, cfg: dict, column: str) -> pd.DataFrame:
    params = params_from_config(cfg)
    sig = run_strategy_config(data.copy(), _ensure_entry_blocks(cfg), params)
    if column not in sig.columns:
        return pd.DataFrame(columns=["datetime", column])
    return sig[["datetime", column]].copy()


def _component_signal_frame(data: pd.DataFrame, cfg: dict, block: dict) -> pd.DataFrame:
    """計算單一週期條件元件，回傳 datetime/signal/atr。"""
    params = params_from_config(cfg)
    enriched = add_indicator_columns(data.copy(), params, cfg)
    signal, _ = evaluate_combo(enriched, block or {})
    out = pd.DataFrame({
        "datetime": pd.to_datetime(enriched["datetime"]),
        "signal": signal.fillna(False).astype(bool),
        "atr": pd.to_numeric(enriched.get("atr"), errors="coerce"),
    })
    return out.sort_values("datetime").reset_index(drop=True)


def _align_component(execution: pd.DataFrame, signal_df: pd.DataFrame, mode: str = "state"):
    """state=沿用最近完成高週期狀態；trigger=只在完成時刻產生一次脈衝。"""
    left = pd.DataFrame({"datetime": pd.to_datetime(execution["datetime"])})
    sig = pd.Series(False, index=execution.index, dtype=bool)
    atr = pd.Series(np.nan, index=execution.index, dtype=float)
    if signal_df.empty or execution.empty:
        return sig, atr
    mode = str(mode or "state").lower()
    if mode == "trigger":
        exec_times = left["datetime"].to_numpy(dtype="datetime64[ns]")
        for _, row in signal_df.loc[signal_df["signal"].fillna(False)].iterrows():
            idx = int(np.searchsorted(exec_times, np.datetime64(pd.Timestamp(row["datetime"])), side="left"))
            if idx < len(execution):
                sig.iloc[idx] = True
        # ATR仍以最近完成高週期值對齊，供部位與停損使用。
        merged = pd.merge_asof(left.sort_values("datetime"),
                               signal_df[["datetime", "atr"]].sort_values("datetime"),
                               on="datetime", direction="backward")
        atr.iloc[:] = pd.to_numeric(merged["atr"], errors="coerce").to_numpy()
        return sig, atr
    merged = pd.merge_asof(left.sort_values("datetime"),
                           signal_df[["datetime", "signal", "atr"]].sort_values("datetime"),
                           on="datetime", direction="backward")
    sig.iloc[:] = merged["signal"].astype("boolean").fillna(False).astype(bool).to_numpy()
    atr.iloc[:] = pd.to_numeric(merged["atr"], errors="coerce").to_numpy()
    return sig, atr


def _apply_mtf_components(base: pd.DataFrame, timeframes: dict[str, pd.DataFrame], cfg: dict,
                          spec: dict, output_column: str, reason_column: str | None = None):
    """把跨週期元件以AND/OR組合後寫入執行週期資料。"""
    components = list((spec or {}).get("components") or [])
    if not components:
        return base
    logic = str((spec or {}).get("logic", "AND")).upper()
    if logic not in {"AND", "OR"}:
        raise ValueError(f"跨週期元件logic需為AND/OR，收到 {logic}")
    combined = pd.Series(True if logic == "AND" else False, index=base.index, dtype=bool)
    atr_candidates = []
    labels = []
    for component in components:
        tf = _tf_name(component.get("timeframe"), cfg.get("timeframe", "1D"))
        if tf not in timeframes:
            raise ValueError(f"跨週期元件缺少 {tf} 資料")
        block = component.get("block") or {
            "logic": component.get("logic", "AND"),
            "conditions": component.get("conditions") or [],
            "ever": component.get("ever"),
            "exclude": component.get("exclude"),
        }
        frame = _component_signal_frame(timeframes[tf], cfg, block)
        aligned, aligned_atr = _align_component(base, frame, component.get("mode", "state"))
        combined = (combined & aligned) if logic == "AND" else (combined | aligned)
        atr_candidates.append(aligned_atr)
        labels.append(f"{tf}:{component.get('mode','state')}")
    base[output_column] = combined.fillna(False)
    if reason_column is not None:
        base[reason_column] = np.where(base[output_column], " MTF ".join(labels), "")
        side = "long" if output_column.startswith("long") else "short"
        base[f"{side}_entry_group_1"] = base[output_column].fillna(False)
        base[f"{side}_entry_group_reason_1"] = base[reason_column]
        atr_out = pd.Series(np.nan, index=base.index, dtype=float)
        for candidate in atr_candidates:
            atr_out = atr_out.where(atr_out.notna(), candidate)
        base[f"{side}_entry_atr"] = atr_out
    return base


def prepare_execution_frame(timeframes: dict[str, pd.DataFrame], cfg: dict, indicator_cache: dict | None = None):
    """產生可直接交給 backtester 的執行週期資料。"""
    mt = cfg.get("multi_timeframe") or {}
    enabled = bool(mt.get("enabled", False))
    default_tf = _tf_name(cfg.get("timeframe"), "1D")
    execution_tf = _tf_name(mt.get("execution_timeframe"), default_tf)
    long_tf = _tf_name(mt.get("long_signal_timeframe"), default_tf)
    short_tf = _tf_name(mt.get("short_signal_timeframe"), default_tf)
    long_exit_tf = _tf_name(mt.get("long_exit_signal_timeframe"), execution_tf)
    short_exit_tf = _tf_name(mt.get("short_exit_signal_timeframe"), execution_tf)
    if execution_tf not in timeframes:
        raise ValueError(f"缺少執行週期 {execution_tf}，可用週期：{list(timeframes)}")

    params = params_from_config(cfg)
    execution_cfg = _ensure_entry_blocks(cfg)
    tf_cache = None
    if indicator_cache is not None:
        tf_cache = indicator_cache.setdefault(execution_tf, {})
    base = run_strategy_config(timeframes[execution_tf], execution_cfg, params, indicator_cache=tf_cache)
    if not enabled:
        return base, params, {
            "enabled": False, "execution_timeframe": execution_tf,
            "long_signal_timeframe": execution_tf, "short_signal_timeframe": execution_tf,
            "long_exit_signal_timeframe": execution_tf,
            "short_exit_signal_timeframe": execution_tf,
        }

    # 先清除執行週期自身的進場，再由高週期訊號或跨週期元件覆寫。
    long_components = mt.get("entry_long_components") or {}
    short_components = mt.get("entry_short_components") or {}
    if long_components.get("components"):
        base = _apply_mtf_components(base, timeframes, cfg, long_components,
                                     "long_entry", "long_entry_reasons")
    else:
        long_sig, _ = _direction_signal_frame(timeframes[long_tf], cfg, "long")
        base = _align_signals(base, long_sig, "long")
    if short_components.get("components"):
        base = _apply_mtf_components(base, timeframes, cfg, short_components,
                                     "short_entry", "short_entry_reasons")
    else:
        short_sig, _ = _direction_signal_frame(timeframes[short_tf], cfg, "short")
        base = _align_signals(base, short_sig, "short")

    # 條件出場可由單一高週期或跨週期OR/AND元件組合。
    long_exit_components = mt.get("exit_long_components") or {}
    short_exit_components = mt.get("exit_short_components") or {}
    if long_exit_components.get("components"):
        base = _apply_mtf_components(base, timeframes, cfg, long_exit_components,
                                     "exit_long_signal")
    elif cfg.get("exit_long_block") and long_exit_tf != execution_tf:
        base = _align_exit_signal(base, _exit_signal_frame(
            timeframes[long_exit_tf], cfg, "exit_long_signal"), "exit_long_signal")
    if short_exit_components.get("components"):
        base = _apply_mtf_components(base, timeframes, cfg, short_exit_components,
                                     "exit_short_signal")
    elif cfg.get("exit_short_block") and short_exit_tf != execution_tf:
        base = _align_exit_signal(base, _exit_signal_frame(
            timeframes[short_exit_tf], cfg, "exit_short_signal"), "exit_short_signal")

    direction = str(cfg.get("direction", params.direction))
    if direction == "long":
        base["short_entry"] = False
    elif direction == "short":
        base["long_entry"] = False
    return base, params, {
        "enabled": True, "execution_timeframe": execution_tf,
        "long_signal_timeframe": long_tf, "short_signal_timeframe": short_tf,
        "long_exit_signal_timeframe": long_exit_tf,
        "short_exit_signal_timeframe": short_exit_tf,
    }


def run_strategy_path(session_bars: pd.DataFrame, cfg: dict, cost: CostModel,
                      seed: int = 42, initial_capital: float = 500000.0,
                      simulation_config: SimulationConfig | None = None) -> dict:
    timeframes = build_simulated_timeframes(session_bars, seed=seed, config=simulation_config)
    frame, params, mt_info = prepare_execution_frame(timeframes, cfg)
    trades, equity = run_backtest(frame, cost, params)
    metrics = compute_metrics(
        trades, equity, margin_reference=cost.original_margin_amount,
        quantity=cost.quantity, initial_capital=initial_capital,
        market_data=timeframes.get("1D"),
    )
    return {
        "seed": int(seed), "timeframes": timeframes, "execution_frame": frame,
        "params": params, "trades": trades, "equity": equity, "metrics": metrics,
        "multi_timeframe": mt_info,
    }


def run_monte_carlo(session_bars: pd.DataFrame, cfg: dict, cost: CostModel,
                    seeds: list[int] | tuple[int, ...],
                    initial_capital: float = 500000.0,
                    simulation_config: SimulationConfig | None = None) -> dict:
    """同一策略跑多條可能路徑，回傳分布與代表路徑。"""
    runs = []
    rows = []
    for seed in seeds:
        result = run_strategy_path(
            session_bars, cfg, cost, seed=int(seed), initial_capital=initial_capital,
            simulation_config=simulation_config,
        )
        runs.append(result)
        m = result["metrics"]
        rows.append({
            "seed": int(seed),
            "total_pnl": m.get("總損益(元)", 0.0),
            "return_drawdown_ratio": m.get("報酬回撤比", np.nan),
            "max_drawdown": m.get("最大回撤(元)", 0.0),
            "annual_return": m.get("年化報酬率(%)", np.nan),
            "profit_factor": m.get("獲利因子", np.nan),
            "trade_count": m.get("交易次數", 0),
            "margin_calls": m.get("斷頭次數", 0),
        })
    distribution = pd.DataFrame(rows)
    if distribution.empty:
        return {"runs": [], "distribution": distribution, "summary": {}, "representative": None}

    def q(col, pct):
        ser = pd.to_numeric(distribution[col], errors="coerce").dropna()
        return float(ser.quantile(pct)) if len(ser) else None

    pnl = pd.to_numeric(distribution["total_pnl"], errors="coerce").fillna(0.0)
    summary = {
        "path_count": int(len(distribution)),
        "profitable_path_ratio_pct": round(float((pnl > 0).mean() * 100), 2),
        "median_total_pnl": round(q("total_pnl", 0.50) or 0.0, 1),
        "p25_total_pnl": round(q("total_pnl", 0.25) or 0.0, 1),
        "p75_total_pnl": round(q("total_pnl", 0.75) or 0.0, 1),
        "p10_total_pnl": round(q("total_pnl", 0.10) or 0.0, 1),
        "median_return_drawdown_ratio": round(q("return_drawdown_ratio", 0.50) or 0.0, 3),
        "p25_return_drawdown_ratio": round(q("return_drawdown_ratio", 0.25) or 0.0, 3),
        "p75_return_drawdown_ratio": round(q("return_drawdown_ratio", 0.75) or 0.0, 3),
        "worst_max_drawdown": round(float(pd.to_numeric(distribution["max_drawdown"], errors="coerce").min()), 1),
        "paths_with_margin_call": int((pd.to_numeric(distribution["margin_calls"], errors="coerce").fillna(0) > 0).sum()),
    }
    median_pnl = float(pnl.median())
    rep_idx = int((pnl - median_pnl).abs().idxmin())
    representative = runs[rep_idx]
    return {"runs": runs, "distribution": distribution, "summary": summary,
            "representative": representative}
