# -*- coding: utf-8 -*-
"""
backtester.py - 單一持倉回測引擎（避免 look-ahead bias）。

規則：
- 進場：第 i 根「收盤」訊號成立 -> 第 i+1 根「開盤價」進場（含滑價）
- 出場優先順序（同一根 K 棒內）：
    斷頭開盤跳空 > 固定停損 > 固定停利 > 移動停損 > 斷頭盤中觸價
    > 吊燈出場 > MACD 反向 > 條件出場
  * v0.5.1 起可設定「獲利放大後排除 MACD 反向」，讓分段吊燈真正主導後段出場。
  * v0.6.5 起支援以「最大順向浮盈 ÷ 進場前已完成 K 棒 ATR」作為相對市場波動階梯。
  * 停損/停利/移動停損：盤中觸價，以觸價價位成交；若開盤跳空超過則以開盤價
  * 吊燈 / MACD 反向：收盤確認，以「收盤價」出場
- 單一持倉：只有空手時才接受新訊號；不加碼、不反手
- 移動停損追蹤的最高/最低點只用「前一根(含)以前」的資料，無未來函數
- timeframe 不寫死：任何符合 OHLCV 的資料（5分K/60分K/週K...）皆可回測

v0.3 正確性強化：
- 交易明細新增 signal_date / signal_bar_index / entry_execution_date / entry_bar_index / exit_bar_index
- 交易明細新增 entry_reason，保留每筆交易的進場條件積木文字
- 保留舊欄位 entry_date，並讓它等於實際進場執行日，避免舊 UI/匯出流程壞掉

成本：
- 手續費 fee：單邊、每口固定金額，進出各收一次
- 滑價 slippage_points：進出場價格各往不利方向調整
- 期交稅 tax_rate：成交金額 * 稅率，進出各一次（預設用 config 值，可設 0）
"""
from dataclasses import dataclass

import pandas as pd


@dataclass
class CostModel:
    point_value: float = 50.0     # 每點價值 (元)
    fee: float = 20.0             # 單邊手續費 (元/口)
    slippage_points: float = 1.0  # 單邊滑價 (點)
    tax_rate: float = 0.0         # 期交稅率 (單邊)，先預留
    quantity: int = 1             # 口數（第一版固定單一持倉）
    use_margin_call_check: bool = False  # v0.4.0：安全緩衝金額被吃光時，視同斷頭強制平倉
    safety_buffer_amount: float = 0.0    # 可承受的反向浮動損失金額（元）
    original_margin_amount: float = 159000.0  # 一口小台原始保證金（元，顯示/報告用）


def _entry_reason(row: pd.Series, direction: str) -> str:
    """從策略訊號列取出進場條件說明；若舊策略沒有 reasons 欄，仍可相容。"""
    col = "long_entry_reasons" if direction == "long" else "short_entry_reasons"
    reason = row.get(col, "")
    if pd.isna(reason) or str(reason).strip() == "":
        return "long_entry" if direction == "long" else "short_entry"
    return str(reason)


def _margin_call_line(entry_price: float, direction: int, cost: CostModel) -> float | None:
    """回傳斷頭判斷價格。

    本專案的斷頭不是券商維持保證金判斷，而是：
    持倉期間反向浮動損失 >= 安全緩衝金額。
    """
    if not cost.use_margin_call_check or cost.safety_buffer_amount <= 0:
        return None
    q = max(int(cost.quantity), 1)
    buffer_points = float(cost.safety_buffer_amount) / (float(cost.point_value) * q)
    return entry_price - direction * buffer_points


def _adverse_points(entry_price: float, direction: int, row: pd.Series) -> float:
    """以本根 K 棒估算持倉期間最不利反向浮動點數。"""
    if direction == 1:
        return max(0.0, float(entry_price) - float(row["low"]))
    return max(0.0, float(row["high"]) - float(entry_price))


def _favorable_points(entry_price: float, direction: int, row: pd.Series) -> float:
    """以本根 K 棒估算持倉期間最有利順向浮動點數。"""
    if direction == 1:
        return max(0.0, float(row["high"]) - float(entry_price))
    return max(0.0, float(entry_price) - float(row["low"]))


def _current_unrealized_points(entry_price: float, direction: int, row: pd.Series) -> float:
    return (float(row["close"]) - float(entry_price)) * int(direction)


def _current_unrealized_amount(entry_price: float, direction: int, row: pd.Series, cost: CostModel) -> float:
    return _current_unrealized_points(entry_price, direction, row) * float(cost.point_value) * int(cost.quantity)


def _tier_reference_amount(pos: dict, entry_price: float, direction: int, row: pd.Series, cost: CostModel, p) -> float:
    """回傳獲利分段依據金額。

    current_unrealized：用當根收盤浮盈；
    max_favorable：用持倉以來最高順向浮盈，較符合「獲利放大後同步放大出場條件」。
    """
    ref = str(getattr(p, "profit_tier_reference", "current_unrealized") or "current_unrealized").lower()
    if ref in {"max_favorable", "max_floating_profit", "max_profit", "peak_profit"}:
        return float(pos.get("max_favorable_amount", 0.0))
    return _current_unrealized_amount(entry_price, direction, row, cost)


def _tier_reference_points(pos: dict, entry_price: float, direction: int, row: pd.Series, p) -> float:
    ref = str(getattr(p, "profit_tier_reference", "current_unrealized") or "current_unrealized").lower()
    if ref in {"max_favorable", "max_floating_profit", "max_profit", "peak_profit"}:
        return float(pos.get("max_favorable_points", 0.0))
    return _current_unrealized_points(entry_price, direction, row)


def _positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number) or number <= 0:
        return None
    return number


def _profit_tier_mode(p) -> str:
    mode = str(getattr(p, "profit_tier_threshold_mode", "amount") or "amount").lower()
    return "entry_atr" if mode in {"entry_atr", "atr", "atr_multiple", "normalized_atr"} else "amount"


def _tier_reference_value(pos: dict, entry_price: float, direction: int, row: pd.Series,
                          cost: CostModel, p) -> float:
    """回傳目前分段比較值；ATR 模式使用進場前已完成 K 棒 ATR，避免未來函數。"""
    if _profit_tier_mode(p) == "entry_atr":
        entry_atr = _positive_float(pos.get("entry_atr"))
        if entry_atr is None:
            return 0.0
        return _tier_reference_points(pos, entry_price, direction, row, p) / entry_atr
    return _tier_reference_amount(pos, entry_price, direction, row, cost, p)


def _tier_thresholds(p):
    if _profit_tier_mode(p) == "entry_atr":
        return getattr(p, "profit_tier_atr_multiples", ())
    return getattr(p, "profit_tier_amounts", ())


def _chandelier_suffix(mult: float) -> str:
    return str(float(mult)).replace(".", "_").replace("-", "m")


def _select_profit_tier_mult(reference_value: float, thresholds_raw, mults, fallback: float) -> float:
    """依目前分段比較值選擇吊燈倍數；門檻需升冪，倍數數量須多一段。"""
    try:
        thresholds = [float(x) for x in (thresholds_raw or [])]
        values = [float(x) for x in (mults or [])]
    except Exception:
        return float(fallback)
    if not values or len(values) != len(thresholds) + 1:
        return float(fallback)
    tier = 0
    for th in thresholds:
        if float(reference_value) >= th:
            tier += 1
        else:
            break
    return values[min(tier, len(values) - 1)]


def run_backtest(df: pd.DataFrame, cost: CostModel, p) -> tuple:
    """
    df   : strategies 產出的 DataFrame（需含 datetime/OHLC/long_entry/short_entry；
           出場用欄位 macd_hist / chandelier_long / chandelier_short 視開關而定）
    cost : CostModel
    p    : StrategyParams（出場開關與點數）
    回傳 (trades_df, equity_df)
    """
    required = ["datetime", "open", "high", "low", "close",
                "long_entry", "short_entry"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"回測資料缺少欄位: {missing}")

    df = df.reset_index(drop=True)
    n = len(df)
    trades = []
    equity_rows = []
    pos = None        # 目前持倉 dict
    pending = None    # 前一根收盤產生、等待下一根開盤執行的訊號 dict
    realized = 0.0    # 已實現累積損益（元）

    for i in range(n):
        row = df.iloc[i]
        dt = row["datetime"]

        # ---- 1) 執行前一根收盤的進場訊號：本根開盤進場 ----
        if pos is None and pending is not None:
            d = 1 if pending["direction"] == "long" else -1
            entry_price = row["open"] + d * cost.slippage_points  # 不利方向滑價
            pos = {
                "direction": d,
                "entry_price": entry_price,
                "entry_i": i,
                "entry_date": dt,                 # 相容舊欄位：實際進場日
                "entry_execution_date": dt,       # v0.3 明確標示執行日
                "signal_i": pending["signal_i"],
                "signal_date": pending["signal_date"],
                "entry_reason": pending["entry_reason"],
                # 進場發生在本根開盤，不能使用本根尚未完成的 ATR；固定保存訊號根 ATR。
                "entry_atr": pending.get("signal_atr"),
                "highest": row["high"],  # 供移動停損用（本根結束後才生效）
                "lowest": row["low"],
                "max_adverse_points": _adverse_points(entry_price, d, row),
                "max_favorable_points": _favorable_points(entry_price, d, row),
                "max_favorable_amount": _favorable_points(entry_price, d, row) * cost.point_value * cost.quantity,
            }
            pending = None

        # ---- 2) 出場判斷 ----
        exit_price, exit_reason = None, None
        if pos is not None:
            d, ep = pos["direction"], pos["entry_price"]
            pos["max_adverse_points"] = max(
                float(pos.get("max_adverse_points", 0.0)),
                _adverse_points(ep, d, row),
            )
            pos["max_favorable_points"] = max(
                float(pos.get("max_favorable_points", 0.0)),
                _favorable_points(ep, d, row),
            )
            pos["max_favorable_amount"] = float(pos.get("max_favorable_points", 0.0)) * cost.point_value * cost.quantity
            margin_line = _margin_call_line(ep, d, cost)

            # a0) 斷頭開盤跳空：開盤已吃光安全緩衝金額，優先視同斷頭強制平倉
            if margin_line is not None:
                if d == 1 and row["open"] <= margin_line:
                    exit_price = row["open"]
                    exit_reason = "margin_call"
                elif d == -1 and row["open"] >= margin_line:
                    exit_price = row["open"]
                    exit_reason = "margin_call"

            # a) 固定停損（盤中觸價）
            if exit_price is None and p.use_fixed_stop:
                stop = ep - d * p.stop_points
                if d == 1 and row["low"] <= stop:
                    exit_price = min(row["open"], stop)
                    exit_reason = "fixed_stop"
                elif d == -1 and row["high"] >= stop:
                    exit_price = max(row["open"], stop)
                    exit_reason = "fixed_stop"

            # b) 固定停利（盤中觸價）
            if exit_price is None and p.use_take_profit:
                tp = ep + d * p.take_profit_points
                if d == 1 and row["high"] >= tp:
                    exit_price = max(row["open"], tp)
                    exit_reason = "take_profit"
                elif d == -1 and row["low"] <= tp:
                    exit_price = min(row["open"], tp)
                    exit_reason = "take_profit"

            # c) 移動停損（用進場後、前一根以前的極值追蹤；進場當根不觸發）
            if exit_price is None and p.use_trailing_stop and i > pos["entry_i"]:
                if d == 1:
                    trail = pos["highest"] - p.trailing_points
                    if row["low"] <= trail:
                        exit_price = min(row["open"], trail)
                        exit_reason = "trailing_stop"
                else:
                    trail = pos["lowest"] + p.trailing_points
                    if row["high"] >= trail:
                        exit_price = max(row["open"], trail)
                        exit_reason = "trailing_stop"

            # c2) 斷頭盤中觸價：安全緩衝金額被吃光，視同斷頭強制平倉
            if exit_price is None and margin_line is not None:
                if d == 1 and row["low"] <= margin_line:
                    exit_price = margin_line
                    exit_reason = "margin_call"
                elif d == -1 and row["high"] >= margin_line:
                    exit_price = margin_line
                    exit_reason = "margin_call"

            # d) 吊燈出場（收盤確認）
            # v0.5.0：支援「獲利分段吊燈」：依目前浮盈金額選擇不同吊燈倍數。
            if exit_price is None and (p.use_chandelier or getattr(p, "use_profit_tier_chandelier", False)):
                ch_label = "chandelier"
                if getattr(p, "use_profit_tier_chandelier", False):
                    tier_value = _tier_reference_value(pos, ep, d, row, cost, p)
                    mult = _select_profit_tier_mult(
                        tier_value,
                        _tier_thresholds(p),
                        getattr(p, "profit_tier_mults", ()),
                        getattr(p, "chandelier_mult", 3.0),
                    )
                    suf = _chandelier_suffix(mult)
                    long_col = f"chandelier_long_m_{suf}"
                    short_col = f"chandelier_short_m_{suf}"
                    ch_label = f"profit_tier_chandelier_{mult:g}"
                else:
                    long_col = "chandelier_long"
                    short_col = "chandelier_short"

                if d == 1:
                    ch = row.get(long_col)
                    if pd.notna(ch) and row["close"] < ch:
                        exit_price, exit_reason = row["close"], ch_label
                else:
                    ch = row.get(short_col)
                    if pd.notna(ch) and row["close"] > ch:
                        exit_price, exit_reason = row["close"], ch_label

            # e) MACD 反向（收盤確認）：多單 hist<0 出場、空單 hist>0 出場
            # v0.5.1：若已達指定最高浮盈，可排除 MACD 反向，避免獲利擴大後被短期反向訊號提前洗出。
            macd_reverse_blocked = False
            if exit_price is None and getattr(p, "use_profit_scaled_macd_exclusion", False):
                if _profit_tier_mode(p) == "entry_atr":
                    block_at = float(getattr(p, "macd_reverse_exclude_atr_multiple", 0.0) or 0.0)
                else:
                    block_at = float(getattr(p, "macd_reverse_exclude_profit_amount", 0.0) or 0.0)
                ref_value = _tier_reference_value(pos, ep, d, row, cost, p)
                if block_at > 0 and ref_value >= block_at:
                    macd_reverse_blocked = True

            if exit_price is None and p.use_macd_reverse and (not macd_reverse_blocked) and "macd_hist" in df.columns:
                h = row["macd_hist"]
                if pd.notna(h) and ((d == 1 and h < 0) or (d == -1 and h > 0)):
                    exit_price, exit_reason = row["close"], "macd_reverse"

            # e2) 條件出場（收盤確認；v0.3.4 新增、可選）
            #     只有 params.use_signal_exit=True 且策略層有產生
            #     exit_long_signal / exit_short_signal 欄位時才會啟用，
            #     否則完全不影響既有出場行為（self_check 8 cases 不變）。
            if exit_price is None and getattr(p, "use_signal_exit", False):
                sig_col = "exit_long_signal" if d == 1 else "exit_short_signal"
                if sig_col in df.columns and bool(row.get(sig_col, False)):
                    exit_price, exit_reason = row["close"], "signal_exit"

            # f) 資料結束強制平倉
            if exit_price is None and i == n - 1:
                exit_price, exit_reason = row["close"], "end_of_data"

        # ---- 3) 結算出場 ----
        if pos is not None and exit_price is not None:
            d = pos["direction"]
            px = exit_price - d * cost.slippage_points  # 出場滑價（不利方向）
            pts = (px - pos["entry_price"]) * d
            q = cost.quantity
            tax = (pos["entry_price"] + px) * cost.point_value * cost.tax_rate * q
            amount = pts * cost.point_value * q - 2 * cost.fee * q - tax
            max_adverse_points = float(pos.get("max_adverse_points", 0.0))
            max_adverse_amount = max_adverse_points * cost.point_value * q
            max_favorable_points = float(pos.get("max_favorable_points", 0.0))
            max_favorable_amount = max_favorable_points * cost.point_value * q
            entry_atr = _positive_float(pos.get("entry_atr"))
            max_favorable_atr_multiple = (max_favorable_points / entry_atr) if entry_atr else None
            required_safety_capital = cost.original_margin_amount + max_adverse_amount
            trades.append({
                "signal_date": pos["signal_date"],
                "signal_bar_index": pos["signal_i"],
                "entry_date": pos["entry_date"],
                "entry_execution_date": pos["entry_execution_date"],
                "entry_bar_index": pos["entry_i"],
                "exit_date": dt,
                "exit_bar_index": i,
                "direction": "long" if d == 1 else "short",
                "entry_price": round(pos["entry_price"], 2),
                "exit_price": round(px, 2),
                "quantity": q,
                "pnl_points": round(pts, 2),
                "pnl_amount": round(amount, 1),
                "holding_bars": i - pos["entry_i"] + 1,
                "exit_reason": exit_reason,
                "entry_reason": pos["entry_reason"],
                "max_adverse_points": round(max_adverse_points, 2),
                "max_adverse_amount": round(max_adverse_amount, 1),
                "max_favorable_points": round(max_favorable_points, 2),
                "max_favorable_amount": round(max_favorable_amount, 1),
                "entry_atr": round(entry_atr, 4) if entry_atr else None,
                "max_favorable_atr_multiple": round(max_favorable_atr_multiple, 4)
                    if max_favorable_atr_multiple is not None else None,
                "required_safety_capital": round(required_safety_capital, 1),
            })
            realized += amount
            pos = None

        # ---- 4) 收盤訊號 -> 下一根開盤進場（空手才接單）----
        if pos is None and pending is None and i < n - 1:
            if bool(row["long_entry"]):
                pending = {
                    "direction": "long",
                    "signal_i": i,
                    "signal_date": dt,
                    "entry_reason": _entry_reason(row, "long"),
                    "signal_atr": _positive_float(row.get("atr")),
                }
            elif bool(row["short_entry"]):
                pending = {
                    "direction": "short",
                    "signal_i": i,
                    "signal_date": dt,
                    "entry_reason": _entry_reason(row, "short"),
                    "signal_atr": _positive_float(row.get("atr")),
                }

        # ---- 5) 更新移動停損追蹤極值（本根結束後生效，避免盤中未來函數）----
        if pos is not None:
            pos["highest"] = max(pos["highest"], row["high"])
            pos["lowest"] = min(pos["lowest"], row["low"])
            pos["max_adverse_points"] = max(
                float(pos.get("max_adverse_points", 0.0)),
                _adverse_points(pos["entry_price"], pos["direction"], row),
            )
            pos["max_favorable_points"] = max(
                float(pos.get("max_favorable_points", 0.0)),
                _favorable_points(pos["entry_price"], pos["direction"], row),
            )
            pos["max_favorable_amount"] = float(pos.get("max_favorable_points", 0.0)) * cost.point_value * cost.quantity

        # ---- 6) 權益曲線（已實現 + 未實現以收盤價估）----
        unreal = 0.0
        if pos is not None:
            unreal = ((row["close"] - pos["entry_price"]) * pos["direction"]
                      * cost.point_value * cost.quantity)
        equity_rows.append({"datetime": dt, "equity": realized + unreal})

    trades_df = pd.DataFrame(trades, columns=[
        "signal_date", "signal_bar_index",
        "entry_date", "entry_execution_date", "entry_bar_index",
        "exit_date", "exit_bar_index", "direction",
        "entry_price", "exit_price", "quantity",
        "pnl_points", "pnl_amount", "holding_bars",
        "exit_reason", "entry_reason",
        "max_adverse_points", "max_adverse_amount",
        "max_favorable_points", "max_favorable_amount",
        "entry_atr", "max_favorable_atr_multiple",
        "required_safety_capital"])
    equity_df = pd.DataFrame(equity_rows)
    return trades_df, equity_df
