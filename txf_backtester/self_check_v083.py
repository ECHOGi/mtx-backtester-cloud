# -*- coding: utf-8 -*-
"""v0.8.3 正二分割、未來情境與說明專項自檢。"""
from pathlib import Path

import numpy as np
import pandas as pd

from benchmark_00631l import (apply_split_adjustment, benchmark_metrics,
                              historical_buy_hold_curve)
from backtester import CostModel, _safe_capital_position_spec
from types import SimpleNamespace
from future_scenarios import SCENARIO_STATES, generate_future_daily


def _benchmark_raw():
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-03-23", "2026-03-24", "2026-03-31", "2026-04-01"]),
        "open": [450.0, 458.0, 20.70, 20.90],
        "high": [455.0, 460.0, 21.10, 21.20],
        "low": [448.0, 455.0, 20.50, 20.80],
        "close": [452.0, 458.75, 20.85, 21.00],
        "volume": [1000, 1000, 22000, 18000],
    })


def _daily_library(n=520):
    rng = np.random.default_rng(123)
    dates = pd.bdate_range("2022-01-03", periods=n)
    ret = rng.normal(0.0004, 0.012, n)
    # 注入上漲、盤整、下跌與崩跌反彈段落，確保六種分類都有資料。
    ret[80:140] += 0.002
    ret[160:210] *= 0.25
    ret[230:290] -= 0.0015
    ret[320:335] -= 0.035
    ret[335:355] += 0.025
    close = 15000 * np.cumprod(1 + ret)
    gap = rng.normal(0, 0.003, n)
    open_ = np.r_[close[0], close[:-1]] * (1 + gap)
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.012, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.012, n))
    return pd.DataFrame({
        "datetime": dates + pd.Timedelta(hours=13, minutes=45),
        "trade_date": dates, "symbol": "MTX", "contract_month": "TEST",
        "session": "full_session", "timeframe": "1D",
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.integers(10000, 50000, n), "open_interest": 100000,
    })


def main():
    passed = []

    raw = _benchmark_raw()
    adj = apply_split_adjustment(raw)
    # 分割前458.75 / 22 = 20.85227，與分割後20.85連續，不能出現-95%。
    split_return = float(adj.loc[adj["date"] == pd.Timestamp("2026-03-31"), "adjusted_return"].iloc[0])
    assert abs(split_return) < 0.01, split_return
    passed.append("split_adjusted_return_continuity")

    curve = historical_buy_hold_curve(raw, 500000.0, buy_fee_rate=0.0)
    before = curve.loc[curve["date"] == pd.Timestamp("2026-03-24")].iloc[0]
    after = curve.loc[curve["date"] == pd.Timestamp("2026-03-31")].iloc[0]
    assert int(after["shares"]) == int(before["shares"]) * 22, (before, after)
    assert abs(float(after["account_value"]) / float(before["account_value"]) - 1.0) < 0.01
    passed.append("split_multiplies_shares_not_loss")

    m = benchmark_metrics(curve, 500000.0)
    assert m["最大回撤率(%)"] > -5.0, m
    passed.append("benchmark_metrics_no_fake_crash")

    post = raw[raw["date"] >= pd.Timestamp("2026-03-31")].copy()
    post_curve = historical_buy_hold_curve(post, 500000.0, buy_fee_rate=0.0)
    expected_shares = int(500000.0 // float(post["close"].iloc[0]))
    assert int(post_curve["shares"].iloc[0]) == expected_shares
    passed.append("post_split_start_not_multiplied_again")

    p = SimpleNamespace(
        position_sizing_capital=1_000_000_000.0, position_compounding=True,
        position_safe_capital_per_micro_unit=100_000.0,
        position_min_cash_buffer=0.0, position_drawdown_reserve_fraction=0.1,
        position_gap_stress_points=500.0, position_stress_multiple=4.0,
        position_use_stress_capital_check=True, position_max_micro_units=0,
        position_max_small_contracts=0, position_sizing_mode="dynamic_safe_capital",
        position_contract_mix_mode="min_contract_count", position_max_contract_point_value=200.0,
        position_micro_point_value=10.0, position_small_point_value=50.0,
        position_large_point_value=200.0, position_micro_margin=32000.0,
        position_small_margin=159000.0, position_large_margin=636000.0,
        position_micro_maintenance_margin=24400.0, position_small_maintenance_margin=122000.0,
        position_large_maintenance_margin=488000.0, position_micro_fee=12.0,
        position_small_fee=20.0, position_large_fee=50.0)
    spec = _safe_capital_position_spec(100.0, p, realized=0.0, cost=CostModel())
    assert spec is not None and spec["micro_units"] > 1000
    passed.append("large_compound_position_uses_binary_search")

    library = _daily_library()
    for state in SCENARIO_STATES:
        a, src_a = generate_future_daily(library, float(library["close"].iloc[-1]),
                                         library["trade_date"].iloc[-1], state, 80, 77)
        b, src_b = generate_future_daily(library, float(library["close"].iloc[-1]),
                                         library["trade_date"].iloc[-1], state, 80, 77)
        assert len(a) == 80 and len(src_a) == 80
        assert (a["high"] >= a[["open", "close"]].max(axis=1)).all()
        assert (a["low"] <= a[["open", "close"]].min(axis=1)).all()
        pd.testing.assert_frame_equal(a, b)
        assert src_a == src_b
    passed.append("six_states_valid_and_reproducible")

    app_text = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    for text in ("盤中模擬情境數", "期末強制平倉損益", "期末總權益超越正二比例", "help="):
        assert text in app_text, text
    passed.append("ui_help_and_result_explanations_present")

    print(f"PASS {len(passed)} v0.8.3 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
