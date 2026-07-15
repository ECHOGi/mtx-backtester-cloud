# -*- coding: utf-8 -*-
"""
indicators.py - 技術指標。
輸入 pandas Series / DataFrame（OHLCV），輸出 Series / DataFrame。
所有指標只用當根(含)以前的資料，無未來函數。
"""
import numpy as np
import pandas as pd


# ---------- 移動平均 ----------
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def ma(s: pd.Series, n: int, kind: str = "SMA") -> pd.Series:
    kind = kind.upper()
    if kind == "SMA":
        return sma(s, n)
    if kind == "EMA":
        return ema(s, n)
    if kind == "WMA":
        return wma(s, n)
    raise ValueError(f"不支援的均線型態: {kind}（需為 SMA/EMA/WMA）")


# ---------- 震盪 / 趨勢指標 ----------
def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    dif = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd_dif": dif, "macd_dea": dea, "macd_hist": dif - dea})


def kd(df: pd.DataFrame, n: int = 9, k_smooth: int = 3,
       d_smooth: int = 3) -> pd.DataFrame:
    """台灣常用 KD（RSV 平滑法）。"""
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = ((df["close"] - low_n) / rng * 100).fillna(50)
    k = rsv.ewm(alpha=1 / k_smooth, adjust=False).mean()
    d = k.ewm(alpha=1 / d_smooth, adjust=False).mean()
    return pd.DataFrame({"k": k, "d": d})


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * sd,
                         "bb_lower": mid - k * sd})


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def dmi_adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder DMI/ADX。回傳 di_plus、di_minus、adx。

    僅使用當根及以前資料；平滑採 Wilder alpha=1/n。
    """
    n = max(int(n), 1)
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_sm = plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    minus_sm = minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    di_plus = 100.0 * plus_sm / atr_w.replace(0, np.nan)
    di_minus = 100.0 * minus_sm / atr_w.replace(0, np.nan)
    denom = (di_plus + di_minus).replace(0, np.nan)
    dx = 100.0 * (di_plus - di_minus).abs() / denom
    adx = dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return pd.DataFrame({"di_plus": di_plus, "di_minus": di_minus, "adx": adx}, index=df.index)


def volume_ma(volume: pd.Series, n: int = 20) -> pd.Series:
    return sma(volume, n)


def chandelier_exit(df: pd.DataFrame, n: int = 22,
                    mult: float = 3.0) -> pd.DataFrame:
    """吊燈出場：多方停損 = N日最高 - mult*ATR；空方 = N日最低 + mult*ATR。"""
    a = atr(df, n)
    return pd.DataFrame({
        "chandelier_long": df["high"].rolling(n, min_periods=n).max() - mult * a,
        "chandelier_short": df["low"].rolling(n, min_periods=n).min() + mult * a,
    })


# ---------- 均線輔助條件（可作進出場 / 過濾） ----------
def cross_over(a: pd.Series, b: pd.Series) -> pd.Series:
    """黃金交叉：a 由下往上穿越 b。"""
    return (a > b) & (a.shift(1) <= b.shift(1))


def cross_under(a: pd.Series, b: pd.Series) -> pd.Series:
    """死亡交叉：a 由上往下穿越 b。"""
    return (a < b) & (a.shift(1) >= b.shift(1))


def bullish_alignment(mas: list) -> pd.Series:
    """多頭排列：由短到長的均線遞減排列（短均在上）。"""
    cond = pd.Series(True, index=mas[0].index)
    for s1, s2 in zip(mas[:-1], mas[1:]):
        cond &= s1 > s2
    return cond


def bearish_alignment(mas: list) -> pd.Series:
    """空頭排列：短均在下。"""
    cond = pd.Series(True, index=mas[0].index)
    for s1, s2 in zip(mas[:-1], mas[1:]):
        cond &= s1 < s2
    return cond


# ---------- v0.8.5：自適應停損／乖離／缺口／寶塔線 ----------
def parabolic_sar(df: pd.DataFrame, af_start: float = 0.02,
                  af_step: float = 0.02, af_max: float = 0.2) -> pd.DataFrame:
    """Parabolic SAR（Wilder 遞迴版）。

    回傳欄位：
    - sar：當根處理完成後的 SAR 顯示值
    - sar_trend：1=多方、-1=空方
    - sar_flip_bullish / sar_flip_bearish：當根是否完成翻多／翻空
    - sar_stop_long / sar_stop_short：當根開盤前即可由過去資料算出的停損線，
      供盤中觸價出場使用，避免把當根極值偷放進停損價。
    """
    start = float(af_start)
    step = float(af_step)
    maximum = float(af_max)
    if start <= 0 or step <= 0 or maximum < start:
        raise ValueError("SAR 參數需滿足 af_start>0、af_step>0、af_max>=af_start")

    high = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    n = len(df)
    sar = np.full(n, np.nan, dtype=float)
    trend = np.zeros(n, dtype=int)
    flip_up = np.zeros(n, dtype=bool)
    flip_down = np.zeros(n, dtype=bool)
    stop_long = np.full(n, np.nan, dtype=float)
    stop_short = np.full(n, np.nan, dtype=float)
    if n == 0:
        return pd.DataFrame(index=df.index, data={
            "sar": sar, "sar_trend": trend,
            "sar_flip_bullish": flip_up, "sar_flip_bearish": flip_down,
            "sar_stop_long": stop_long, "sar_stop_short": stop_short,
        })
    if n == 1:
        return pd.DataFrame(index=df.index, data={
            "sar": sar, "sar_trend": trend,
            "sar_flip_bullish": flip_up, "sar_flip_bearish": flip_down,
            "sar_stop_long": stop_long, "sar_stop_short": stop_short,
        })

    # 第1根不使用第2根資料猜方向；第2根收盤後才完成初始化。
    is_up = bool(close[1] >= close[0])
    prev_sar = float(min(low[0], low[1]) if is_up else max(high[0], high[1]))
    ep = float(max(high[0], high[1]) if is_up else min(low[0], low[1]))
    af = start
    sar[1] = prev_sar
    trend[1] = 1 if is_up else -1

    for i in range(2, n):
        candidate = prev_sar + af * (ep - prev_sar)
        if is_up:
            candidate = min(candidate, low[i - 1])
            if i >= 2:
                candidate = min(candidate, low[i - 2])
            stop_long[i] = candidate
            if low[i] < candidate:
                # 當根盤中跌破原多方 SAR；成交停損仍用 candidate，
                # 顯示 SAR 則依標準轉向放到前一段 EP。
                is_up = False
                flip_down[i] = True
                current_sar = ep
                ep = low[i]
                af = start
            else:
                current_sar = candidate
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + step, maximum)
        else:
            candidate = max(candidate, high[i - 1])
            if i >= 2:
                candidate = max(candidate, high[i - 2])
            stop_short[i] = candidate
            if high[i] > candidate:
                is_up = True
                flip_up[i] = True
                current_sar = ep
                ep = high[i]
                af = start
            else:
                current_sar = candidate
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + step, maximum)
        sar[i] = current_sar
        trend[i] = 1 if is_up else -1
        prev_sar = current_sar

    return pd.DataFrame(index=df.index, data={
        "sar": sar, "sar_trend": trend,
        "sar_flip_bullish": flip_up, "sar_flip_bearish": flip_down,
        "sar_stop_long": stop_long, "sar_stop_short": stop_short,
    })


def bias(close: pd.Series, n: int = 20, kind: str = "SMA") -> pd.Series:
    """乖離率 BIAS = (close / MA(N) - 1) × 100。"""
    base = ma(close, int(n), str(kind))
    return (close / base.replace(0, np.nan) - 1.0) * 100.0


def open_gap_pct(df: pd.DataFrame) -> pd.Series:
    """開盤相對前一根收盤的有號跳空百分比。"""
    prev_close = df["close"].shift(1).replace(0, np.nan)
    return (df["open"] / prev_close - 1.0) * 100.0


def full_gap_created(df: pd.DataFrame, direction: str = "up",
                     min_gap_pct: float = 0.0) -> pd.Series:
    """日K完整缺口建立訊號：向上 low>前高；向下 high<前低。"""
    threshold = max(float(min_gap_pct), 0.0) / 100.0
    direction = str(direction).lower()
    if direction == "up":
        level = df["high"].shift(1)
        return (df["low"] > level * (1.0 + threshold)).fillna(False)
    if direction == "down":
        level = df["low"].shift(1)
        return (df["high"] < level * (1.0 - threshold)).fillna(False)
    raise ValueError("direction 需為 up 或 down")


def unfilled_gap(df: pd.DataFrame, direction: str = "up", min_age: int = 5,
                 lookback: int = 60, min_gap_pct: float = 0.0) -> pd.DataFrame:
    """追蹤仍未回補的日K完整缺口。

    條件成立表示：在最近 lookback 根內建立的完整缺口，已至少存在 min_age 根，
    且向上缺口未出現 low<=前高、向下缺口未出現 high>=前低。
    """
    direction = str(direction).lower()
    if direction not in {"up", "down"}:
        raise ValueError("direction 需為 up 或 down")
    min_age = max(int(min_age), 0)
    lookback = max(int(lookback), max(min_age, 1))
    threshold = max(float(min_gap_pct), 0.0) / 100.0
    highs = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
    active: list[tuple[int, float]] = []
    flag = np.zeros(len(df), dtype=bool)
    oldest_age = np.full(len(df), np.nan, dtype=float)
    for i in range(len(df)):
        kept = []
        for created_i, level in active:
            age = i - created_i
            filled = (lows[i] <= level) if direction == "up" else (highs[i] >= level)
            if (not filled) and age <= lookback:
                kept.append((created_i, level))
        active = kept
        if i >= 1:
            if direction == "up":
                level = highs[i - 1]
                created = np.isfinite(level) and np.isfinite(lows[i]) and lows[i] > level * (1.0 + threshold)
            else:
                level = lows[i - 1]
                created = np.isfinite(level) and np.isfinite(highs[i]) and highs[i] < level * (1.0 - threshold)
            if created:
                active.append((i, float(level)))
        ages = [i - created_i for created_i, _ in active if i - created_i >= min_age]
        if ages:
            flag[i] = True
            oldest_age[i] = max(ages)
    return pd.DataFrame(index=df.index, data={
        f"gap_{direction}_unfilled": flag,
        f"gap_{direction}_oldest_age": oldest_age,
    })


def tower_line(close: pd.Series, confirm_bars: int = 3) -> pd.DataFrame:
    """平台研究版寶塔線（三根轉向確認）。

    紅色狀態中，收盤跌破前 confirm_bars 根最低收盤才翻黑；
    黑色狀態中，收盤突破前 confirm_bars 根最高收盤才翻紅。
    此定義刻意固定且可重現，供與均線／突破條件做研究對照。
    """
    bars = max(int(confirm_bars), 1)
    c = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    n = len(c)
    color = np.zeros(n, dtype=int)
    flip_red = np.zeros(n, dtype=bool)
    flip_black = np.zeros(n, dtype=bool)
    state = 0
    for i in range(n):
        if i > 0 and state == 0 and np.isfinite(c[i - 1]) and np.isfinite(c[i]):
            if c[i] > c[i - 1]:
                state = 1
            elif c[i] < c[i - 1]:
                state = -1
        if i < bars or not np.isfinite(c[i]):
            color[i] = state
            continue
        prior = c[i - bars:i]
        if not np.isfinite(prior).all():
            color[i] = state
            continue
        if state <= 0 and c[i] > np.max(prior):
            state = 1
            flip_red[i] = True
        elif state >= 0 and c[i] < np.min(prior):
            state = -1
            flip_black[i] = True
        color[i] = state
    return pd.DataFrame(index=close.index, data={
        "tower_color": color,
        "tower_flip_red": flip_red,
        "tower_flip_black": flip_black,
    })
