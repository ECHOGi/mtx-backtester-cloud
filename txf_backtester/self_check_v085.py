# -*- coding: utf-8 -*-
"""v0.8.5 SAR、BIAS、日K缺口、寶塔線與SAR移動出場專項自檢。"""
import numpy as np
import pandas as pd

import indicators as ind
from backtester import CostModel, run_backtest
from condition_blocks import evaluate_condition, list_conditions
from correctness import summarize_validation, validate_trades
from strategies import StrategyParams, add_indicator_columns, params_from_config


def _df(close, opens=None, highs=None, lows=None):
    close = np.asarray(close, dtype=float)
    n = len(close)
    opens = np.asarray(opens if opens is not None else np.r_[close[0], close[:-1]], dtype=float)
    highs = np.asarray(highs if highs is not None else np.maximum(opens, close) + 1.0, dtype=float)
    lows = np.asarray(lows if lows is not None else np.minimum(opens, close) - 1.0, dtype=float)
    return pd.DataFrame({
        "datetime": pd.bdate_range("2025-01-02", periods=n),
        "open": opens, "high": highs, "low": lows, "close": close,
        "volume": 1000, "open_interest": 1000,
    })


def main():
    passed = []

    # 1. SAR 必須有翻多／翻空，且修改未來資料不影響過去結果。
    d = _df([100, 102, 104, 106, 108, 110, 107, 103, 99, 96, 98, 102, 107])
    sar = ind.parabolic_sar(d)
    assert sar["sar_flip_bearish"].any() and sar["sar_flip_bullish"].any()
    d2 = d.copy()
    d2.loc[10:, ["open", "high", "low", "close"]] *= 1.5
    sar2 = ind.parabolic_sar(d2)
    assert np.allclose(sar.loc[:9, "sar"], sar2.loc[:9, "sar"], equal_nan=True)
    passed.append("sar_flip_and_no_future_leak")

    # 2. BIAS 公式與條件門檻。
    bdf = _df([100, 100, 100, 100, 110])
    b = ind.bias(bdf["close"], 4, "SMA")
    expected = (110 / 102.5 - 1) * 100
    assert abs(float(b.iloc[-1]) - expected) < 1e-9
    assert bool(evaluate_condition(bdf, {"type": "bias_above", "period": 4, "value": 7.0}).iloc[-1])
    passed.append("bias_formula_and_condition")

    # 3. 日K完整缺口建立、存在三根後仍未回補、回補後關閉。
    g = _df(
        [100, 112, 114, 116, 108],
        opens=[100, 110, 113, 115, 109],
        highs=[102, 114, 116, 118, 111],
        lows=[98, 110, 112, 114, 101],
    )
    assert bool(evaluate_condition(g, {"type": "full_gap_up"}).iloc[1])
    unfilled = evaluate_condition(g, {"type": "gap_up_unfilled", "min_age": 2, "lookback": 10})
    assert bool(unfilled.iloc[3]) and not bool(unfilled.iloc[4])
    passed.append("daily_gap_created_unfilled_and_filled")

    # 4. 平台研究版寶塔線三根確認翻黑與翻紅。
    tdf = _df([100, 102, 104, 106, 103, 101, 99, 102, 105, 108])
    tower = ind.tower_line(tdf["close"], 3)
    assert tower["tower_flip_black"].any() and tower["tower_flip_red"].any()
    passed.append("tower_three_bar_reversal")

    # 5. SAR 出場使用預先計算的盤中停損線，並可由 correctness 重算一致。
    rows = _df([100, 105, 110, 108, 104, 100])
    rows["long_entry"] = [True, False, False, False, False, False]
    rows["short_entry"] = False
    p = StrategyParams(use_chandelier=False, use_macd_reverse=False,
                       use_fixed_stop=False, use_take_profit=False,
                       use_trailing_stop=False, use_sar_exit=True)
    enriched = add_indicator_columns(rows, p)
    trades, _ = run_backtest(enriched, CostModel(point_value=50, fee=0, slippage_points=0), p)
    assert len(trades) == 1 and trades.iloc[0]["exit_reason"] in {"sar_stop", "end_of_data"}
    checks = validate_trades(enriched, trades, CostModel(point_value=50, fee=0, slippage_points=0), p)
    assert summarize_validation(checks)["failed_checks"] == 0
    passed.append("sar_exit_and_correctness_replay")

    # 6. JSON欄位與條件註冊完整。
    cfg = {"direction": "long", "entry_long": [], "entry_short": [],
           "exit": {"use_sar_exit": True, "sar_af_start": .02,
                    "sar_af_step": .03, "sar_af_max": .18}}
    pp = params_from_config(cfg)
    assert pp.use_sar_exit and abs(pp.sar_af_step - .03) < 1e-12
    required = {"sar_flip_bullish", "sar_flip_bearish", "bias_above", "bias_below",
                "open_gap_pct_above", "full_gap_up", "gap_up_unfilled",
                "tower_flip_red", "tower_flip_black"}
    assert required.issubset(list_conditions())
    passed.append("json_and_condition_registry")

    print(f"PASS {len(passed)} v0.8.5 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
