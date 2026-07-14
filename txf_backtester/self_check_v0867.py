# -*- coding: utf-8 -*-
"""v0.8.6.7 全域出場機制比較版自檢。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backtester import CostModel, run_backtest
from correctness import summarize_validation, validate_trades
from strategies import StrategyParams


def _frame(rows):
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ("long_entry", "short_entry"):
        if c not in df:
            df[c] = False
    if "volume" not in df:
        df["volume"] = 1000.0
    return df


def _base_params():
    p = StrategyParams()
    p.direction = "long"
    p.use_chandelier = False
    p.use_profit_tier_chandelier = False
    p.use_macd_reverse = False
    p.use_take_profit = False
    p.use_trailing_stop = False
    p.use_signal_exit = False
    p.use_dynamic_position_sizing = False
    p.use_regime_position_sizing = False
    p.use_account_margin_model = False
    p.minimum_holding_bars = 10
    return p


def main():
    root = Path(__file__).resolve().parent
    info = json.loads((root / "version.json").read_text(encoding="utf-8"))
    assert info["version"] == "v0.8.6.7"
    assert info["build_id"] == "20260714-5"

    cost = CostModel(point_value=1.0, fee=0.0, slippage_points=0.0,
                     tax_rate=0.0, quantity=1)

    # 1) 取消實際固定停損後，仍可用 1 ATR 虛擬距離進行 dynamic_risk 部位估算。
    rows = [
        {"datetime": "2026-01-01", "open": 100, "high": 101, "low": 99, "close": 100,
         "atr": 10.0, "long_entry": True},
        {"datetime": "2026-01-02", "open": 100, "high": 103, "low": 97, "close": 101,
         "atr": 10.0},
        {"datetime": "2026-01-03", "open": 101, "high": 104, "low": 98, "close": 102,
         "atr": 10.0},
    ]
    p = _base_params()
    p.use_fixed_stop = False
    p.position_sizing_reference_atr_multiple = 1.0
    p.use_dynamic_position_sizing = True
    p.use_account_margin_model = True
    p.position_sizing_mode = "dynamic_risk"
    p.position_sizing_capital = 500000.0
    p.position_risk_fraction = 0.14
    p.position_stress_multiple = 1.0
    p.position_micro_point_value = 10.0
    p.position_micro_margin = 1.0
    p.position_micro_maintenance_margin = 1.0
    p.position_max_micro_units = 0
    p.position_use_stress_capital_check = False
    p.position_compounding = True
    trades, _ = run_backtest(_frame(rows), cost, p)
    assert len(trades) == 1, trades
    t = trades.iloc[0]
    assert pd.isna(t["planned_stop_points"])
    assert float(t["position_sizing_reference_points"]) == 10.0
    assert int(t["position_micro_units"]) > 0

    # 2) 第5根收盤確認無效，下一根開盤退出；不受 minimum_holding_bars=10 阻擋。
    rows = [
        {"datetime": "2026-02-01", "open": 100, "high": 101, "low": 99, "close": 100,
         "atr": 10.0, "long_entry": True},
        {"datetime": "2026-02-02", "open": 100, "high": 102, "low": 98, "close": 99, "atr": 10.0},
        {"datetime": "2026-02-03", "open": 99, "high": 102, "low": 97, "close": 99, "atr": 10.0},
        {"datetime": "2026-02-04", "open": 99, "high": 103, "low": 98, "close": 99, "atr": 10.0},
        {"datetime": "2026-02-05", "open": 99, "high": 103, "low": 97, "close": 98, "atr": 10.0},
        {"datetime": "2026-02-06", "open": 98, "high": 103, "low": 97, "close": 98, "atr": 10.0},
        {"datetime": "2026-02-07", "open": 97, "high": 100, "low": 96, "close": 99, "atr": 10.0},
    ]
    p = _base_params()
    p.use_fixed_stop = True
    p.stop_threshold_mode = "entry_atr"
    p.stop_atr_multiple = 1.5
    p.use_time_invalid_exit = True
    p.time_invalid_exit_bars = 5
    p.time_invalid_max_favorable_atr_multiple = 0.5
    p.time_invalid_require_losing_close = True
    trades, _ = run_backtest(_frame(rows), cost, p)
    assert len(trades) == 1, trades
    t = trades.iloc[0]
    assert t["exit_reason"] == "time_invalid_exit", t
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp("2026-02-07")
    assert float(t["exit_price"]) == 97.0
    checks = validate_trades(_frame(rows), trades, cost, p)
    assert summarize_validation(checks)["failed_checks"] == 0, checks[checks.status == "FAIL"]

    # 3) 若前5根最大有利移動已達 0.5 ATR，不應觸發無效退出。
    rows[3]["high"] = 106.0
    trades, _ = run_backtest(_frame(rows), cost, p)
    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "end_of_data"

    print("PASS 3 v0.8.6.7 cases")
    print("- virtual_atr_reference_sizes_no_stop_variant")
    print("- five_bar_invalid_trade_exits_next_open")
    print("- sufficient_mfe_prevents_invalid_exit")


if __name__ == "__main__":
    main()
