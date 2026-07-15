# -*- coding: utf-8 -*-
"""
condition_blocks.py - 策略條件模組（kcTrader 式條件組合）。

設計：
- 每個條件是一個 function：輸入標準 OHLCV DataFrame + 參數，輸出 bool Series
- 用 @register("條件名") 註冊進 CONDITIONS 字典
- evaluate_block() 用 AND / OR 組合多個條件
- 新增條件：寫一個函式 + 註冊即可，backtester.py 與 strategies.py 不用改

JSON 用法（見 strategies.py 的策略 JSON 結構）：
    {"logic": "AND", "conditions": [
        {"type": "macd_hist_cross_up"},
        {"type": "close_above_ma", "ma_type": "SMA", "period": 20}
    ]}

注意：進場條件在此模組；「出場條件」（固定停損/停利/移動停損/吊燈/MACD反向）
因為與持倉狀態有關（進場價、持倉極值），由 backtester.py 依 StrategyParams
的開關執行，不在此模組。
"""
import json

import numpy as np
import pandas as pd

import indicators as ind

CONDITIONS = {}


def register(name):
    def deco(fn):
        CONDITIONS[name] = fn
        return fn
    return deco


# ---------- 內部小工具 ----------
def _ma(df, ma_type, period):
    return ind.ma(df["close"], int(period), str(ma_type))


def _bb(df, period, std):
    return ind.bollinger(df["close"], int(period), float(std))


def _macd(df, fast, slow, signal):
    return ind.macd(df["close"], int(fast), int(slow), int(signal))


def _kd(df, period, k_smooth, d_smooth):
    return ind.kd(df, int(period), int(k_smooth), int(d_smooth))


def _dmi(df, period=14):
    n = int(period)
    return _cached_indicator(df, ("dmi_adx", n), lambda: ind.dmi_adx(df, n))


# ---------- 價格 vs 均線 ----------
@register("close_above_ma")
def close_above_ma(df, ma_type="SMA", period=20, **_):
    """close > MA(N)，ma_type 可為 SMA/EMA/WMA"""
    return df["close"] > _ma(df, ma_type, period)


@register("close_below_ma")
def close_below_ma(df, ma_type="SMA", period=20, **_):
    """close < MA(N)"""
    return df["close"] < _ma(df, ma_type, period)


@register("close_cross_up_ma")
def close_cross_up_ma(df, ma_type="SMA", period=20, **_):
    """收盤價由下往上穿越均線。"""
    return ind.cross_over(df["close"], _ma(df, ma_type, period))


@register("close_cross_down_ma")
def close_cross_down_ma(df, ma_type="SMA", period=20, **_):
    """收盤價由上往下穿越均線。"""
    return ind.cross_under(df["close"], _ma(df, ma_type, period))


# ---------- 價格 vs Bollinger ----------
@register("close_above_bollinger_mid")
def close_above_bollinger_mid(df, period=20, std=2.0, **_):
    return df["close"] > _bb(df, period, std)["bb_mid"]


@register("close_below_bollinger_mid")
def close_below_bollinger_mid(df, period=20, std=2.0, **_):
    return df["close"] < _bb(df, period, std)["bb_mid"]


@register("close_above_bollinger_upper")
def close_above_bollinger_upper(df, period=20, std=2.0, **_):
    return df["close"] > _bb(df, period, std)["bb_upper"]


@register("close_below_bollinger_lower")
def close_below_bollinger_lower(df, period=20, std=2.0, **_):
    return df["close"] < _bb(df, period, std)["bb_lower"]


@register("close_cross_up_bollinger_mid")
def close_cross_up_bollinger_mid(df, period=20, std=2.0, **_):
    """突破中線（由下往上穿越）"""
    return ind.cross_over(df["close"], _bb(df, period, std)["bb_mid"])


@register("close_cross_down_bollinger_mid")
def close_cross_down_bollinger_mid(df, period=20, std=2.0, **_):
    """跌破中線（由上往下穿越）"""
    return ind.cross_under(df["close"], _bb(df, period, std)["bb_mid"])


@register("close_cross_up_bollinger_upper")
def close_cross_up_bollinger_upper(df, period=20, std=2.0, **_):
    """突破布林上軌（由下往上穿越）"""
    return ind.cross_over(df["close"], _bb(df, period, std)["bb_upper"])


@register("close_cross_down_bollinger_upper")
def close_cross_down_bollinger_upper(df, period=20, std=2.0, **_):
    """跌回布林上軌之下（由上往下穿越上軌）"""
    return ind.cross_under(df["close"], _bb(df, period, std)["bb_upper"])


@register("close_cross_up_bollinger_lower")
def close_cross_up_bollinger_lower(df, period=20, std=2.0, **_):
    """站回布林下軌之上（由下往上穿越下軌）"""
    return ind.cross_over(df["close"], _bb(df, period, std)["bb_lower"])


@register("close_cross_down_bollinger_lower")
def close_cross_down_bollinger_lower(df, period=20, std=2.0, **_):
    """跌破布林下軌（由上往下穿越）"""
    return ind.cross_under(df["close"], _bb(df, period, std)["bb_lower"])


# ---------- 均線交叉 / 排列 ----------
@register("ma_cross_up")
def ma_cross_up(df, ma_type="SMA", fast=5, slow=20, **_):
    """短均上穿長均（黃金交叉），ma_type=SMA/EMA/WMA"""
    return ind.cross_over(_ma(df, ma_type, fast), _ma(df, ma_type, slow))


@register("ma_cross_down")
def ma_cross_down(df, ma_type="SMA", fast=5, slow=20, **_):
    """短均下穿長均（死亡交叉）"""
    return ind.cross_under(_ma(df, ma_type, fast), _ma(df, ma_type, slow))


@register("ma_bullish_alignment")
def ma_bullish_alignment(df, ma_type="SMA", periods=(5, 10, 20), **_):
    """多頭排列：MA(periods[0]) > MA(periods[1]) > ..."""
    mas = [_ma(df, ma_type, n) for n in periods]
    return ind.bullish_alignment(mas)


@register("ma_bearish_alignment")
def ma_bearish_alignment(df, ma_type="SMA", periods=(5, 10, 20), **_):
    """空頭排列"""
    mas = [_ma(df, ma_type, n) for n in periods]
    return ind.bearish_alignment(mas)


# ---------- MACD ----------
@register("macd_hist_cross_up")
def macd_hist_cross_up(df, fast=12, slow=26, signal=9, **_):
    """MACD histogram 由負轉正"""
    h = _macd(df, fast, slow, signal)["macd_hist"]
    return (h > 0) & (h.shift(1) <= 0)


@register("macd_hist_cross_down")
def macd_hist_cross_down(df, fast=12, slow=26, signal=9, **_):
    """MACD histogram 由正轉負"""
    h = _macd(df, fast, slow, signal)["macd_hist"]
    return (h < 0) & (h.shift(1) >= 0)


@register("macd_hist_rising")
def macd_hist_rising(df, fast=12, slow=26, signal=9, **_):
    """MACD 柱狀體本根高於前一根；不限制正負值。"""
    h = _macd(df, fast, slow, signal)["macd_hist"]
    return h > h.shift(1)


@register("macd_dif_cross_up")
def macd_dif_cross_up(df, fast=12, slow=26, signal=9, **_):
    """DIF 上穿訊號線(DEA)"""
    m = _macd(df, fast, slow, signal)
    return ind.cross_over(m["macd_dif"], m["macd_dea"])


@register("macd_hist_positive")
def macd_hist_positive(df, fast=12, slow=26, signal=9, **_):
    """MACD 柱狀圖為正（多方動能持續）"""
    return _macd(df, fast, slow, signal)["macd_hist"] > 0


@register("macd_hist_negative")
def macd_hist_negative(df, fast=12, slow=26, signal=9, **_):
    """MACD 柱狀圖為負（空方動能持續）"""
    return _macd(df, fast, slow, signal)["macd_hist"] < 0


@register("macd_dif_above_zero")
def macd_dif_above_zero(df, fast=12, slow=26, signal=9, **_):
    """DIF 在零軸之上（中期偏多）"""
    return _macd(df, fast, slow, signal)["macd_dif"] > 0


@register("macd_dif_below_zero")
def macd_dif_below_zero(df, fast=12, slow=26, signal=9, **_):
    """DIF 在零軸之下（中期偏空）"""
    return _macd(df, fast, slow, signal)["macd_dif"] < 0


@register("macd_dif_cross_down")
def macd_dif_cross_down(df, fast=12, slow=26, signal=9, **_):
    """DIF 下穿訊號線(DEA)"""
    m = _macd(df, fast, slow, signal)
    return ind.cross_under(m["macd_dif"], m["macd_dea"])


@register("ma_slope_up")
def ma_slope_up(df, ma_type="SMA", period=20, **_):
    """均線向上（今日均線值高於昨日）"""
    m = _ma(df, ma_type, period)
    return m > m.shift(1)


@register("ma_slope_down")
def ma_slope_down(df, ma_type="SMA", period=20, **_):
    """均線向下（今日均線值低於昨日）"""
    m = _ma(df, ma_type, period)
    return m < m.shift(1)


@register("close_return_pct_above")
def close_return_pct_above(df, lookback=3, value=1.5, **_):
    """收盤價 N 根有號報酬率高於門檻（百分比）。"""
    n = max(int(lookback), 1)
    ret = (df["close"] / df["close"].shift(n) - 1.0) * 100.0
    return ret > float(value)


@register("close_return_pct_below")
def close_return_pct_below(df, lookback=3, value=-1.5, **_):
    """收盤價 N 根有號報酬率低於門檻（百分比）。"""
    n = max(int(lookback), 1)
    ret = (df["close"] / df["close"].shift(n) - 1.0) * 100.0
    return ret < float(value)


@register("di_minus_above_di_plus")
def di_minus_above_di_plus(df, period=14, **_):
    """Wilder DI- > DI+。"""
    d = _dmi(df, period)
    return d["di_minus"] > d["di_plus"]


@register("di_plus_above_di_minus")
def di_plus_above_di_minus(df, period=14, **_):
    """Wilder DI+ > DI-。"""
    d = _dmi(df, period)
    return d["di_plus"] > d["di_minus"]


@register("adx_above")
def adx_above(df, period=14, value=20, **_):
    """Wilder ADX 高於門檻。"""
    return _dmi(df, period)["adx"] >= float(value)


@register("adx_below")
def adx_below(df, period=14, value=20, **_):
    """Wilder ADX 低於門檻。"""
    return _dmi(df, period)["adx"] < float(value)


@register("ma_rejection_bearish")
def ma_rejection_bearish(df, ma_type="SMA", period=20, proximity_pct=0.5,
                         require_bearish_close=False, allow_upper_wick=True,
                         upper_wick_body_ratio=1.0, **_):
    """價格由下方反彈接近均線但收盤未站上，並出現空方拒絕K棒。

    proximity_pct：最高價至少到達 MA 下方指定百分比範圍。
    空方K棒可由收黑或上影線/實體比例達標確認。
    """
    m = _ma(df, ma_type, period)
    prox = max(float(proximity_pct), 0.0) / 100.0
    approached = df["high"] >= m * (1.0 - prox)
    stayed_below = df["close"] < m
    was_below = df["close"].shift(1) < m.shift(1)
    bearish = df["close"] < df["open"]
    body = (df["close"] - df["open"]).abs().replace(0, np.nan)
    upper_wick = df["high"] - pd.concat([df["open"], df["close"]], axis=1).max(axis=1)
    wick_ok = upper_wick >= body.fillna(0.0) * max(float(upper_wick_body_ratio), 0.0)
    if bool(require_bearish_close):
        candle_ok = bearish
    elif bool(allow_upper_wick):
        candle_ok = bearish | wick_ok
    else:
        candle_ok = bearish
    return approached & stayed_below & was_below & candle_ok


# ---------- KD ----------
@register("kd_cross_up")
def kd_cross_up(df, period=9, k_smooth=3, d_smooth=3, **_):
    """K 上穿 D"""
    k = _kd(df, period, k_smooth, d_smooth)
    return ind.cross_over(k["k"], k["d"])


@register("kd_cross_down")
def kd_cross_down(df, period=9, k_smooth=3, d_smooth=3, **_):
    """K 下穿 D"""
    k = _kd(df, period, k_smooth, d_smooth)
    return ind.cross_under(k["k"], k["d"])


@register("kd_k_above")
def kd_k_above(df, value=80, period=9, k_smooth=3, d_smooth=3, **_):
    """K 高於指定值"""
    return _kd(df, period, k_smooth, d_smooth)["k"] > float(value)


@register("kd_k_below")
def kd_k_below(df, value=20, period=9, k_smooth=3, d_smooth=3, **_):
    """K 低於指定值"""
    return _kd(df, period, k_smooth, d_smooth)["k"] < float(value)


@register("kd_k_above_d")
def kd_k_above_d(df, period=9, k_smooth=3, d_smooth=3, **_):
    """K 在 D 之上（多方格局）"""
    k = _kd(df, period, k_smooth, d_smooth)
    return k["k"] > k["d"]


@register("kd_k_below_d")
def kd_k_below_d(df, period=9, k_smooth=3, d_smooth=3, **_):
    """K 在 D 之下（空方格局）"""
    k = _kd(df, period, k_smooth, d_smooth)
    return k["k"] < k["d"]


# ---------- RSI ----------
@register("rsi_above")
def rsi_above(df, value=50, period=14, **_):
    return ind.rsi(df["close"], int(period)) > float(value)


@register("rsi_below")
def rsi_below(df, value=50, period=14, **_):
    return ind.rsi(df["close"], int(period)) < float(value)


@register("rsi_cross_up")
def rsi_cross_up(df, value=50, period=14, **_):
    """RSI 由下往上穿越門檻"""
    r = ind.rsi(df["close"], int(period))
    v = float(value)
    return (r > v) & (r.shift(1) <= v)


@register("rsi_cross_down")
def rsi_cross_down(df, value=50, period=14, **_):
    """RSI 由上往下穿越門檻"""
    r = ind.rsi(df["close"], int(period))
    v = float(value)
    return (r < v) & (r.shift(1) >= v)


# ---------- 成交量 ----------
@register("volume_above_ma")
def volume_above_ma(df, period=20, multiplier=1.0, **_):
    """volume > volume_ma(N) * multiplier"""
    return df["volume"] > ind.volume_ma(df["volume"], int(period)) * float(multiplier)


@register("volume_below_ma")
def volume_below_ma(df, period=20, multiplier=1.0, **_):
    """volume < volume_ma(N) * multiplier"""
    return df["volume"] < ind.volume_ma(df["volume"], int(period)) * float(multiplier)


@register("volume_above_prev")
def volume_above_prev(df, multiplier=1.5, **_):
    """成交量大於昨日量 × 倍數（單日爆量）"""
    return df["volume"] > df["volume"].shift(1) * float(multiplier)




# ---------- v0.7.0 通用研究條件 ----------
@register("normalized_atr_above")
def normalized_atr_above(df, value=2.0, period=14, **_):
    """ATR / close × 100 高於指定百分比。"""
    ratio = ind.atr(df, int(period)) / df["close"].replace(0, np.nan) * 100.0
    return ratio > float(value)


@register("normalized_atr_below")
def normalized_atr_below(df, value=2.0, period=14, **_):
    """ATR / close × 100 低於指定百分比。"""
    ratio = ind.atr(df, int(period)) / df["close"].replace(0, np.nan) * 100.0
    return ratio < float(value)


@register("atr_percentile_above")
def atr_percentile_above(df, value=70, period=14, lookback=252, **_):
    """目前 ATR 在過去 lookback 根中的百分位高於指定值。"""
    a = ind.atr(df, int(period))
    lb = max(int(lookback), 2)
    pct = a.rolling(lb, min_periods=max(20, lb // 5)).apply(
        lambda x: float(np.sum(x <= x[-1])) / len(x) * 100.0, raw=True)
    return pct > float(value)


@register("atr_percentile_below")
def atr_percentile_below(df, value=30, period=14, lookback=252, **_):
    """目前 ATR 在過去 lookback 根中的百分位低於指定值。"""
    a = ind.atr(df, int(period))
    lb = max(int(lookback), 2)
    pct = a.rolling(lb, min_periods=max(20, lb // 5)).apply(
        lambda x: float(np.sum(x <= x[-1])) / len(x) * 100.0, raw=True)
    return pct < float(value)


@register("ma_slope_pct_above")
def ma_slope_pct_above(df, ma_type="SMA", period=120, lookback=20, value=0.0, **_):
    """均線在 lookback 根內的百分比斜率高於門檻。"""
    m = _ma(df, ma_type, period)
    slope = (m / m.shift(max(int(lookback), 1)) - 1.0) * 100.0
    return slope > float(value)


@register("ma_slope_pct_below")
def ma_slope_pct_below(df, ma_type="SMA", period=120, lookback=20, value=0.0, **_):
    """均線在 lookback 根內的百分比斜率低於門檻。"""
    m = _ma(df, ma_type, period)
    slope = (m / m.shift(max(int(lookback), 1)) - 1.0) * 100.0
    return slope < float(value)


@register("close_drawdown_from_high_above")
def close_drawdown_from_high_above(df, lookback=252, value=-10.0, **_):
    """收盤相對過去高點回撤率高於門檻，例如 > -10%。"""
    high = df["close"].rolling(max(int(lookback), 2), min_periods=2).max()
    dd = (df["close"] / high - 1.0) * 100.0
    return dd > float(value)


@register("close_drawdown_from_high_below")
def close_drawdown_from_high_below(df, lookback=252, value=-10.0, **_):
    """收盤相對過去高點回撤率低於門檻，例如 < -10%。"""
    high = df["close"].rolling(max(int(lookback), 2), min_periods=2).max()
    dd = (df["close"] / high - 1.0) * 100.0
    return dd < float(value)


@register("close_breakout_high")
def close_breakout_high(df, lookback=60, **_):
    """收盤突破前 lookback 根最高收盤。"""
    prior_high = df["close"].shift(1).rolling(max(int(lookback), 2), min_periods=2).max()
    return df["close"] > prior_high


@register("close_breakdown_low")
def close_breakdown_low(df, lookback=60, **_):
    """收盤跌破前 lookback 根最低收盤。"""
    prior_low = df["close"].shift(1).rolling(max(int(lookback), 2), min_periods=2).min()
    return df["close"] < prior_low



# ---------- v0.8.5：SAR / BIAS / 日K缺口 / 寶塔線 ----------
def _cached_indicator(df: pd.DataFrame, key: tuple, builder):
    """同一策略／同一路徑內只計算一次相同參數的指標。"""
    cache = df.attrs.setdefault("_condition_indicator_cache", {})
    if key not in cache:
        cache[key] = builder()
    return cache[key]

def _sar(df, af_start=0.02, af_step=0.02, af_max=0.2):
    a, step, maximum = float(af_start), float(af_step), float(af_max)
    key = ("sar", a, step, maximum)
    return _cached_indicator(df, key, lambda: ind.parabolic_sar(df, a, step, maximum))


@register("sar_flip_bullish")
def sar_flip_bullish(df, af_start=0.02, af_step=0.02, af_max=0.2, **_):
    """Parabolic SAR 當根由空方翻為多方。"""
    return _sar(df, af_start, af_step, af_max)["sar_flip_bullish"]


@register("sar_flip_bearish")
def sar_flip_bearish(df, af_start=0.02, af_step=0.02, af_max=0.2, **_):
    """Parabolic SAR 當根由多方翻為空方。"""
    return _sar(df, af_start, af_step, af_max)["sar_flip_bearish"]


@register("sar_bullish")
def sar_bullish(df, af_start=0.02, af_step=0.02, af_max=0.2, **_):
    """Parabolic SAR 目前為多方狀態。"""
    return _sar(df, af_start, af_step, af_max)["sar_trend"] > 0


@register("sar_bearish")
def sar_bearish(df, af_start=0.02, af_step=0.02, af_max=0.2, **_):
    """Parabolic SAR 目前為空方狀態。"""
    return _sar(df, af_start, af_step, af_max)["sar_trend"] < 0


@register("bias_above")
def bias_above(df, value=5.0, period=20, ma_type="SMA", **_):
    """乖離率高於門檻；BIAS=(close/MA-1)×100。"""
    n, kind = int(period), str(ma_type)
    b = _cached_indicator(df, ("bias", n, kind.upper()),
                          lambda: ind.bias(df["close"], n, kind))
    return b > float(value)


@register("bias_below")
def bias_below(df, value=-5.0, period=20, ma_type="SMA", **_):
    """乖離率低於門檻。"""
    n, kind = int(period), str(ma_type)
    b = _cached_indicator(df, ("bias", n, kind.upper()),
                          lambda: ind.bias(df["close"], n, kind))
    return b < float(value)


@register("bias_cross_up")
def bias_cross_up(df, value=0.0, period=20, ma_type="SMA", **_):
    """乖離率由下往上穿越門檻。"""
    n, kind = int(period), str(ma_type)
    b = _cached_indicator(df, ("bias", n, kind.upper()),
                          lambda: ind.bias(df["close"], n, kind))
    v = float(value)
    return (b > v) & (b.shift(1) <= v)


@register("bias_cross_down")
def bias_cross_down(df, value=0.0, period=20, ma_type="SMA", **_):
    """乖離率由上往下穿越門檻。"""
    n, kind = int(period), str(ma_type)
    b = _cached_indicator(df, ("bias", n, kind.upper()),
                          lambda: ind.bias(df["close"], n, kind))
    v = float(value)
    return (b < v) & (b.shift(1) >= v)


@register("open_gap_pct_above")
def open_gap_pct_above(df, value=1.0, **_):
    """開盤相對前收的有號跳空百分比高於門檻。"""
    gap = _cached_indicator(df, ("open_gap_pct",), lambda: ind.open_gap_pct(df))
    return gap > float(value)


@register("open_gap_pct_below")
def open_gap_pct_below(df, value=-1.0, **_):
    """開盤相對前收的有號跳空百分比低於門檻。"""
    gap = _cached_indicator(df, ("open_gap_pct",), lambda: ind.open_gap_pct(df))
    return gap < float(value)


@register("full_gap_up")
def full_gap_up(df, min_gap_pct=0.0, **_):
    """當根建立向上完整缺口：low > 前一根 high。"""
    threshold = float(min_gap_pct)
    return _cached_indicator(df, ("full_gap", "up", threshold),
                             lambda: ind.full_gap_created(df, "up", threshold))


@register("full_gap_down")
def full_gap_down(df, min_gap_pct=0.0, **_):
    """當根建立向下完整缺口：high < 前一根 low。"""
    threshold = float(min_gap_pct)
    return _cached_indicator(df, ("full_gap", "down", threshold),
                             lambda: ind.full_gap_created(df, "down", threshold))


@register("gap_up_unfilled")
def gap_up_unfilled(df, min_age=5, lookback=60, min_gap_pct=0.0, **_):
    """最近 lookback 根內的向上完整缺口，至少 min_age 根仍未回補。"""
    age, lb, threshold = int(min_age), int(lookback), float(min_gap_pct)
    result = _cached_indicator(df, ("unfilled_gap", "up", age, lb, threshold),
                               lambda: ind.unfilled_gap(df, "up", age, lb, threshold))
    return result["gap_up_unfilled"]


@register("gap_down_unfilled")
def gap_down_unfilled(df, min_age=5, lookback=60, min_gap_pct=0.0, **_):
    """最近 lookback 根內的向下完整缺口，至少 min_age 根仍未回補。"""
    age, lb, threshold = int(min_age), int(lookback), float(min_gap_pct)
    result = _cached_indicator(df, ("unfilled_gap", "down", age, lb, threshold),
                               lambda: ind.unfilled_gap(df, "down", age, lb, threshold))
    return result["gap_down_unfilled"]


@register("tower_flip_red")
def tower_flip_red(df, confirm_bars=3, **_):
    """平台研究版寶塔線由黑翻紅。"""
    bars = int(confirm_bars)
    tower = _cached_indicator(df, ("tower", bars), lambda: ind.tower_line(df["close"], bars))
    return tower["tower_flip_red"]


@register("tower_flip_black")
def tower_flip_black(df, confirm_bars=3, **_):
    """平台研究版寶塔線由紅翻黑。"""
    bars = int(confirm_bars)
    tower = _cached_indicator(df, ("tower", bars), lambda: ind.tower_line(df["close"], bars))
    return tower["tower_flip_black"]


@register("tower_red")
def tower_red(df, confirm_bars=3, **_):
    """平台研究版寶塔線目前為紅色。"""
    bars = int(confirm_bars)
    tower = _cached_indicator(df, ("tower", bars), lambda: ind.tower_line(df["close"], bars))
    return tower["tower_color"] > 0


@register("tower_black")
def tower_black(df, confirm_bars=3, **_):
    """平台研究版寶塔線目前為黑色。"""
    bars = int(confirm_bars)
    tower = _cached_indicator(df, ("tower", bars), lambda: ind.tower_line(df["close"], bars))
    return tower["tower_color"] < 0

# ---------- 組合器 ----------
def evaluate_condition(df: pd.DataFrame, spec: dict) -> pd.Series:
    """spec = {"type": 條件名, ...其餘為該條件參數}。

    v0.7.0 另支援可巢狀的通用條件：
    - all_recent：指定條件連續 bars 根皆成立
    - all_recent_trigger：指定條件首次達成連續 bars 根時觸發一次
    - any_recent：指定條件最近 bars 根至少成立一次
    - not：反向條件
    """
    spec = dict(spec or {})
    ctype = spec.pop("type", None)
    if ctype in {"all_recent", "all_recent_trigger", "any_recent", "not"}:
        inner = spec.get("condition") or spec.get("inner")
        if not isinstance(inner, dict):
            raise ValueError(f"{ctype} 需要 condition 物件。")
        base = evaluate_condition(df, inner).fillna(False)
        if ctype == "not":
            return (~base).fillna(False)
        bars = max(int(spec.get("bars", 2)), 1)
        count = base.astype(int).rolling(bars, min_periods=bars).sum()
        if ctype == "all_recent":
            return (count >= bars).fillna(False)
        if ctype == "all_recent_trigger":
            state = (count >= bars).fillna(False)
            return (state & ~state.shift(1, fill_value=False)).fillna(False)
        return (count >= 1).fillna(False)
    if ctype not in CONDITIONS:
        extras = ["all_recent", "all_recent_trigger", "any_recent", "not"]
        raise ValueError(
            f"未知條件 '{ctype}'。可用條件：{sorted(CONDITIONS.keys()) + extras}")
    return CONDITIONS[ctype](df, **spec).fillna(False)


def _condition_label(spec: dict) -> str:
    """把條件 spec 轉成交易明細可讀的短文字。"""
    ctype = spec.get("type", "unknown")
    params = {k: v for k, v in spec.items() if k != "type"}
    if not params:
        return str(ctype)
    return f"{ctype}({json.dumps(params, ensure_ascii=False, sort_keys=True)})"


def evaluate_block(df: pd.DataFrame, block: dict) -> pd.Series:
    """
    block = {"logic": "AND"|"OR", "conditions": [spec, ...]}
    空 block 回傳全 False（= 不進場）。
    """
    signal, _ = evaluate_block_with_reasons(df, block)
    return signal


def evaluate_block_with_reasons(df: pd.DataFrame, block: dict) -> tuple[pd.Series, pd.Series]:
    """
    回傳 (signal, reasons)。reasons 會標出該根 K 棒進場時成立的條件積木。

    - AND：訊號成立時列出整組條件，因為每個條件都必須成立。
    - OR ：訊號成立時只列出該根 K 棒實際成立的條件。

    這只用來追蹤交易可解釋性，不改變原本進場邏輯。
    """
    false_signal = pd.Series(False, index=df.index)
    empty_reason = pd.Series("", index=df.index, dtype="object")
    if not block or not block.get("conditions"):
        return false_signal, empty_reason

    logic = str(block.get("logic", "AND")).upper()
    if logic not in ("AND", "OR"):
        raise ValueError(f"logic 需為 AND 或 OR，收到 {logic}")

    specs = list(block["conditions"])
    labels = [_condition_label(spec) for spec in specs]
    series = [evaluate_condition(df, spec).fillna(False) for spec in specs]

    result = series[0]
    for s in series[1:]:
        result = (result & s) if logic == "AND" else (result | s)
    result = result.fillna(False)

    reasons = empty_reason.copy()
    if logic == "AND":
        reasons.loc[result] = " AND ".join(labels)
    else:
        for label, s in zip(labels, series):
            mask = result & s
            if mask.any():
                prior = reasons.loc[mask]
                reasons.loc[mask] = prior.where(prior == "", prior + " OR ") + label
    return result, reasons


def list_conditions() -> dict:
    """回傳 {條件名: docstring}，供介面或文件使用。"""
    return {name: (fn.__doc__ or "").strip()
            for name, fn in sorted(CONDITIONS.items())}
