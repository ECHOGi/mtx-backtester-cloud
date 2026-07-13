# -*- coding: utf-8 -*-
"""v0.8.6.2 200筆檢查點與回撤煞車觀察欄位專項自檢。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtester import CostModel
from future_scenarios import ScenarioConfig, run_cutoff_scenarios
from metrics import compute_metrics


def _daily(n: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(20260713)
    dates = pd.bdate_range("2024-01-02", periods=n)
    ret = rng.normal(0.0003, 0.01, n)
    close = 10000 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1] * (1 + rng.normal(0, 0.0015, n - 1))]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.008, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.008, n))
    return pd.DataFrame({
        "datetime": dates + pd.Timedelta(hours=13, minutes=45),
        "trade_date": dates,
        "symbol": "MTX",
        "contract_month": "FUTURE",
        "session": "full_session",
        "timeframe": "1D",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "open_interest": 1000,
    })


def main() -> None:
    passed: list[str] = []
    root = Path(__file__).resolve().parent

    app_text = (root / "app.py").read_text(encoding="utf-8")
    future_text = (root / "future_scenarios.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "v0.8.6.2"' in app_text
    assert "checkpoint_every=200" in app_text
    assert "checkpoint_every: int = 200" in future_text
    assert "每200筆" in app_text and "最多可能重算最近199筆" in app_text
    passed.append("checkpoint_interval_200_configured")

    # 人工序列精確驗證：兩次由非煞車切入煞車，共4/7個交易日處於煞車。
    equity = pd.DataFrame({
        "datetime": pd.bdate_range("2026-01-01", periods=7),
        "equity": [0.0] * 7,
        "daily_drawdown_brake_multiplier": [1.0, 0.9, 0.8, 1.0, 0.7, 0.7, 1.0],
        "daily_realized_equity_drawdown_pct": [0.0, 5.0, 8.0, 0.0, 10.0, 11.0, 0.0],
    })
    trades = pd.DataFrame({
        "pnl_amount": [1000.0],
        "pnl_points": [10.0],
        "holding_bars": [1],
        "exit_reason": ["signal_exit"],
        "quantity": [1.0],
    })
    metrics = compute_metrics(trades, equity, initial_capital=500000)
    assert metrics["回撤煞車觸發次數"] == 2
    assert metrics["煞車狀態交易日數"] == 4
    assert metrics["煞車狀態交易日占比(%)"] == 57.14
    assert metrics["平均每日回撤煞車倍率"] == 0.8714
    assert metrics["最低每日回撤煞車倍率"] == 0.7
    assert metrics["逐日最大已實現權益回撤(%)"] == 11.0
    passed.append("daily_brake_observation_metrics_exact")

    # 少於200筆的正式情境回測仍須在結束時補寫尾批，並輸出新觀察欄位。
    daily = _daily()
    strategy = {
        "name": "v0862_tail_and_brake_test",
        "symbol": "MTX",
        "timeframe": "1D",
        "direction": "long",
        "entry_long": {
            "logic": "AND",
            "conditions": [{"type": "close_breakout_high", "lookback": 20}],
        },
        "entry_short": {"logic": "AND", "conditions": []},
        "exit": {
            "use_chandelier": False,
            "use_macd_reverse": False,
            "use_fixed_stop": True,
            "stop_threshold_mode": "entry_atr",
            "stop_atr_multiple": 1.5,
            "use_take_profit": False,
            "use_trailing_stop": False,
            "use_signal_exit": False,
            "position_sizing_mode": "dynamic_risk",
            "use_dynamic_position_sizing": True,
            "position_sizing_capital": 500000,
            "position_compounding": True,
            "position_risk_fraction": 0.12,
            "position_stress_multiple": 4.0,
            "position_max_micro_units": 60,
            "position_use_stress_capital_check": True,
            "use_drawdown_risk_brake": True,
            "position_drawdown_brake_start_pct": 4.0,
            "position_drawdown_brake_full_pct": 16.0,
            "position_drawdown_brake_floor": 0.45,
        },
    }
    chunks: list[tuple[list[dict], int, int]] = []

    def capture(chunk, done, total):
        chunks.append((list(chunk), int(done), int(total)))

    config = ScenarioConfig(paths_per_state=1, min_future_days=30, max_future_days=30, seed=24680)
    result = run_cutoff_scenarios(
        daily,
        [(strategy["name"], strategy)],
        CostModel(),
        500000,
        [daily["trade_date"].iloc[-1]],
        config=config,
        checkpoint_callback=capture,
        checkpoint_every=200,
    )
    distribution = result["distribution"]
    comparison = result["comparison"]
    assert len(distribution) == 6
    assert len(chunks) == 1 and len(chunks[0][0]) == 6
    for col in (
        "回撤煞車觸發次數",
        "煞車狀態交易日數",
        "煞車狀態交易日占比(%)",
        "平均每日回撤煞車倍率",
        "最低每日回撤煞車倍率",
    ):
        assert col in distribution.columns, col
    for col in (
        "煞車觸發次數中位數",
        "煞車狀態交易日占比中位數(%)",
        "平均每日回撤煞車倍率中位數",
        "最低每日回撤煞車倍率中位數",
    ):
        assert col in comparison.columns, col
    passed.append("scenario_tail_flush_and_brake_columns")

    print(f"PASS {len(passed)} v0.8.6.2 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
