# -*- coding: utf-8 -*-
"""v0.8.4 期末總權益主排名、正二並排欄位與NX回撤煞車專項自檢。"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from backtester import CostModel, _drawdown_risk_brake_multiplier, _dynamic_position_spec
from future_scenarios import ScenarioConfig, run_cutoff_scenarios


def _risk_params(brake=True):
    return SimpleNamespace(
        position_sizing_capital=500000.0,
        position_compounding=True,
        position_risk_fraction=0.12,
        position_stress_multiple=4.0,
        position_max_micro_units=0,
        position_use_stress_capital_check=True,
        position_micro_point_value=10.0,
        position_small_point_value=50.0,
        position_large_point_value=200.0,
        position_micro_margin=1000.0,
        position_small_margin=5000.0,
        position_large_margin=20000.0,
        position_micro_maintenance_margin=800.0,
        position_small_maintenance_margin=4000.0,
        position_large_maintenance_margin=16000.0,
        position_micro_fee=12.0,
        position_small_fee=20.0,
        position_large_fee=50.0,
        position_contract_mix_mode="min_contract_count",
        position_max_contract_point_value=200.0,
        use_drawdown_risk_brake=brake,
        position_drawdown_brake_start_pct=4.0,
        position_drawdown_brake_full_pct=10.0,
        position_drawdown_brake_floor=0.4,
    )


def _daily_library(n=340):
    rng = np.random.default_rng(84)
    dates = pd.bdate_range("2020-01-02", periods=n)
    ret = rng.normal(0.00035, 0.012, n)
    ret[70:115] += 0.002
    ret[130:175] *= 0.25
    ret[200:245] -= 0.0015
    ret[255:270] -= 0.025
    ret[270:292] += 0.018
    close = 12000 * np.cumprod(1 + ret)
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, .002, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(.001, .01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(.001, .01, n))
    return pd.DataFrame({
        "datetime": dates + pd.Timedelta(hours=13, minutes=45),
        "trade_date": dates, "symbol": "MTX", "contract_month": "TEST",
        "session": "full_session", "timeframe": "1D",
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.integers(10000, 50000, n), "open_interest": 100000,
    })


def _benchmark(daily):
    dates = pd.to_datetime(daily["trade_date"])
    underlying = pd.Series(daily["close"].to_numpy(), index=dates)
    returns = underlying.pct_change().fillna(0).clip(-.45, .45)
    close = 50.0 * (1.0 + 2.0 * returns).clip(lower=.05).cumprod().to_numpy()
    out = pd.DataFrame({"date": dates.to_numpy(), "open": close, "high": close,
                        "low": close, "close": close, "volume": 1000})
    out["adjusted_close"] = out["close"]
    out["adjusted_return"] = out["adjusted_close"].pct_change()
    return out


def main():
    passed = []

    p = _risk_params(True)
    mult, dd, peak = _drawdown_risk_brake_multiplier(p, realized=-50000.0, realized_peak_equity=500000.0)
    assert abs(mult - 0.4) < 1e-9 and abs(dd - 10.0) < 1e-9 and peak == 500000.0
    passed.append("drawdown_brake_reaches_floor")

    p_mid = _risk_params(True)
    mult_mid, dd_mid, _ = _drawdown_risk_brake_multiplier(p_mid, realized=-35000.0, realized_peak_equity=500000.0)
    assert abs(dd_mid - 7.0) < 1e-9 and abs(mult_mid - 0.7) < 1e-9
    passed.append("drawdown_brake_linear_interpolation")

    no_brake = _dynamic_position_spec(100.0, _risk_params(False), realized=-50000.0,
                                      cost=CostModel(), realized_peak_equity=500000.0)
    with_brake = _dynamic_position_spec(100.0, _risk_params(True), realized=-50000.0,
                                        cost=CostModel(), realized_peak_equity=500000.0)
    assert no_brake and with_brake
    assert no_brake["micro_units"] > with_brake["micro_units"] >= 1
    assert with_brake["position_sizing_mode"] == "dynamic_risk"
    assert abs(with_brake["drawdown_brake_multiplier"] - 0.4) < 1e-9
    passed.append("dynamic_risk_zero_cap_and_brake")

    daily = _daily_library()
    benchmark = _benchmark(daily)
    cfg = {
        "name": "TEST", "symbol": "MTX", "timeframe": "1D", "direction": "long",
        "entry_long": {"logic": "AND", "conditions": [{"type": "close_breakout_high", "lookback": 20}]},
        "entry_short": [],
        "exit_long_block": {"logic": "OR", "conditions": [{"type": "close_breakdown_low", "lookback": 10}]},
        "exit": {"use_signal_exit": True, "use_fixed_stop": True,
                 "stop_threshold_mode": "entry_atr", "stop_atr_multiple": 1.5,
                 "use_chandelier": False, "use_macd_reverse": False,
                 "position_sizing_mode": "dynamic_risk", "position_compounding": True,
                 "use_dynamic_position_sizing": True, "use_account_margin_model": True,
                 "position_sizing_capital": 500000.0, "position_risk_fraction": .04,
                 "position_max_micro_units": 0, "position_stress_multiple": 4.0,
                 "position_use_stress_capital_check": True,
                 "position_micro_point_value": 10.0, "position_small_point_value": 50.0,
                 "position_large_point_value": 200.0, "position_micro_margin": 32000.0,
                 "position_small_margin": 159000.0, "position_large_margin": 636000.0,
                 "position_micro_maintenance_margin": 24400.0,
                 "position_small_maintenance_margin": 122000.0,
                 "position_large_maintenance_margin": 488000.0,
                 "position_contract_mix_mode": "min_contract_count",
                 "position_max_contract_point_value": 200.0,
                 "position_allow_downsize": True, "stop_trading_after_margin_call": True},
    }
    cost = CostModel(point_value=50, fee=20, slippage_points=1, tax_rate=0,
                     original_margin_amount=159000, use_margin_call_check=True,
                     safety_buffer_amount=341000)
    result = run_cutoff_scenarios(
        daily, [("TEST", cfg)], cost, 500000.0,
        [daily["trade_date"].iloc[-60]], benchmark_df=benchmark,
        config=ScenarioConfig(paths_per_state=1, min_future_days=20, max_future_days=20, seed=84))
    required = {
        "期末總權益超越正二比例(%)", "策略期末總權益中位數", "正二期末資產中位數",
        "策略總權益年化中位數(%)", "正二年化中位數(%)",
        "策略最大回撤率中位數(%)", "正二最大回撤率中位數(%)",
    }
    assert required.issubset(result["comparison"].columns), result["comparison"].columns
    assert result["ranking_basis"] == "共同路徑期末總權益對00631L期末市值"
    assert len(result["distribution"]) == 6
    passed.append("terminal_equity_benchmark_ranking_columns")

    app_text = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    for text in ("options=[1, 2, 3, 5, 10, 15, 20]", "策略期末總權益中位數",
                 "正二期末資產中位數", "相對正二回撤改善", "未自然出場比例只"):
        assert text in app_text, text
    passed.append("ui_twenty_paths_and_paired_benchmark_values")

    print(f"PASS {len(passed)} v0.8.4 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
