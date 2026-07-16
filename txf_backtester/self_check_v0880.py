# -*- coding: utf-8 -*-
"""v0.8.8.0 起點敏感度功能自我檢查。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtester import CostModel
from batch_utils import apply_position_mode, parse_strategy_batch
from benchmark_00631l import apply_split_adjustment
from rolling_start import run_rolling_start_sensitivity
from strategies import _apply_entry_group_priority_policy


def synthetic_bars() -> pd.DataFrame:
    rng = np.random.default_rng(20260716)
    dates = pd.bdate_range("2015-01-05", "2022-12-30")
    returns = rng.normal(0.00025, 0.011, len(dates))
    close = 9000 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, len(dates)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.004, 0.003, len(dates))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.004, 0.003, len(dates))))
    return pd.DataFrame({
        "datetime": dates + pd.Timedelta(hours=13, minutes=45),
        "trade_date": dates,
        "previous_trade_date": dates - pd.offsets.BDay(1),
        "session": "day",
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.integers(10000, 50000, len(dates)),
    })


def check_priority_reset():
    dates = pd.bdate_range("2020-01-01", periods=15)
    out = pd.DataFrame({"datetime": dates, "trade_date": dates})
    groups = [
        pd.Series([True] + [False] * 14),
        pd.Series([False] * 15),
        pd.Series([False] * 10 + [True] + [False] * 4),
    ]
    reasons = [pd.Series(["H"] * 15), pd.Series([""] * 15), pd.Series(["L"] * 15)]
    cfg = {
        "signal_state_reset_date": str(dates[10].date()),
        "entry_group_priority_policy": {
            "direction": "short", "higher_priority_groups": [1, 2],
            "lower_priority_groups": [3], "block_lower_after_higher_signal_bars": 10,
        },
    }
    _, _, new_groups, _ = _apply_entry_group_priority_policy(
        out, groups[0] | groups[2], reasons[0], groups, reasons, cfg, "short")
    assert bool(new_groups[2].iloc[10]), "起點前高優先訊號不應封鎖起點後低優先入口"


def main():
    root = Path(__file__).resolve().parent
    batch_path = root / "examples" / "batch_049_L16_L14_50萬起點敏感度.json"
    raw = batch_path.read_text(encoding="utf-8")
    _, items, meta = parse_strategy_batch(raw)
    meta = copy.deepcopy(meta)
    meta["rolling_start_config"]["start_generation"].update({
        "start_from": "2019-01-01", "start_to": "2019-06-30"})
    meta["rolling_start_config"]["horizons"] = [
        {"label": "60日", "trading_days": 60},
        {"label": "120日", "trading_days": 120},
    ]
    bars = synthetic_bars()
    rng = np.random.default_rng(99)
    bm_close = 50 * np.exp(np.cumsum(rng.normal(0.0002, 0.009, len(bars))))
    benchmark = apply_split_adjustment(pd.DataFrame({
        "date": bars["trade_date"], "open": bm_close, "high": bm_close,
        "low": bm_close, "close": bm_close, "volume": 1000,
    }))
    final_items = [(name, apply_position_mode(cfg, "json", 500000)) for name, cfg in items]
    cost = CostModel(
        point_value=50, fee=20, slippage_points=1, tax_rate=0.00002,
        original_margin_amount=159000, use_margin_call_check=True,
        safety_buffer_amount=341000,
    )
    result = run_rolling_start_sensitivity(
        bars, final_items, cost, 500000, meta, benchmark_df=benchmark)
    detail = result["rolling_start"]["detail"]
    assert len(detail) == 24, f"預期24列，實際{len(detail)}列"
    assert set(detail["起始資產(元)"]) == {500000.0}
    assert detail["期末資產超越00631L"].notna().all()
    assert detail["同期00631L期末資產(元)"].notna().all()
    assert detail["同期00631L買進股數"].gt(0).all()
    assert detail["同期00631L最大回撤率(%)"].notna().all()
    assert len(result["representatives"]) == 2
    required = {
        "state_summary", "thresholds", "worst", "best", "ruin", "wait", "paired",
        "benchmark_detail", "benchmark_summary", "strategy_vs_benchmark",
    }
    assert required.issubset(result["rolling_start"])
    assert len(result["rolling_start"]["benchmark_detail"]) == 12
    assert not result["rolling_start"]["benchmark_summary"].empty
    assert not result["rolling_start"]["strategy_vs_benchmark"].empty
    check_priority_reset()
    print("v0.8.8.0 self-check: PASS")
    print(f"rows={len(detail)}, starts={result['rolling_start']['start_count']}")


if __name__ == "__main__":
    main()
