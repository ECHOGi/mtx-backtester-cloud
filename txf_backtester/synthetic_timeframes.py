# -*- coding: utf-8 -*-
"""synthetic_timeframes.py - 以日盤/夜盤 OHLC 約束隨機模擬 30 分與 60 分 K。

重要定位：
- 這是「可能路徑」生成器，不是歷史真實盤中資料還原。
- 每個 seed 會產生一套一致的 30 分 K；60 分 K 一律由同一套 30 分 K 聚合，
  避免兩個週期互相矛盾。
- 每個來源時段都嚴格保留 open / high / low / close / volume。
- 所有策略比較應共用相同 seed 集合，並以多路徑分布判斷穩健性。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SimulationConfig:
    base_minutes: int = 30
    regular_bars: int = 10       # 08:45~13:45，共 5 小時
    after_hours_bars: int = 28   # 15:00~次日 05:00，共 14 小時
    bridge_noise: float = 0.18
    intrabar_wick_scale: float = 0.16
    volume_concentration: float = 18.0


def _as_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def _trade_date_series(df: pd.DataFrame) -> pd.Series:
    if "trade_date" in df.columns:
        return pd.to_datetime(df["trade_date"], errors="coerce").dt.normalize()
    if "datetime" in df.columns:
        return pd.to_datetime(df["datetime"], errors="coerce").dt.normalize()
    if "date" in df.columns:
        return pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    raise ValueError("資料需包含 trade_date、datetime 或 date 欄位")


def _previous_trade_date_map(trade_dates: Iterable[pd.Timestamp]) -> dict[pd.Timestamp, pd.Timestamp]:
    dates = sorted({_as_timestamp(x).normalize() for x in trade_dates if pd.notna(x)})
    out: dict[pd.Timestamp, pd.Timestamp] = {}
    prev = None
    for d in dates:
        out[d] = prev if prev is not None else d - pd.Timedelta(days=1)
        prev = d
    return out


def _session_start(trade_date: pd.Timestamp, session: str,
                   previous_trade_date: pd.Timestamp) -> pd.Timestamp:
    session = str(session or "regular").lower()
    if session == "after_hours":
        # 盤後列屬於下一個交易日；用上一個有效交易日 15:00 開始，
        # 自然涵蓋週末與假日，不以固定減一天猜測。
        return previous_trade_date.normalize() + pd.Timedelta(hours=15)
    return trade_date.normalize() + pd.Timedelta(hours=8, minutes=45)


def _anchor_path(open_: float, high: float, low: float, close: float,
                 n_bars: int, rng: np.random.Generator,
                 noise_scale: float) -> np.ndarray:
    """建立 n_bars+1 個邊界價，強制首尾與高低點成立。"""
    if n_bars < 4:
        raise ValueError("模擬 K 棒數至少需為 4")
    lo = min(float(low), float(open_), float(close))
    hi = max(float(high), float(open_), float(close))
    open_ = float(np.clip(open_, lo, hi))
    close = float(np.clip(close, lo, hi))
    high = float(hi)
    low = float(lo)

    # 極端點避開首尾，且保持兩者位置不同。
    candidates = np.arange(1, n_bars)
    first_pos, second_pos = sorted(rng.choice(candidates, size=2, replace=False).tolist())
    if rng.random() < 0.5:
        first_val, second_val = high, low
    else:
        first_val, second_val = low, high
    anchors = [(0, open_), (first_pos, first_val), (second_pos, second_val), (n_bars, close)]

    values = np.empty(n_bars + 1, dtype=float)
    values[:] = np.nan
    total_range = max(high - low, 1e-9)
    for (i0, v0), (i1, v1) in zip(anchors[:-1], anchors[1:]):
        length = i1 - i0
        values[i0] = v0
        values[i1] = v1
        if length <= 1:
            continue
        t = np.arange(1, length, dtype=float) / length
        linear = v0 + (v1 - v0) * t
        # Brownian bridge：兩端噪音歸零。
        increments = rng.normal(0.0, 1.0, size=length)
        walk = np.cumsum(increments)[:-1]
        if len(walk):
            bridge = walk - t * np.cumsum(increments)[-1]
            std = float(np.std(bridge))
            if std > 1e-12:
                bridge = bridge / std
            envelope = np.sqrt(np.maximum(t * (1.0 - t), 0.0))
            linear = linear + bridge * envelope * total_range * float(noise_scale)
        values[i0 + 1:i1] = np.clip(linear, low, high)

    # 浮點保險：高低點、首尾精確落在指定值。
    values[0], values[-1] = open_, close
    values[first_pos], values[second_pos] = first_val, second_val
    return np.clip(values, low, high)


def _split_volume(total_volume: float, n_bars: int, rng: np.random.Generator,
                  concentration: float) -> np.ndarray:
    total = max(float(total_volume or 0.0), 0.0)
    if total <= 0:
        return np.zeros(n_bars, dtype=float)
    x = np.linspace(-1.0, 1.0, n_bars)
    # 開收盤較活躍的 U 型權重，仍保留隨機性。
    weights = 0.65 + 1.35 * np.abs(x) ** 1.35
    alpha = np.maximum(weights / weights.mean() * concentration / n_bars, 0.05)
    shares = rng.dirichlet(alpha)
    raw = shares * total
    ints = np.floor(raw).astype(int)
    remainder = int(round(total - ints.sum()))
    if remainder > 0:
        order = np.argsort(raw - ints)[::-1]
        ints[order[:remainder]] += 1
    elif remainder < 0:
        order = np.argsort(raw - ints)
        for idx in order[:abs(remainder)]:
            if ints[idx] > 0:
                ints[idx] -= 1
    return ints.astype(float)


def _simulate_one_session(row: pd.Series, n_bars: int, start: pd.Timestamp,
                          config: SimulationConfig,
                          rng: np.random.Generator, seed: int) -> pd.DataFrame:
    o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
    path = _anchor_path(o, h, l, c, n_bars, rng, config.bridge_noise)
    session_range = max(h - l, 1e-9)
    volumes = _split_volume(float(row.get("volume", 0.0) or 0.0), n_bars, rng,
                            config.volume_concentration)
    timestamps = [start + pd.Timedelta(minutes=config.base_minutes * (i + 1))
                  for i in range(n_bars)]

    records = []
    for i in range(n_bars):
        bo, bc = float(path[i]), float(path[i + 1])
        body_hi, body_lo = max(bo, bc), min(bo, bc)
        room_up, room_dn = max(h - body_hi, 0.0), max(body_lo - l, 0.0)
        wick_scale = session_range * config.intrabar_wick_scale
        up = min(room_up, abs(rng.normal(0.0, wick_scale)))
        dn = min(room_dn, abs(rng.normal(0.0, wick_scale)))
        bh, bl = body_hi + up, body_lo - dn
        records.append({
            "datetime": timestamps[i],
            "trade_date": _as_timestamp(row.get("trade_date", row.get("datetime"))).normalize(),
            "session": str(row.get("session", "regular")),
            "timeframe": f"{config.base_minutes}m",
            "symbol": row.get("symbol", "MTX"),
            "contract_month": row.get("contract_month"),
            "open": bo,
            "high": min(max(bh, body_hi), h),
            "low": max(min(bl, body_lo), l),
            "close": bc,
            "volume": volumes[i],
            "open_interest": float(row.get("open_interest", 0.0) or 0.0),
            "simulated": True,
            "simulation_seed": int(seed),
            "source_open": o,
            "source_high": h,
            "source_low": l,
            "source_close": c,
        })

    out = pd.DataFrame(records)
    # 強制來源 high / low 各至少出現一次，且不改變 OHLC 邊界。
    hi_idx = int(np.argmax(path[1:]))
    lo_idx = int(np.argmin(path[1:]))
    out.loc[hi_idx, "high"] = h
    out.loc[lo_idx, "low"] = l
    out["high"] = out[["open", "high", "close"]].max(axis=1).clip(upper=h)
    out["low"] = out[["open", "low", "close"]].min(axis=1).clip(lower=l)
    return out


def simulate_30m(session_bars: pd.DataFrame, seed: int = 42,
                 config: SimulationConfig | None = None) -> pd.DataFrame:
    """把日盤/夜盤時段 OHLC 模擬為 30 分 K。"""
    if config is None:
        config = SimulationConfig()
    required = {"open", "high", "low", "close"}
    missing = sorted(required - set(session_bars.columns))
    if missing:
        raise ValueError(f"模擬資料缺少欄位：{missing}")
    src = session_bars.copy()
    src["trade_date"] = _trade_date_series(src)
    src = src.dropna(subset=["trade_date"]).reset_index(drop=True)
    if "session" not in src.columns:
        src["session"] = "regular"
    order_map = {"after_hours": 0, "regular": 1, "full_session": 1}
    src["_session_order"] = src["session"].map(order_map).fillna(1)
    src = src.sort_values(["trade_date", "_session_order"]).reset_index(drop=True)
    prev_map = _previous_trade_date_map(src["trade_date"])
    master_rng = np.random.default_rng(int(seed))

    frames = []
    for idx, row in src.iterrows():
        session = str(row.get("session", "regular"))
        n_bars = config.after_hours_bars if session == "after_hours" else config.regular_bars
        trade_date = _as_timestamp(row["trade_date"]).normalize()
        previous_hint = pd.to_datetime(row.get("previous_trade_date"), errors="coerce")
        previous_trade_date = (_as_timestamp(previous_hint).normalize()
                               if pd.notna(previous_hint) else prev_map[trade_date])
        start = _session_start(trade_date, session, previous_trade_date)
        child_seed = int(master_rng.integers(0, np.iinfo(np.int32).max))
        child_rng = np.random.default_rng(child_seed)
        frame = _simulate_one_session(row, n_bars, start, config, child_rng, int(seed))
        frame["source_row_index"] = int(idx)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values("datetime").reset_index(drop=True)


def aggregate_timeframe(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """由較小週期一致聚合較大週期；不跨 session 聚合。"""
    if bars.empty:
        return bars.copy()
    if minutes <= 0:
        raise ValueError("minutes 必須大於 0")
    out_frames = []
    keys = ["trade_date", "session"]
    for _, grp in bars.sort_values("datetime").groupby(keys, sort=True, dropna=False):
        grp = grp.reset_index(drop=True).copy()
        base_min = int(str(grp.get("timeframe", pd.Series(["30m"])).iloc[0]).rstrip("m"))
        factor = max(int(round(minutes / base_min)), 1)
        grp["_bucket"] = np.arange(len(grp)) // factor
        agg = grp.groupby("_bucket", sort=True).agg(
            datetime=("datetime", "max"),
            trade_date=("trade_date", "first"),
            session=("session", "first"),
            symbol=("symbol", "first"),
            contract_month=("contract_month", "first"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            open_interest=("open_interest", "last"),
            simulation_seed=("simulation_seed", "first"),
        ).reset_index(drop=True)
        agg["timeframe"] = f"{minutes}m"
        agg["simulated"] = True
        out_frames.append(agg)
    return pd.concat(out_frames, ignore_index=True).sort_values("datetime").reset_index(drop=True)


def aggregate_full_session_daily(session_bars: pd.DataFrame) -> pd.DataFrame:
    """把同一交易日的盤後＋一般盤合成完整交易日日 K。

    時序固定為盤後在前、一般盤在後；只有一個時段時直接使用該時段。
    """
    if session_bars.empty:
        return session_bars.copy()
    src = session_bars.copy()
    src["trade_date"] = _trade_date_series(src)
    if "session" not in src.columns:
        src["session"] = "regular"
    src["_session_order"] = src["session"].map({"after_hours": 0, "regular": 1}).fillna(1)
    src = src.sort_values(["trade_date", "_session_order"])
    out = src.groupby("trade_date", sort=True).agg(
        datetime=("trade_date", "first"),
        symbol=("symbol", "first"),
        contract_month=("contract_month", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        open_interest=("open_interest", "last"),
    ).reset_index(drop=False)
    out["datetime"] = pd.to_datetime(out["trade_date"]) + pd.Timedelta(hours=13, minutes=45)
    out["session"] = "full_session"
    out["timeframe"] = "1D"
    return out[["datetime", "trade_date", "symbol", "contract_month", "session", "timeframe",
                "open", "high", "low", "close", "volume", "open_interest"]]


def build_simulated_timeframes(session_bars: pd.DataFrame, seed: int = 42,
                               config: SimulationConfig | None = None,
                               required: Iterable[str] | None = None) -> dict[str, pd.DataFrame]:
    """建立策略實際需要的週期。

    ``required`` 預設仍建立 30m、60m、1D，以維持舊呼叫相容。若批次只有
    完整日 K 策略，可傳入 ``{"1D"}``，避免不必要地生成數萬根盤中模擬 K。
    60m 一律由同 seed 的 30m 聚合，因此要求 60m 時會先生成 30m。
    """
    need = set(required or {"30m", "60m", "1D"})
    unknown = need - {"30m", "60m", "1D"}
    if unknown:
        raise ValueError(f"不支援的模擬週期：{sorted(unknown)}")
    out: dict[str, pd.DataFrame] = {}
    if "1D" in need:
        out["1D"] = aggregate_full_session_daily(session_bars)
    if need & {"30m", "60m"}:
        bars30 = simulate_30m(session_bars, seed=seed, config=config)
        if "30m" in need:
            out["30m"] = bars30
        if "60m" in need:
            out["60m"] = aggregate_timeframe(bars30, 60)
    return out


def validate_simulation(source: pd.DataFrame, simulated_30m: pd.DataFrame,
                        atol: float = 1e-6) -> list[str]:
    """驗證模擬後 OHLCV 是否精確回到來源資料（向量化版本）。"""
    errors: list[str] = []
    if source.empty and simulated_30m.empty:
        return errors
    if source.empty != simulated_30m.empty:
        return ["來源資料與模擬資料其中一方為空"]

    src = source.copy()
    src["trade_date"] = _trade_date_series(src)
    if "session" not in src.columns:
        src["session"] = "regular"
    src["session"] = src["session"].astype(str)
    src["_order"] = range(len(src))
    src_agg = src.sort_values("_order").groupby(["trade_date", "session"], as_index=False).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))

    sim = simulated_30m.copy()
    sim["trade_date"] = pd.to_datetime(sim["trade_date"], errors="coerce").dt.normalize()
    if "session" not in sim.columns:
        sim["session"] = "regular"
    sim["session"] = sim["session"].astype(str)
    sim = sim.sort_values("datetime")
    sim_agg = sim.groupby(["trade_date", "session"], as_index=False).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))

    merged = src_agg.merge(sim_agg, on=["trade_date", "session"], how="outer",
                           suffixes=("_src", "_sim"), indicator=True)
    for _, row in merged.iterrows():
        key = f"{row.get('trade_date')}｜{row.get('session')}"
        if row["_merge"] != "both":
            errors.append(f"{key} 缺少{'模擬' if row['_merge']=='left_only' else '來源'}資料")
            continue
        for name in ("open", "high", "low", "close", "volume"):
            got = float(row[f"{name}_sim"])
            expected = float(row[f"{name}_src"])
            if not np.isclose(got, expected, atol=atol, rtol=0):
                errors.append(f"{key} {name} 不一致：{got} != {expected}")
    return errors

