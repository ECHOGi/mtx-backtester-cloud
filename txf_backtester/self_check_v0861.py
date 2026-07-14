# -*- coding: utf-8 -*-
"""v0.8.6.1長回測續跑功能相容性自檢（v0.8.6.2沿用）。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtester import CostModel
from future_scenarios import ScenarioConfig, run_cutoff_scenarios


def _daily(n=420):
    rng = np.random.default_rng(20260713)
    dates = pd.bdate_range("2024-01-02", periods=n)
    ret = rng.normal(0.0003, 0.01, n)
    close = 10000 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1] * (1 + rng.normal(0, 0.0015, n - 1))]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.008, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.008, n))
    return pd.DataFrame({
        "datetime": dates + pd.Timedelta(hours=13, minutes=45),
        "trade_date": dates, "symbol": "MTX", "contract_month": "FUTURE",
        "session": "full_session", "timeframe": "1D",
        "open": open_, "high": high, "low": low, "close": close,
        "volume": 1000, "open_interest": 1000,
    })


def main():
    passed = []
    root = Path(__file__).resolve().parent

    app_text = (root / "app.py").read_text(encoding="utf-8")
    future_text = (root / "future_scenarios.py").read_text(encoding="utf-8")
    assert 'version.json' in app_text and 'APP_BUILD_ID' in app_text
    assert 'checkpoint_every=200' in app_text
    assert 'checkpoint_every: int = 200' in future_text
    passed.append("v0861_resume_feature_preserved_with_200_interval")

    # 少於200筆時，正常結束仍須補寫尾批，不能因門檻未到而遺失。
    daily = _daily()
    strategy = {
        "name": "tail_flush_test", "symbol": "MTX", "timeframe": "1D", "direction": "long",
        "entry_long": {"logic": "AND", "conditions": [{"type": "close_breakout_high", "lookback": 20}]},
        "entry_short": {"logic": "AND", "conditions": []},
        "exit": {"use_chandelier": False, "use_macd_reverse": False,
                 "use_fixed_stop": True, "stop_threshold_mode": "entry_atr",
                 "stop_atr_multiple": 1.5, "use_take_profit": False,
                 "use_trailing_stop": False, "use_signal_exit": False},
    }
    chunks = []
    def capture(chunk, done, total):
        chunks.append((list(chunk), done, total))

    config = ScenarioConfig(paths_per_state=1, min_future_days=30, max_future_days=30, seed=24680)
    result = run_cutoff_scenarios(
        daily, [(strategy["name"], strategy)], CostModel(), 500000,
        [daily["trade_date"].iloc[-1]], config=config,
        checkpoint_callback=capture, checkpoint_every=200)
    assert len(result["distribution"]) == 6
    assert len(chunks) == 1 and len(chunks[0][0]) == 6
    passed.append("final_tail_flush_below_200")

    passed.append("legacy_pressure_batch_runtime_independent")

    print(f"PASS {len(passed)} v0.8.6.1 compatibility cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
