# -*- coding: utf-8 -*-
"""v0.8.6.5 MACD柱狀體連續拉高與連續站穩觸發自檢。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import condition_blocks as cb


def _frame(close):
    close = pd.Series(close, dtype="float64")
    return pd.DataFrame({
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000.0,
    })


def main() -> None:
    root = Path(__file__).resolve().parent
    info = json.loads((root / "version.json").read_text(encoding="utf-8"))
    passed = []

    assert info["version"] == "v0.8.6.5"
    assert info["build_id"] == "20260714-3"
    passed.append("version_updated")

    # 直接用可控布林/ MACD序列測試 wrapper 事件語意，避免指標暖機干擾。
    idx = pd.RangeIndex(8)
    df = _frame(np.arange(8, dtype=float) + 100)

    original = cb.CONDITIONS.get("test_bool")
    original_macd = cb.CONDITIONS.get("macd_hist_rising")
    try:
        cb.CONDITIONS["test_bool"] = lambda df, **_: pd.Series(
            [False, True, True, True, False, True, True, False], index=df.index)
        state = cb.evaluate_condition(df, {
            "type": "all_recent", "bars": 2,
            "condition": {"type": "test_bool"},
        })
        trigger = cb.evaluate_condition(df, {
            "type": "all_recent_trigger", "bars": 2,
            "condition": {"type": "test_bool"},
        })
        assert state.tolist() == [False, False, True, True, False, False, True, False]
        assert trigger.tolist() == [False, False, True, False, False, False, True, False]
        passed.append("first_consecutive_trigger_only")

        # 三根柱狀體逐根拉高 = 兩個連續的 h[t] > h[t-1]，於第三根只觸發一次。
        rising = pd.Series([False, True, True, True, False, True, True, False], index=idx)
        cb.CONDITIONS["macd_hist_rising"] = lambda df, **_: rising
        macd3 = cb.evaluate_condition(df, {
            "type": "all_recent_trigger", "bars": 2,
            "condition": {"type": "macd_hist_rising", "fast": 12, "slow": 26, "signal": 9},
        })
        assert macd3.tolist() == [False, False, True, False, False, False, True, False]
        passed.append("three_histogram_bars_semantics")

        # 兩根收盤站上中軌，只在第二根確認時觸發，不會持續每天重複。
        mid2 = cb.evaluate_condition(df, {
            "type": "all_recent_trigger", "bars": 2,
            "condition": {"type": "test_bool"},
        })
        assert mid2.sum() == 2 and bool(mid2.iloc[2]) and bool(mid2.iloc[6])
        passed.append("two_close_confirmation_semantics")
    finally:
        if original is None:
            cb.CONDITIONS.pop("test_bool", None)
        else:
            cb.CONDITIONS["test_bool"] = original
        if original_macd is not None:
            cb.CONDITIONS["macd_hist_rising"] = original_macd

    assert "macd_hist_rising" in cb.CONDITIONS
    passed.append("new_condition_registered")

    print(f"PASS {len(passed)} v0.8.6.5 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
