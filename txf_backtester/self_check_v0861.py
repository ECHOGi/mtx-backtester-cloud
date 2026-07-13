# -*- coding: utf-8 -*-
"""v0.8.6.1 500筆檢查點與20組壓力測試專項自檢。"""
from __future__ import annotations

import json
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
    assert 'APP_VERSION = "v0.8.6.1"' in app_text
    assert 'checkpoint_every=500' in app_text
    assert 'checkpoint_every: int = 500' in future_text
    passed.append("checkpoint_interval_500_configured")

    # 少於500筆時，正常結束仍須補寫尾批，不能因門檻未到而遺失。
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
        checkpoint_callback=capture, checkpoint_every=500)
    assert len(result["distribution"]) == 6
    assert len(chunks) == 1 and len(chunks[0][0]) == 6
    passed.append("final_tail_flush_below_500")

    batch_path = Path("/mnt/data/batch_029_NX01_NX02_Claude新指標_20組壓力測試.json")
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    assert batch["required_platform_version"] == "v0.8.6.1"
    assert len(batch["strategies"]) == 20
    assert batch["strategies"][0]["name"].startswith("00_NX01")
    assert batch["strategies"][-1]["name"].startswith("19_CL06")
    passed.append("merged_pressure_batch_20_strategies")

    print(f"PASS {len(passed)} v0.8.6.1 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
