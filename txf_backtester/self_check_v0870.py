# -*- coding: utf-8 -*-
"""v0.8.7.0 空單多週期研究版專項檢查。"""
from pathlib import Path
import json
import numpy as np
import pandas as pd

from backtester import CostModel, run_backtest
from batch_utils import parse_strategy_batch
from condition_blocks import evaluate_condition
from strategies import StrategyParams


def _base(rows=5):
    return pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=rows, freq="D"),
        "open": [100.0] * rows,
        "high": [101.0] * rows,
        "low": [99.0] * rows,
        "close": [100.0] * rows,
        "volume": [1000.0] * rows,
        "long_entry": [False] * rows,
        "short_entry": [False] * rows,
        "atr": [2.0] * rows,
    })


def main():
    passed = []

    df = _base(40)
    for spec in [
        {"type": "close_return_pct_below", "lookback": 3, "value": -1.5},
        {"type": "di_minus_above_di_plus", "period": 14},
        {"type": "adx_above", "period": 14, "value": 20},
        {"type": "ma_rejection_bearish", "period": 20},
        {"type": "close_cross_down_ma", "period": 20},
    ]:
        assert len(evaluate_condition(df, spec)) == len(df)
    passed.append("new_short_condition_blocks")

    df = _base(4)
    df.loc[0, "short_entry"] = True
    df.loc[2, "high"] = 102.0
    p = StrategyParams(direction="short", use_chandelier=False, use_macd_reverse=False,
                       use_fixed_stop=True, stop_threshold_mode="entry_pct",
                       stop_entry_pct=1.5)
    trades, _ = run_backtest(df, CostModel(point_value=50, fee=0, slippage_points=0), p)
    assert len(trades) == 1 and trades.iloc[0]["exit_reason"] == "fixed_stop"
    assert abs(float(trades.iloc[0]["exit_price"]) - 101.5) < 1e-9
    passed.append("entry_pct_stop")

    df = _base(4)
    df.loc[0, "short_entry"] = True
    p = StrategyParams(direction="short", use_chandelier=False, use_macd_reverse=False,
                       use_fixed_stop=False, use_max_holding_exit=True,
                       max_holding_bars=2)
    trades, _ = run_backtest(df, CostModel(point_value=50, fee=0, slippage_points=0), p)
    assert len(trades) == 1 and trades.iloc[0]["exit_reason"] == "max_holding_exit"
    passed.append("max_holding_exit")

    df = _base(4)
    df.loc[0, "short_entry"] = True
    df["short_entry_group_1"] = [True, False, False, False]
    df["short_entry_group_reason_1"] = ["g1", "", "", ""]
    df["exit_short_group_1_signal"] = [False, False, True, False]
    p = StrategyParams(direction="short", use_chandelier=False, use_macd_reverse=False,
                       use_fixed_stop=False, use_signal_exit=True)
    trades, _ = run_backtest(df, CostModel(point_value=50, fee=0, slippage_points=0), p)
    assert len(trades) == 1 and trades.iloc[0]["exit_reason"] == "signal_exit_group_1"
    passed.append("entry_group_exit_block")

    df = _base(4)
    df.loc[0, "short_entry"] = True
    df["short_r_reference_ma"] = [101.0, np.nan, np.nan, np.nan]
    df.loc[2, "low"] = 96.0
    df.loc[2, "close"] = 98.0
    p = StrategyParams(direction="short", use_chandelier=False, use_macd_reverse=False,
                       use_fixed_stop=False, use_partial_r_exit=True,
                       partial_r_multiple=3.0, partial_exit_fraction=0.5)
    trades, _ = run_backtest(df, CostModel(point_value=50, fee=0, slippage_points=0), p)
    assert "partial_r_exit" in set(trades["exit_reason"])
    passed.append("partial_r_exit")

    batch_path = Path(__file__).resolve().parent / "batch_039_三家AI空單初選_6組.json"
    batch_name, items, _ = parse_strategy_batch(batch_path.read_text(encoding="utf-8"), symbol="MTX")
    assert len(items) == 6 and batch_name.startswith("batch_039")
    passed.append("six_strategy_batch_parse")

    print(f"PASS {len(passed)} v0.8.7.0 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
