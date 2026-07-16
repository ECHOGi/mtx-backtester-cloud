# -*- coding: utf-8 -*-
"""v0.8.8.1 起點敏感度唯一交易日日K修正自我檢查。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtester import CostModel
from batch_utils import apply_position_mode, parse_strategy_batch
from benchmark_00631l import apply_split_adjustment
from rolling_start import run_rolling_start_sensitivity
from synthetic_timeframes import aggregate_full_session_daily


def synthetic_session_bars() -> pd.DataFrame:
    """刻意讓每個trade_date有after_hours與regular兩列，重現v0.8.8.0缺陷。"""
    rng = np.random.default_rng(20260716)
    dates = pd.bdate_range("2015-01-05", "2022-12-30")
    returns = rng.normal(0.00025, 0.011, len(dates))
    daily_close = 9000 * np.exp(np.cumsum(returns))
    prev = np.r_[daily_close[0], daily_close[:-1]]
    rows = []
    for i, d in enumerate(dates):
        night_open = prev[i] * (1 + rng.normal(0, 0.0015))
        night_close = (night_open + daily_close[i]) / 2
        day_open = night_close * (1 + rng.normal(0, 0.001))
        day_close = daily_close[i]
        for session, o, c, order in [
            ("after_hours", night_open, night_close, 0),
            ("regular", day_open, day_close, 1),
        ]:
            high = max(o, c) * (1 + abs(rng.normal(0.003, 0.002)))
            low = min(o, c) * (1 - abs(rng.normal(0.003, 0.002)))
            rows.append({
                "datetime": d,
                "trade_date": d,
                "symbol": "MTX",
                "contract_month": int(d.strftime("%Y%m")),
                "session": session,
                "open": o,
                "high": high,
                "low": low,
                "close": c,
                "volume": int(rng.integers(5000, 30000)),
                "open_interest": int(rng.integers(10000, 50000)) if order else 0,
            })
    return pd.DataFrame(rows)


def benchmark_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(99)
    bm_close = 50 * np.exp(np.cumsum(rng.normal(0.0002, 0.009, len(daily))))
    return apply_split_adjustment(pd.DataFrame({
        "date": daily["trade_date"], "open": bm_close, "high": bm_close,
        "low": bm_close, "close": bm_close, "volume": 1000,
    }))


def main():
    root = Path(__file__).resolve().parent
    batch_path = root / "examples" / "batch_049R_L16_L14_50萬起點敏感度_唯一交易日日K修正.json"
    raw = batch_path.read_text(encoding="utf-8")
    _, items, meta = parse_strategy_batch(raw)
    meta = copy.deepcopy(meta)
    meta["rolling_start_config"]["start_generation"].update({
        "start_from": "2019-01-01", "start_to": "2019-03-31"})
    meta["rolling_start_config"]["horizons"] = [
        {"label": "啟動後1年", "trading_days": 252},
    ]

    sessions = synthetic_session_bars()
    daily = aggregate_full_session_daily(sessions)
    assert len(sessions) == len(daily) * 2
    assert not pd.to_datetime(daily["trade_date"]).duplicated().any()
    benchmark = benchmark_from_daily(daily)
    final_items = [(name, apply_position_mode(cfg, "json", 500000)) for name, cfg in items]
    cost = CostModel(
        point_value=50, fee=20, slippage_points=1, tax_rate=0.00002,
        original_margin_amount=159000, use_margin_call_check=True,
        safety_buffer_amount=341000,
    )

    # 以含日夜盤的session資料執行。
    result_sessions = run_rolling_start_sensitivity(
        sessions, final_items, cost, 500000, meta, benchmark_df=benchmark)
    detail_s = result_sessions["rolling_start"]["detail"].sort_values(
        ["策略名稱", "啟動日", "觀察期限"]).reset_index(drop=True)

    # 以預先聚合的完整日K執行，結果必須完全相同。
    result_daily = run_rolling_start_sensitivity(
        daily, final_items, cost, 500000, meta, benchmark_df=benchmark)
    detail_d = result_daily["rolling_start"]["detail"].sort_values(
        ["策略名稱", "啟動日", "觀察期限"]).reset_index(drop=True)

    assert len(detail_s) == 6, f"預期6列，實際{len(detail_s)}列"
    assert set(detail_s["觀察交易日"]) == {252}
    for _, row in detail_s.iterrows():
        start = pd.Timestamp(row["啟動日"])
        end = pd.Timestamp(row["期末日"])
        span = (end - start).days
        assert 330 <= span <= 390, f"252唯一交易日日曆跨度異常：{start.date()}->{end.date()}={span}天"
    cols = [
        "策略名稱", "啟動日", "觀察期限", "觀察交易日", "期末日",
        "期末資產(元)", "最低帳戶權益(元)", "最大回撤率(%)", "交易次數",
        "同期00631L期末資產(元)",
    ]
    pd.testing.assert_frame_equal(detail_s[cols], detail_d[cols], check_dtype=False)
    assert detail_s["同期00631L期末資產(元)"].notna().all()
    assert not result_sessions["rolling_start"]["benchmark_summary"].empty
    print("v0.8.8.1 self-check: PASS")
    print(f"rows={len(detail_s)}, starts={result_sessions['rolling_start']['start_count']}")
    print("252 trading-day spans:", sorted(set(
        (pd.to_datetime(detail_s['期末日']) - pd.to_datetime(detail_s['啟動日'])).dt.days.tolist()
    )))


if __name__ == "__main__":
    main()
