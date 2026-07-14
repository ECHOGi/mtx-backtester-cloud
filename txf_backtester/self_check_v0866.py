# -*- coding: utf-8 -*-
"""v0.8.6.6 布林固定停損後新事件重置自檢。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from backtester import CostModel, run_backtest
from strategies import StrategyParams, run_strategy_config


def _base_frame(n=7):
    dt = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "datetime": dt,
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0, 98.0, 99.0, 99.0, 99.0, 99.0, 99.0],
        "close": [100.0] * n,
        "volume": [1000.0] * n,
        "long_entry": [True, False, True, False, True, False, False],
        "short_entry": [False] * n,
        "long_entry_reasons": ["組合1：布林", "", "組合1：布林", "", "組合1：布林", "", ""],
        "long_entry_group_1": [True, False, True, False, True, False, False],
        "long_entry_group_reason_1": ["組合1：布林", "", "組合1：布林", "", "組合1：布林", "", ""],
        # 第3根（index=3）才出現出場後新的下軌向下穿越事件。
        "bollinger_reentry_reset_long": [False, False, False, True, False, False, False],
        "atr": [1.0] * n,
        "macd_hist": [0.0] * n,
    })


def _params():
    p = StrategyParams()
    p.direction = "long"
    p.use_fixed_stop = True
    p.stop_threshold_mode = "points"
    p.stop_points = 1.0
    p.use_chandelier = False
    p.use_profit_tier_chandelier = False
    p.use_macd_reverse = False
    p.use_take_profit = False
    p.use_trailing_stop = False
    p.use_signal_exit = False
    p.use_dynamic_position_sizing = False
    p.use_regime_position_sizing = False
    p.use_account_margin_model = False
    p.use_bollinger_reentry_reset_after_fixed_stop = True
    p.bollinger_reentry_long_group_indices = (1,)
    return p


def main():
    root = Path(__file__).resolve().parent
    info = json.loads((root / "version.json").read_text(encoding="utf-8"))
    assert info["version"] == "v0.8.6.6"
    assert info["build_id"] == "20260714-4"

    cost = CostModel(point_value=1.0, fee=0.0, slippage_points=0.0, tax_rate=0.0, quantity=1)
    trades, equity = run_backtest(_base_frame(), cost, _params())
    # index0 訊號 -> index1 進場且固定停損；index2 同模組訊號被鎖；
    # index3 新事件解鎖；index4 訊號 -> index5 再進場。
    assert len(trades) == 2, trades
    assert int(trades.iloc[0]["entry_group_index"]) == 1
    assert pd.Timestamp(trades.iloc[1]["entry_date"]) == pd.Timestamp("2026-01-06")
    assert int(equity.iloc[-1]["bollinger_reentry_lock_activations"]) >= 1
    assert int(equity.iloc[-1]["bollinger_reentry_blocked_signals"]) == 1

    # 鎖定布林組時，同日若另一個入口也成立，仍應允許另一組進場。
    df = _base_frame()
    df["long_entry_group_2"] = [False, False, True, False, False, False, False]
    df["long_entry_group_reason_2"] = ["", "", "組合2：其他入口", "", "", "", ""]
    trades2, _ = run_backtest(df, cost, _params())
    assert len(trades2) >= 2
    assert 2 in set(trades2["entry_group_index"].dropna().astype(int))

    # 策略層應輸出各 OR 組合欄與重置事件欄。
    raw = pd.DataFrame({
        "datetime": pd.date_range("2025-01-01", periods=300, freq="D"),
        "open": [100.0] * 300,
        "high": [101.0] * 300,
        "low": [99.0] * 300,
        "close": [100.0] * 300,
        "volume": [1000.0] * 300,
    })
    cfg = {
        "direction": "long",
        "entry_long": [
            {"logic": "AND", "conditions": [{"type": "close_above_ma", "ma_type": "SMA", "period": 20}]},
            {"logic": "AND", "conditions": [{"type": "close_cross_up_bollinger_lower", "period": 20, "std": 2.0}]},
        ],
        "entry_short": {"logic": "AND", "conditions": []},
        "entry_policy": {
            "use_bollinger_reentry_reset_after_fixed_stop": True,
            "bollinger_reentry_long_group_indices": [2],
            "bollinger_reentry_period": 20,
            "bollinger_reentry_std": 2.0,
        },
        "exit": {},
    }
    out = run_strategy_config(raw, cfg)
    for col in ("long_entry_group_1", "long_entry_group_2", "bollinger_reentry_reset_long"):
        assert col in out.columns

    print("PASS 3 v0.8.6.6 cases")
    print("- stop_reentry_lock_and_fresh_event_unlock")
    print("- other_entry_group_remains_available")
    print("- strategy_group_columns_and_reset_signal")


if __name__ == "__main__":
    main()
