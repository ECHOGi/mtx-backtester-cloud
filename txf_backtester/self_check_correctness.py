# -*- coding: utf-8 -*-
"""合成資料正確性測試。執行：python self_check_correctness.py"""
import pandas as pd

from backtester import CostModel, run_backtest
from correctness import summarize_validation, validate_trades
from strategies import StrategyParams
from condition_blocks import evaluate_condition


def base_params(**kw):
    p = StrategyParams(
        use_chandelier=False,
        use_macd_reverse=False,
        use_fixed_stop=False,
        use_take_profit=False,
        use_trailing_stop=False,
    )
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def df_from_rows(rows):
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["long_entry", "short_entry"]:
        if c not in df:
            df[c] = False
    for c in ["volume", "open_interest"]:
        if c not in df:
            df[c] = 1
    return df


def run_case(name, rows, p, expected, cost=None):
    if cost is None:
        cost = CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0, quantity=1)
    df = df_from_rows(rows)
    trades, _ = run_backtest(df, cost, p)
    assert len(trades) == 1, f"{name}: expected 1 trade, got {len(trades)}"
    t = trades.iloc[0]
    for k, v in expected.items():
        actual = t[k]
        if isinstance(v, float):
            assert abs(float(actual) - v) < 1e-9, f"{name}: {k} actual={actual}, expected={v}"
        else:
            assert actual == v, f"{name}: {k} actual={actual}, expected={v}"
    checks = validate_trades(df, trades, cost, p)
    summary = summarize_validation(checks)
    assert summary["failed_checks"] == 0, f"{name}: validation failed\n{checks[checks.status=='FAIL']}"
    return name


def main():
    passed = []

    # 1. 下一根開盤進場 + end_of_data 出場
    passed.append(run_case(
        "next_bar_entry",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 110, "low": 99, "close": 108},
        ],
        base_params(),
        {"signal_bar_index": 0, "entry_bar_index": 1, "entry_price": 100.0,
         "exit_price": 108.0, "exit_reason": "end_of_data", "pnl_points": 8.0},
    ))

    # 2. 固定停損
    passed.append(run_case(
        "fixed_stop",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 102, "low": 94, "close": 96},
        ],
        base_params(use_fixed_stop=True, stop_points=5),
        {"entry_price": 100.0, "exit_price": 95.0, "exit_reason": "fixed_stop", "pnl_points": -5.0},
    ))

    # 3. 固定停利
    passed.append(run_case(
        "take_profit",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 112, "low": 99, "close": 106},
        ],
        base_params(use_take_profit=True, take_profit_points=10),
        {"entry_price": 100.0, "exit_price": 110.0, "exit_reason": "take_profit", "pnl_points": 10.0},
    ))

    # 4. 同一根 K 棒同時碰停損/停利：固定停損優先
    passed.append(run_case(
        "same_bar_stop_before_take_profit",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 120, "low": 90, "close": 115},
        ],
        base_params(use_fixed_stop=True, stop_points=5, use_take_profit=True, take_profit_points=10),
        {"entry_price": 100.0, "exit_price": 95.0, "exit_reason": "fixed_stop", "pnl_points": -5.0},
    ))

    # 5. 跳空停損：開盤已跳過停損價，用開盤價成交
    passed.append(run_case(
        "gap_stop_uses_open",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 106, "low": 99, "close": 104},
            {"datetime": "2025-01-03", "open": 90, "high": 92, "low": 88, "close": 89},
        ],
        base_params(use_fixed_stop=True, stop_points=5),
        {"entry_price": 100.0, "exit_price": 90.0, "exit_reason": "fixed_stop", "pnl_points": -10.0},
    ))

    # 6. 移動停損不可偷看當根高點：第 3 根若用當根 high=160 會停在 140；正確應用前一根 high=150，停在 130
    passed.append(run_case(
        "trailing_stop_no_same_bar_lookahead",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 150, "low": 99, "close": 145},
            {"datetime": "2025-01-03", "open": 140, "high": 160, "low": 129, "close": 155},
        ],
        base_params(use_trailing_stop=True, trailing_points=20),
        {"entry_price": 100.0, "exit_price": 130.0, "exit_reason": "trailing_stop", "pnl_points": 30.0},
    ))

    # 7. MACD 反向出場：收盤確認，用收盤價
    passed.append(run_case(
        "macd_reverse_exit",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "macd_hist": 1, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 110, "low": 99, "close": 108, "macd_hist": 1},
            {"datetime": "2025-01-03", "open": 109, "high": 111, "low": 100, "close": 101, "macd_hist": -1},
        ],
        base_params(use_macd_reverse=True),
        {"entry_price": 100.0, "exit_price": 101.0, "exit_reason": "macd_reverse", "pnl_points": 1.0},
    ))

    # 8. 吊燈出場：收盤跌破 chandelier_long，用收盤價
    passed.append(run_case(
        "chandelier_exit",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "chandelier_long": 80, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 110, "low": 99, "close": 108, "chandelier_long": 95},
            {"datetime": "2025-01-03", "open": 109, "high": 111, "low": 100, "close": 94, "chandelier_long": 95},
        ],
        base_params(use_chandelier=True),
        {"entry_price": 100.0, "exit_price": 94.0, "exit_reason": "chandelier", "pnl_points": -6.0},
    ))



    # 9. 條件出場（多單）：use_signal_exit=True 且 exit_long_signal=True，收盤價出場
    passed.append(run_case(
        "signal_exit_long",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 108, "low": 99, "close": 105, "exit_long_signal": True},
        ],
        base_params(use_signal_exit=True),
        {"entry_price": 100.0, "exit_price": 105.0, "exit_reason": "signal_exit", "pnl_points": 5.0},
    ))

    # 10. 條件出場（空單）：use_signal_exit=True 且 exit_short_signal=True，收盤價出場
    passed.append(run_case(
        "signal_exit_short",
        [
            {"datetime": "2025-01-01", "open": 110, "high": 115, "low": 105, "close": 108, "short_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 101, "low": 94, "close": 95, "exit_short_signal": True},
        ],
        base_params(use_signal_exit=True),
        {"entry_price": 100.0, "exit_price": 95.0, "exit_reason": "signal_exit", "pnl_points": 5.0},
    ))

    # 11. 條件出場優先序：同一根固定停損先成立時，不可被 signal_exit 搶先
    passed.append(run_case(
        "signal_exit_after_fixed_stop_priority",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 108, "low": 94, "close": 105, "exit_long_signal": True},
        ],
        base_params(use_fixed_stop=True, stop_points=5, use_signal_exit=True),
        {"entry_price": 100.0, "exit_price": 95.0, "exit_reason": "fixed_stop", "pnl_points": -5.0},
    ))

    # 12. 條件出場優先序：同一根固定停利先成立時，不可被 signal_exit 搶先
    passed.append(run_case(
        "signal_exit_after_take_profit_priority",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 112, "low": 99, "close": 105, "exit_long_signal": True},
        ],
        base_params(use_take_profit=True, take_profit_points=10, use_signal_exit=True),
        {"entry_price": 100.0, "exit_price": 110.0, "exit_reason": "take_profit", "pnl_points": 10.0},
    ))

    # 13. 關閉條件出場：use_signal_exit=False 時，即使訊號為 True 也不觸發 signal_exit
    passed.append(run_case(
        "signal_exit_disabled",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 108, "low": 99, "close": 105, "exit_long_signal": True},
            {"datetime": "2025-01-03", "open": 106, "high": 109, "low": 101, "close": 103, "exit_long_signal": True},
        ],
        base_params(use_signal_exit=False),
        {"entry_price": 100.0, "exit_price": 103.0, "exit_reason": "end_of_data", "pnl_points": 3.0},
    ))



    margin_cost = CostModel(
        point_value=50, fee=0, slippage_points=0, tax_rate=0, quantity=1,
        use_margin_call_check=True, safety_buffer_amount=500, original_margin_amount=159000,
    )

    # 14. 斷頭強制平倉（多單）：安全緩衝金額被吃光，以 margin_call 出場
    passed.append(run_case(
        "margin_call_long",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 102, "low": 89, "close": 91},
        ],
        base_params(),
        {"entry_price": 100.0, "exit_price": 90.0, "exit_reason": "margin_call", "pnl_points": -10.0},
        cost=margin_cost,
    ))

    # 15. 斷頭強制平倉（空單）：安全緩衝金額被吃光，以 margin_call 出場
    passed.append(run_case(
        "margin_call_short",
        [
            {"datetime": "2025-01-01", "open": 110, "high": 115, "low": 105, "close": 108, "short_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 111, "low": 98, "close": 109},
        ],
        base_params(),
        {"entry_price": 100.0, "exit_price": 110.0, "exit_reason": "margin_call", "pnl_points": -10.0},
        cost=margin_cost,
    ))

    # 16. 斷頭優先序：若固定停損先觸發，不能被較遠的 margin_call 搶先
    passed.append(run_case(
        "margin_call_after_fixed_stop_priority",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 102, "low": 89, "close": 91},
        ],
        base_params(use_fixed_stop=True, stop_points=5),
        {"entry_price": 100.0, "exit_price": 95.0, "exit_reason": "fixed_stop", "pnl_points": -5.0},
        cost=margin_cost,
    ))

    # 17. ATR 標準化分段：使用訊號根 ATR，不得偷用進場根尚未完成的 ATR。
    passed.append(run_case(
        "atr_tier_uses_signal_bar_atr",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "atr": 10, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 125, "low": 99, "close": 115,
             "atr": 100, "chandelier_long_m_2_5": 110, "chandelier_long_m_3_5": 116,
             "chandelier_long_m_5_0": 120},
        ],
        base_params(
            use_profit_tier_chandelier=True,
            profit_tier_threshold_mode="entry_atr",
            profit_tier_reference="max_favorable",
            profit_tier_atr_multiples=(2, 4),
            profit_tier_mults=(2.5, 3.5, 5.0),
        ),
        {"entry_price": 100.0, "exit_price": 115.0,
         "exit_reason": "profit_tier_chandelier_3.5", "pnl_points": 15.0,
         "entry_atr": 10.0, "max_favorable_atr_multiple": 2.5},
    ))

    # 18. ATR 門檻達標後排除 MACD 反向，持倉繼續由後續規則決定。
    passed.append(run_case(
        "atr_scaled_macd_exclusion",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "atr": 10, "macd_hist": 1, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 145, "low": 99, "close": 120,
             "atr": 80, "macd_hist": -1},
            {"datetime": "2025-01-03", "open": 121, "high": 150, "low": 119, "close": 130,
             "atr": 90, "macd_hist": -1},
        ],
        base_params(
            use_macd_reverse=True,
            use_profit_scaled_macd_exclusion=True,
            profit_tier_threshold_mode="entry_atr",
            profit_tier_reference="max_favorable",
            macd_reverse_exclude_atr_multiple=4.0,
        ),
        {"entry_price": 100.0, "exit_price": 130.0,
         "exit_reason": "end_of_data", "pnl_points": 30.0,
         "entry_atr": 10.0, "max_favorable_atr_multiple": 5.0},
    ))

    # 19. ATR 停損固定使用訊號根 ATR：10 × 0.75 = 7.5 點。
    passed.append(run_case(
        "atr_stop_uses_signal_bar_atr",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "atr": 10, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 102, "low": 92, "close": 95,
             "atr": 100},
        ],
        base_params(use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=0.75),
        {"entry_price": 100.0, "exit_price": 92.5, "exit_reason": "fixed_stop",
         "pnl_points": -7.5, "entry_atr": 10.0, "planned_stop_points": 7.5,
         "planned_stop_risk_amount": 375.0},
    ))

    # 20. 風險上限未超標時允許進場。
    passed.append(run_case(
        "entry_risk_cap_allows_trade",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "atr": 10, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 110, "low": 99, "close": 108},
        ],
        base_params(use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=1.0,
                    use_entry_risk_cap=True, max_entry_risk_amount=500.0),
        {"entry_price": 100.0, "exit_price": 108.0, "exit_reason": "end_of_data",
         "planned_stop_points": 10.0, "planned_stop_risk_amount": 500.0,
         "entry_risk_cap_amount": 500.0},
    ))

    # 21. 風險上限超標時略過進場，並在權益曲線留下累積次數。
    df_skip = df_from_rows([
        {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
         "atr": 10, "long_entry": True},
        {"datetime": "2025-01-02", "open": 100, "high": 110, "low": 99, "close": 108},
    ])
    p_skip = base_params(use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=1.0,
                         use_entry_risk_cap=True, max_entry_risk_amount=499.0)
    trades_skip, equity_skip = run_backtest(
        df_skip, CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0, quantity=1), p_skip)
    assert trades_skip.empty, "entry_risk_cap_skips_trade: expected no trade"
    assert int(equity_skip["risk_cap_skipped_entries"].iloc[-1]) == 1
    passed.append("entry_risk_cap_skips_trade")

    # 22. 舊版固定金額分段仍可使用，確保歷史策略相容。
    passed.append(run_case(
        "amount_tier_backward_compatible",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "atr": 10, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 125, "low": 99, "close": 115,
             "chandelier_long_m_2_5": 110, "chandelier_long_m_3_5": 116,
             "chandelier_long_m_5_0": 120},
        ],
        base_params(
            use_profit_tier_chandelier=True,
            profit_tier_threshold_mode="amount",
            profit_tier_reference="max_favorable",
            profit_tier_amounts=(1000, 2000),
            profit_tier_mults=(2.5, 3.5, 5.0),
        ),
        {"entry_price": 100.0, "exit_price": 115.0,
         "exit_reason": "profit_tier_chandelier_3.5", "pnl_points": 15.0},
        cost=CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0, quantity=1),
    ))

    # 23. 動態部位：低 ATR 時由 50 萬、4% 風險預算與最大10微台單位，配置2口小台。
    passed.append(run_case(
        "dynamic_position_low_atr_scales_to_two_mtx",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "atr": 10, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 110, "low": 99, "close": 108},
        ],
        base_params(
            use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=0.75,
            use_dynamic_position_sizing=True, position_sizing_capital=500000,
            position_risk_fraction=0.04, position_stress_multiple=4.0,
            position_max_micro_units=10,
        ),
        {"entry_price": 100.0, "exit_price": 108.0, "exit_reason": "end_of_data",
         "quantity": 2.0, "small_quantity": 2, "micro_quantity": 0,
         "position_micro_units": 10, "point_value_total": 100.0,
         "planned_stop_points": 7.5, "planned_stop_risk_amount": 750.0,
         "risk_budget_amount": 20000.0, "stress_risk_amount": 3000.0,
         "pnl_amount": 720.0},
        cost=CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0, quantity=1),
    ))

    # 24. 動態部位：高 ATR 時縮小為2口微台，停損點數不變。
    passed.append(run_case(
        "dynamic_position_high_atr_scales_to_micro",
        [
            {"datetime": "2025-01-01", "open": 9000, "high": 9100, "low": 8900, "close": 9050,
             "atr": 1000, "long_entry": True},
            {"datetime": "2025-01-02", "open": 10000, "high": 10100, "low": 9900, "close": 10050},
        ],
        base_params(
            use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=0.75,
            use_dynamic_position_sizing=True, position_sizing_capital=500000,
            position_risk_fraction=0.04, position_stress_multiple=4.0,
            position_max_micro_units=10,
        ),
        {"entry_price": 10000.0, "exit_price": 10050.0, "exit_reason": "end_of_data",
         "quantity": 0.4, "small_quantity": 0, "micro_quantity": 2,
         "position_micro_units": 2, "point_value_total": 20.0,
         "planned_stop_points": 750.0, "planned_stop_risk_amount": 15000.0,
         "risk_budget_amount": 20000.0, "stress_risk_amount": 60000.0,
         "pnl_amount": 952.0},
        cost=CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0, quantity=1),
    ))

    # 25. 4倍跳空壓力＋保證金合計不可超過50萬：原可10單位時縮為9單位。
    passed.append(run_case(
        "dynamic_position_stress_cap_reduces_units",
        [
            {"datetime": "2025-01-01", "open": 9000, "high": 9100, "low": 8900, "close": 9050,
             "atr": 666.6666666667, "long_entry": True},
            {"datetime": "2025-01-02", "open": 10000, "high": 10100, "low": 9990, "close": 10050},
        ],
        base_params(
            use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=0.75,
            use_dynamic_position_sizing=True, position_sizing_capital=500000,
            position_risk_fraction=0.10, position_stress_multiple=4.0,
            position_max_micro_units=10,
        ),
        {"quantity": 1.8, "small_quantity": 1, "micro_quantity": 4,
         "position_micro_units": 9, "point_value_total": 90.0,
         "planned_stop_points": 500.0, "planned_stop_risk_amount": 45000.0,
         "risk_budget_amount": 50000.0, "stress_risk_amount": 180000.0},
        cost=CostModel(point_value=50, fee=0, slippage_points=0, tax_rate=0, quantity=1),
    ))

    # 26. v0.7.0 核心部位：高 ATR 不會因波動變大而自動縮成微台。
    passed.append(run_case(
        "regime_core_keeps_one_mtx_in_high_atr",
        [
            {"datetime": "2025-01-01", "open": 9000, "high": 9100, "low": 8900, "close": 9050,
             "atr": 1000, "long_entry": True, "long_position_micro_units": 5,
             "long_position_regime": "core"},
            {"datetime": "2025-01-02", "open": 10000, "high": 10100, "low": 9990, "close": 10050},
        ],
        base_params(
            use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=0.75,
            use_regime_position_sizing=True, position_sizing_capital=500000,
            position_stress_multiple=4.0, position_max_micro_units=10,
        ),
        {"quantity": 1.0, "small_quantity": 1, "micro_quantity": 0,
         "position_micro_units": 5, "position_regime": "core",
         "planned_stop_points": 750.0, "planned_stop_risk_amount": 37500.0},
        cost=CostModel(point_value=50, fee=20, slippage_points=0, tax_rate=0, quantity=1),
    ))

    # 27. 強趨勢條件要求第二層部位，低風險時可配置2口小台。
    passed.append(run_case(
        "regime_addon_scales_to_two_mtx",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "atr": 10, "long_entry": True, "long_position_micro_units": 10,
             "long_position_regime": "core+addon"},
            {"datetime": "2025-01-02", "open": 100, "high": 110, "low": 99, "close": 108},
        ],
        base_params(
            use_fixed_stop=True, stop_threshold_mode="entry_atr", stop_atr_multiple=0.75,
            use_regime_position_sizing=True, position_sizing_capital=500000,
            position_stress_multiple=4.0, position_max_micro_units=10,
        ),
        {"quantity": 2.0, "small_quantity": 2, "position_micro_units": 10,
         "position_regime": "core+addon"},
        cost=CostModel(point_value=50, fee=20, slippage_points=0, tax_rate=0, quantity=1),
    ))

    # 28. 最短持有期：MACD 反向在持有滿3根前不得讓長期部位離場。
    passed.append(run_case(
        "minimum_holding_bars_delays_macd_exit",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92,
             "macd_hist": 1, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 105, "low": 99, "close": 103, "macd_hist": -1},
            {"datetime": "2025-01-03", "open": 103, "high": 106, "low": 101, "close": 102, "macd_hist": -1},
            {"datetime": "2025-01-04", "open": 102, "high": 104, "low": 100, "close": 101, "macd_hist": -1},
        ],
        base_params(use_macd_reverse=True, minimum_holding_bars=2),
        {"exit_price": 101.0, "exit_reason": "macd_reverse", "holding_bars": 3},
    ))

    # 29. 收盤出場連續確認：條件需連續2根才離場。
    passed.append(run_case(
        "signal_exit_requires_two_confirmations",
        [
            {"datetime": "2025-01-01", "open": 90, "high": 95, "low": 85, "close": 92, "long_entry": True},
            {"datetime": "2025-01-02", "open": 100, "high": 105, "low": 99, "close": 103, "exit_long_signal": True},
            {"datetime": "2025-01-03", "open": 103, "high": 106, "low": 101, "close": 102, "exit_long_signal": True},
        ],
        base_params(use_signal_exit=True, signal_exit_confirmation_bars=2),
        {"exit_price": 102.0, "exit_reason": "signal_exit", "holding_bars": 2},
    ))

    # 30. 通用巢狀條件 all_recent：最近3根皆成立才為真。
    df_cond = df_from_rows([
        {"datetime": "2025-01-01", "open": 10, "high": 11, "low": 9, "close": 10},
        {"datetime": "2025-01-02", "open": 11, "high": 12, "low": 10, "close": 11},
        {"datetime": "2025-01-03", "open": 12, "high": 13, "low": 11, "close": 12},
        {"datetime": "2025-01-04", "open": 13, "high": 14, "low": 12, "close": 13},
    ])
    cond = evaluate_condition(df_cond, {
        "type": "all_recent", "bars": 3,
        "condition": {"type": "ma_slope_up", "ma_type": "SMA", "period": 1},
    })
    assert bool(cond.iloc[-1]) is True and bool(cond.iloc[1]) is False
    passed.append("generic_all_recent_condition")

    print("PASS", len(passed), "cases")
    for name in passed:
        print("-", name)


if __name__ == "__main__":
    main()
