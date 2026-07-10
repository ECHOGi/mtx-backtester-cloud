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


# ---------- 價格 vs 均線 ----------
@register("close_above_ma")
def close_above_ma(df, ma_type="SMA", period=20, **_):
    """close > MA(N)，ma_type 可為 SMA/EMA/WMA"""
    return df["close"] > _ma(df, ma_type, period)


@register("close_below_ma")
def close_below_ma(df, ma_type="SMA", period=20, **_):
    """close < MA(N)"""
    return df["close"] < _ma(df, ma_type, period)


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


# ---------- 組合器 ----------
def evaluate_condition(df: pd.DataFrame, spec: dict) -> pd.Series:
    """spec = {"type": 條件名, ...其餘為該條件參數}"""
    spec = dict(spec)
    ctype = spec.pop("type", None)
    if ctype not in CONDITIONS:
        raise ValueError(
            f"未知條件 '{ctype}'。可用條件：{sorted(CONDITIONS.keys())}")
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
