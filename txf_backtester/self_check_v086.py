# -*- coding: utf-8 -*-
"""v0.8.6 長回測續跑、進度節流與新指標按需計算專項自檢。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from backtester import CostModel
from checkpointing import append_rows, read_rows
from future_scenarios import ScenarioConfig, run_cutoff_scenarios
from strategies import StrategyParams, add_indicator_columns, run_strategy_config


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

    # 1. 未使用新指標時，不再無條件建立SAR／BIAS／寶塔線／缺口欄位。
    df = _daily(100)
    p = StrategyParams(use_sar_exit=False)
    out = add_indicator_columns(df, p)
    assert not {"sar", "bias", "tower_color", "open_gap_pct"}.intersection(out.columns)
    p2 = StrategyParams(use_sar_exit=True)
    out2 = add_indicator_columns(df, p2)
    assert {"sar", "sar_stop_long", "sar_stop_short"}.issubset(out2.columns)
    passed.append("lazy_new_indicator_columns")

    # 2. 條件仍可按需計算，且同一資料框重複使用不改結果。
    cfg = {
        "direction": "long",
        "entry_long": {"logic": "AND", "conditions": [
            {"type": "sar_bullish"}, {"type": "bias_above", "period": 20, "value": -100}
        ]},
        "entry_short": {"logic": "AND", "conditions": []},
        "exit": {"use_sar_exit": False, "use_chandelier": False,
                 "use_macd_reverse": False, "use_fixed_stop": False},
    }
    sig = run_strategy_config(df, cfg)
    assert "long_entry" in sig and sig["long_entry"].dtype == bool
    passed.append("on_demand_condition_indicators")

    # 3. JSONL尾端半行不破壞既有檢查點。
    with tempfile.TemporaryDirectory() as td:
        cp = Path(td) / "checkpoint.jsonl"
        append_rows(cp, [{"a": 1}, {"a": 2}])
        with cp.open("a", encoding="utf-8") as f:
            f.write('{"a":')
        loaded = read_rows(cp)
        assert loaded["a"].tolist() == [1, 2]
    passed.append("checkpoint_truncated_tail_safe")

    # 4. 中途停止後可從已完成列接續，最終與一次完成的seed與列數一致。
    daily = _daily(420)
    strategy = {
        "name": "resume_test", "symbol": "MTX", "timeframe": "1D", "direction": "long",
        "entry_long": {"logic": "AND", "conditions": [{"type": "close_breakout_high", "lookback": 20}]},
        "entry_short": {"logic": "AND", "conditions": []},
        "exit": {"use_chandelier": False, "use_macd_reverse": False,
                 "use_fixed_stop": True, "stop_threshold_mode": "entry_atr",
                 "stop_atr_multiple": 1.5, "use_take_profit": False,
                 "use_trailing_stop": False, "use_signal_exit": False},
    }
    items = [(strategy["name"], strategy)]
    config = ScenarioConfig(paths_per_state=1, min_future_days=30, max_future_days=30, seed=12345)
    partial_rows = []

    class IntentionalStop(RuntimeError):
        pass

    def stop_after_three(chunk, done, total):
        partial_rows.extend(chunk)
        if done >= 3:
            raise IntentionalStop("test interruption")

    try:
        run_cutoff_scenarios(daily, items, CostModel(), 500000,
                             [daily["trade_date"].iloc[-1]], config=config,
                             checkpoint_callback=stop_after_three, checkpoint_every=1)
    except IntentionalStop:
        pass
    assert len(partial_rows) >= 3
    resumed = run_cutoff_scenarios(
        daily, items, CostModel(), 500000, [daily["trade_date"].iloc[-1]], config=config,
        resume_distribution=pd.DataFrame(partial_rows), checkpoint_every=1)
    fresh = run_cutoff_scenarios(
        daily, items, CostModel(), 500000, [daily["trade_date"].iloc[-1]], config=config,
        checkpoint_every=1)
    r = resumed["distribution"].sort_values(["未來情境", "路徑編號", "策略名稱"]).reset_index(drop=True)
    f = fresh["distribution"].sort_values(["未來情境", "路徑編號", "策略名稱"]).reset_index(drop=True)
    assert len(r) == 6 and len(r.drop_duplicates(["截止日", "未來情境", "路徑編號", "seed", "策略名稱"])) == 6
    assert r[["未來情境", "seed", "策略期末總權益(元)"]].equals(
        f[["未來情境", "seed", "策略期末總權益(元)"]])
    passed.append("scenario_resume_matches_fresh_run")

    print(f"PASS {len(passed)} v0.8.6 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
