# -*- coding: utf-8 -*-
"""v0.8.1 專項回歸檢查：安全、複利開關、純1D快速模式與摘要欄位。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from backtester import CostModel, _safe_capital_position_spec, run_backtest
from monte_carlo_batch import run_batch_monte_carlo
from strategies import StrategyParams


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_compounding_switch():
    base = dict(
        position_sizing_capital=500000,
        position_safe_capital_per_micro_unit=100000,
        position_max_micro_units=20,
        position_max_small_contracts=4,
        position_small_point_value=50,
        position_micro_point_value=10,
        position_small_margin=159000,
        position_micro_margin=32000,
        position_small_maintenance_margin=122000,
        position_micro_maintenance_margin=24400,
        position_small_fee=20,
        position_micro_fee=12,
        position_min_cash_buffer=0,
        position_stress_multiple=1,
        position_gap_stress_points=0,
        position_drawdown_reserve_fraction=0,
        position_use_stress_capital_check=False,
        position_sizing_mode="dynamic_safe_capital",
    )
    legacy = _safe_capital_position_spec(
        100, SimpleNamespace(**base, position_compounding=False),
        realized=500000, cost=CostModel())
    compound = _safe_capital_position_spec(
        100, SimpleNamespace(**base, position_compounding=True),
        realized=500000, cost=CostModel())
    check(legacy["micro_units"] == 5, "關閉複利時不應使用獲利加口")
    check(compound["micro_units"] == 10, "開啟複利時應使用獲利加口")
    check(legacy["position_equity_basis"] == "legacy_no_profit_compounding", "舊口徑標示錯誤")
    check(compound["position_equity_basis"] == "compounding", "複利口徑標示錯誤")


def test_margin_call_stops_future_entries():
    df = pd.DataFrame([
        {"datetime": "2025-01-01", "open": 95, "high": 98, "low": 94, "close": 97,
         "long_entry": True, "short_entry": False},
        {"datetime": "2025-01-02", "open": 100, "high": 101, "low": 89, "close": 91,
         "long_entry": True, "short_entry": False},
        {"datetime": "2025-01-03", "open": 100, "high": 105, "low": 99, "close": 104,
         "long_entry": True, "short_entry": False},
        {"datetime": "2025-01-04", "open": 105, "high": 110, "low": 104, "close": 109,
         "long_entry": False, "short_entry": False},
    ])
    df["datetime"] = pd.to_datetime(df["datetime"])
    p = StrategyParams(
        direction="long", use_fixed_stop=False, use_take_profit=False,
        use_trailing_stop=False, use_chandelier=False, use_macd_reverse=False,
        use_signal_exit=False, stop_trading_after_margin_call=True)
    cost = CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0,
                     use_margin_call_check=True, safety_buffer_amount=500,
                     original_margin_amount=159000)
    trades, equity = run_backtest(df, cost, p)
    check(len(trades) == 1, "斷頭後仍產生新交易")
    check(trades.iloc[0]["exit_reason"] == "margin_call", "未觸發斷頭出場")
    check(bool(equity.iloc[-1]["account_disabled"]), "斷頭後帳戶未停用")


def make_daily_sessions(n=80):
    dates = pd.bdate_range("2024-01-02", periods=n)
    rows = []
    price = 17000.0
    for i, dt in enumerate(dates):
        close = price + (i % 9 - 4) * 8 + i * 2
        rows.append({
            "datetime": dt + pd.Timedelta(hours=13, minutes=45),
            "trade_date": dt.normalize(), "symbol": "MTX", "contract_month": "202412",
            "session": "regular", "open": price, "high": max(price, close) + 30,
            "low": min(price, close) - 30, "close": close, "volume": 1000 + i,
            "open_interest": 50000,
        })
        price = close
    return pd.DataFrame(rows)


def test_pure_1d_fast_mode_and_summary_fields():
    cfg = {
        "name": "pure1d", "symbol": "MTX", "timeframe": "1D", "direction": "long",
        "entry_long": {"logic": "AND", "conditions": [{"type": "close_above_ma", "period": 10}]},
        "entry_short": {"logic": "AND", "conditions": []},
        "exit": {"use_fixed_stop": True, "stop_points": 200, "use_chandelier": False,
                 "use_macd_reverse": False, "position_sizing_mode": "fixed"},
    }
    cost = CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0,
                     use_margin_call_check=True, safety_buffer_amount=341000,
                     original_margin_amount=159000)
    result = run_batch_monte_carlo(make_daily_sessions(), [("pure1d", cfg)], cost,
                                   [1, 2, 3, 4, 5], 500000)
    check(result["deterministic_1d_fast_mode"], "純1D未啟用快速模式")
    check(result["seeds"] == [1], "純1D未縮為單一路徑")
    check(len(result["requested_seeds"]) == 5, "原要求路徑未保留")
    cols = set(result["comparison"].columns)
    check("期末強制平倉損益中位數" in cols, "比較表缺期末強平欄位")
    check("歷史最低運作資金中位數" in cols, "比較表缺最低運作資金欄位")
    dist_cols = set(result["distribution"].columns)
    check("策略名稱" in dist_cols and "總損益(元)" in dist_cols, "分布表未統一中文欄名")


def test_app_enables_margin_check():
    text = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    check("use_margin_call_check=bool(use_margin_call_check)" in text,
          "app建立CostModel時未傳入斷頭檢查")
    check("safety_buffer_amount=safety_buffer_amount" in text,
          "app建立CostModel時未傳入安全緩衝")


def main():
    tests = [
        test_compounding_switch,
        test_margin_call_stops_future_entries,
        test_pure_1d_fast_mode_and_summary_fields,
        test_app_enables_margin_check,
    ]
    passed = []
    for test in tests:
        test()
        passed.append(test.__name__)
    print(f"PASS {len(passed)} v0.8.1 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
