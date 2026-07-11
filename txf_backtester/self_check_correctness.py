# -*- coding: utf-8 -*-
"""合成資料正確性測試。執行：python self_check_correctness.py"""
import pandas as pd

from backtester import CostModel, run_backtest
from correctness import summarize_validation, validate_trades
from strategies import StrategyParams


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

    # 19. 舊版固定金額分段仍可使用，確保歷史策略相容。
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

    print("PASS", len(passed), "cases")
    for name in passed:
        print("-", name)


if __name__ == "__main__":
    main()
