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
