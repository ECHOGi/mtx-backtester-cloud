# -*- coding: utf-8 -*-
"""v0.8.8.2 L14→L16權益門檻切換自我檢查。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtester import (CostModel, _apply_equity_risk_switch,
                        _equity_risk_switch_settings)
from batch_utils import apply_position_mode, parse_strategy_batch
from benchmark_00631l import apply_split_adjustment
from rolling_start import run_rolling_start_sensitivity
from synthetic_timeframes import aggregate_full_session_daily
from strategies import params_from_config


def synthetic_session_bars() -> pd.DataFrame:
    rng = np.random.default_rng(20260716)
    dates = pd.bdate_range("2015-01-05", "2022-12-30")
    returns = rng.normal(0.00035, 0.012, len(dates))
    daily_close = 9000 * np.exp(np.cumsum(returns))
    prev = np.r_[daily_close[0], daily_close[:-1]]
    rows = []
    for i, d in enumerate(dates):
        night_open = prev[i] * (1 + rng.normal(0, 0.0015))
        night_close = (night_open + daily_close[i]) / 2
        day_open = night_close * (1 + rng.normal(0, 0.001))
        day_close = daily_close[i]
        for session, o, c, order in [("after_hours", night_open, night_close, 0), ("regular", day_open, day_close, 1)]:
            rows.append({
                "datetime": d, "trade_date": d, "symbol": "MTX",
                "contract_month": int(d.strftime("%Y%m")), "session": session,
                "open": o, "high": max(o, c) * 1.006, "low": min(o, c) * 0.994,
                "close": c, "volume": int(rng.integers(5000, 30000)),
                "open_interest": int(rng.integers(10000, 50000)) if order else 0,
            })
    return pd.DataFrame(rows)


def benchmark_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    close = 50 * np.exp(np.cumsum(np.full(len(daily), 0.00025)))
    return apply_split_adjustment(pd.DataFrame({
        "date": daily["trade_date"], "open": close, "high": close,
        "low": close, "close": close, "volume": 1000,
    }))


def main():
    root = Path(__file__).resolve().parent
    batch_path = root / "examples" / "batch_050_L14升L16_權益門檻75_100_125_150萬與遲滯比較.json"
    raw = batch_path.read_text(encoding="utf-8")
    _, items, meta = parse_strategy_batch(raw)
    assert len(items) == 8
    meta = copy.deepcopy(meta)
    meta["rolling_start_config"]["start_generation"].update({"start_from": "2019-01-01", "start_to": "2019-02-28"})
    meta["rolling_start_config"]["horizons"] = [{"label": "啟動後1年", "trading_days": 252}]

    # 直接驗證遲滯狀態機。
    h_cfg = next(cfg for name, cfg in items if name.startswith("H100_80"))
    p = params_from_config(h_cfg)
    settings = _equity_risk_switch_settings(p)
    mode = "low"
    p1, mode, event = _apply_equity_risk_switch(p, settings, mode, 1_050_000)
    assert mode == "high" and event == "upgrade_to_L16" and abs(p1.position_risk_fraction - .16) < 1e-12
    p2, mode, event = _apply_equity_risk_switch(p, settings, mode, 900_000)
    assert mode == "high" and event == "" and abs(p2.position_risk_fraction - .16) < 1e-12
    p3, mode, event = _apply_equity_risk_switch(p, settings, mode, 790_000)
    assert mode == "low" and event == "downgrade_to_L14" and abs(p3.position_risk_fraction - .14) < 1e-12

    # 建立三個執行策略：全程L14、第一筆立刻升L16、永遠不升級。
    base = copy.deepcopy(next(cfg for name, cfg in items if name == "B14_全程L14"))
    immediate = copy.deepcopy(next(cfg for name, cfg in items if name.startswith("T75_")))
    immediate["name"] = "TEST_立即升級"
    immediate["exit"]["equity_risk_upgrade_threshold"] = 1.0
    never = copy.deepcopy(next(cfg for name, cfg in items if name.startswith("T100_")))
    never["name"] = "TEST_永不升級"
    never["exit"]["equity_risk_upgrade_threshold"] = 1e12
    selected = [(x["name"], apply_position_mode(x, "json", 500000)) for x in (base, immediate, never)]

    sessions = synthetic_session_bars()
    daily = aggregate_full_session_daily(sessions)
    benchmark = benchmark_from_daily(daily)
    cost = CostModel(point_value=50, fee=20, slippage_points=1, tax_rate=0.00002,
                     original_margin_amount=159000, use_margin_call_check=True,
                     safety_buffer_amount=341000)
    result = run_rolling_start_sensitivity(sessions, selected, cost, 500000, meta, benchmark_df=benchmark)
    detail = result["rolling_start"]["detail"]
    assert len(detail) == 6
    assert not result["rolling_start"]["switch_summary"].empty
    assert set(detail["觀察交易日"]) == {252}

    immediate_rows = detail[detail["策略名稱"] == "TEST_立即升級"]
    never_rows = detail[detail["策略名稱"] == "TEST_永不升級"]
    assert (immediate_rows["升級次數"] == 1).all()
    assert (immediate_rows["期末風險模式"] == "high").all()
    assert (never_rows["升級次數"] == 0).all()
    assert (never_rows["期末風險模式"] == "low").all()

    for name in ("TEST_立即升級", "TEST_永不升級"):
        trades = result["representatives"][name]["trades"]
        if trades.empty:
            continue
        if name == "TEST_立即升級":
            assert (pd.to_numeric(trades.loc[trades["direction"] == "long", "base_risk_fraction"], errors="coerce") == .16).all()
        else:
            assert (pd.to_numeric(trades.loc[trades["direction"] == "long", "base_risk_fraction"], errors="coerce") == .14).all()

    print("v0.8.8.2 self-check: PASS")
    print(f"rows={len(detail)}, starts={result['rolling_start']['start_count']}, strategies={len(selected)}")


if __name__ == "__main__":
    main()
