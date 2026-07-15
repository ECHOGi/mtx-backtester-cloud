# -*- coding: utf-8 -*-
"""v0.8.6.8 入口模組別出場覆寫版自檢。"""
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
    p.use_fixed_stop = True
    p.stop_threshold_mode = "entry_atr"
    p.stop_atr_multiple = 1.0
    p.position_sizing_reference_atr_multiple = 1.0
    p.use_dynamic_position_sizing = False
    p.use_regime_position_sizing = False
    p.use_account_margin_model = False
    p.minimum_holding_bars = 10
    return p


def _signal_rows(group_index: int, low_on_entry: float = 89.0):
    return [
        {"datetime": "2026-03-01", "open": 100, "high": 101, "low": 99,
         "close": 100, "atr": 10.0, "long_entry": True,
         f"long_entry_group_{group_index}": True,
         f"long_entry_group_reason_{group_index}": f"group_{group_index}"},
        {"datetime": "2026-03-02", "open": 100, "high": 101,
         "low": low_on_entry, "close": 95, "atr": 10.0},
    ]


def main():
    root = Path(__file__).resolve().parent
    info = json.loads((root / "version.json").read_text(encoding="utf-8"))
    assert info["version"] in {"v0.8.6.8", "v0.8.7.0"}
    assert info["build_id"] in {"20260714-6", "20260715-1"}

    cost = CostModel(point_value=1.0, fee=0.0, slippage_points=0.0,
                     tax_rate=0.0, quantity=1)

    # 1) 未覆寫的動能組合1仍使用共用1 ATR停損。
    p = _base_params()
    p.entry_group_exit_overrides = {
        "3": {"stop_atr_multiple": 1.5,
              "position_sizing_reference_atr_multiple": 1.5}
    }
    df = _frame(_signal_rows(1))
    trades, _ = run_backtest(df, cost, p)
    assert len(trades) == 1
    t = trades.iloc[0]
    assert int(t["entry_group_index"]) == 1
    assert t["exit_reason"] == "fixed_stop"
    assert float(t["planned_stop_points"]) == 10.0
    checks = validate_trades(df, trades, cost, p)
    assert summarize_validation(checks)["failed_checks"] == 0, checks[checks.status == "FAIL"]

    # 2) 恐慌組合3套用1.5 ATR，原本會被1 ATR掃出的低點不再停損。
    df = _frame(_signal_rows(3))
    trades, _ = run_backtest(df, cost, p)
    assert len(trades) == 1
    t = trades.iloc[0]
    assert int(t["entry_group_index"]) == 3
    assert t["exit_reason"] == "end_of_data"
    assert float(t["planned_stop_points"]) == 15.0
    assert float(t["position_sizing_reference_points"]) == 15.0
    checks = validate_trades(df, trades, cost, p)
    assert summarize_validation(checks)["failed_checks"] == 0, checks[checks.status == "FAIL"]

    # 3) 只有布林組合2啟用5根K無效退出，下一根開盤離場。
    rows = [
        {"datetime": "2026-04-01", "open": 100, "high": 101, "low": 99,
         "close": 100, "atr": 10.0, "long_entry": True,
         "long_entry_group_2": True,
         "long_entry_group_reason_2": "group_2"},
        {"datetime": "2026-04-02", "open": 100, "high": 102, "low": 98, "close": 99, "atr": 10.0},
        {"datetime": "2026-04-03", "open": 99, "high": 102, "low": 97, "close": 99, "atr": 10.0},
        {"datetime": "2026-04-04", "open": 99, "high": 103, "low": 98, "close": 99, "atr": 10.0},
        {"datetime": "2026-04-05", "open": 99, "high": 103, "low": 97, "close": 98, "atr": 10.0},
        {"datetime": "2026-04-06", "open": 98, "high": 103, "low": 97, "close": 98, "atr": 10.0},
        {"datetime": "2026-04-07", "open": 97, "high": 100, "low": 96, "close": 99, "atr": 10.0},
    ]
    p = _base_params()
    p.stop_atr_multiple = 1.5  # 避免測試期間先碰固定停損
    p.position_sizing_reference_atr_multiple = 1.5
    p.entry_group_exit_overrides = {
        "2": {"use_time_invalid_exit": True,
              "time_invalid_exit_bars": 5,
              "time_invalid_max_favorable_atr_multiple": 0.5,
              "time_invalid_require_losing_close": True}
    }
    df = _frame(rows)
    trades, _ = run_backtest(df, cost, p)
    assert len(trades) == 1
    t = trades.iloc[0]
    assert t["exit_reason"] == "time_invalid_exit"
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp("2026-04-07")
    checks = validate_trades(df, trades, cost, p)
    assert summarize_validation(checks)["failed_checks"] == 0, checks[checks.status == "FAIL"]

    # 4) 相同價格路徑改為動能組合1，未設定無效退出，應持有到資料終點。
    rows[0].pop("long_entry_group_2")
    rows[0].pop("long_entry_group_reason_2")
    rows[0]["long_entry_group_1"] = True
    rows[0]["long_entry_group_reason_1"] = "group_1"
    df = _frame(rows)
    trades, _ = run_backtest(df, cost, p)
    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "end_of_data"
    checks = validate_trades(df, trades, cost, p)
    assert summarize_validation(checks)["failed_checks"] == 0, checks[checks.status == "FAIL"]

    # 5) 動態風險部位會依入口覆寫後的風險距離計算。
    rows = _signal_rows(1, low_on_entry=96.0)
    rows[1]["high"] = 105.0
    p = _base_params()
    p.use_dynamic_position_sizing = True
    p.use_account_margin_model = True
    p.position_sizing_mode = "dynamic_risk"
    p.position_sizing_capital = 500000.0
    p.position_risk_fraction = 0.14
    p.position_stress_multiple = 1.0
    p.position_micro_point_value = 10.0
    p.position_micro_margin = 1.0
    p.position_micro_maintenance_margin = 1.0
    p.position_max_micro_units = 10000
    p.position_use_stress_capital_check = False
    p.position_compounding = True
    p.entry_group_exit_overrides = {
        "3": {"stop_atr_multiple": 1.5,
              "position_sizing_reference_atr_multiple": 1.5}
    }
    df1 = _frame(rows)
    tr1, _ = run_backtest(df1, cost, p)
    units1 = int(tr1.iloc[0]["requested_micro_units"])
    rows3 = _signal_rows(3, low_on_entry=96.0)
    rows3[1]["high"] = 105.0
    df3 = _frame(rows3)
    tr3, _ = run_backtest(df3, cost, p)
    units3 = int(tr3.iloc[0]["requested_micro_units"])
    assert units3 < units1, (units1, units3)
    assert float(tr3.iloc[0]["position_sizing_reference_points"]) == 15.0

    print("PASS 5 v0.8.6.8 cases")
    print("- unlisted_group_keeps_global_exit")
    print("- panic_group_uses_wider_atr_stop")
    print("- bollinger_group_only_time_invalid_exit")
    print("- momentum_group_does_not_inherit_bollinger_exit")
    print("- group_override_changes_dynamic_risk_sizing")


if __name__ == "__main__":
    main()
