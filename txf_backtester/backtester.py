# -*- coding: utf-8 -*-
"""
backtester.py - 單一持倉回測引擎（避免 look-ahead bias）。

規則：
- 進場：第 i 根「收盤」訊號成立 -> 第 i+1 根「開盤價」進場（含滑價）
- 出場優先順序（同一根 K 棒內）：
    斷頭開盤跳空 > 固定停損 > 固定停利 > 移動停損 > SAR移動停損
    > 斷頭盤中觸價 > 吊燈出場 > MACD 反向 > 條件出場
  * v0.5.1 起可設定「獲利放大後排除 MACD 反向」，讓分段吊燈真正主導後段出場。
  * v0.6.5 起支援以「最大順向浮盈 ÷ 進場前已完成 K 棒 ATR」作為相對市場波動階梯。
  * v0.6.6 起支援「進場 ATR × 倍數」停損，並可在預定停損風險超過上限時略過進場。
  * v0.6.7 起支援 50 萬資金池、固定風險比例與小台/微台等值動態部位。
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
from types import SimpleNamespace

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


def _directional_params(p, direction: str):
    """套用多單／空單獨立出場覆寫；未指定欄位沿用共用設定。"""
    base = dict(vars(p)) if hasattr(p, "__dict__") else {}
    key = "long_exit_overrides" if str(direction).lower() == "long" else "short_exit_overrides"
    overrides = getattr(p, key, {}) or {}
    if not isinstance(overrides, dict):
        overrides = {}
    base.update(overrides)
    return SimpleNamespace(**base)


def _entry_group_params(p, direction: str, group_index: int | None):
    """在方向別設定之上，再套用實際進場 OR 組合的出場覆寫。

    group_index 為 None 或未設定覆寫時，與 v0.8.6.7 完全相同。
    JSON 物件鍵一律是字串，因此同時接受 int/str 查找。
    """
    directional = _directional_params(p, direction)
    base = dict(vars(directional)) if hasattr(directional, "__dict__") else {}
    all_overrides = getattr(directional, "entry_group_exit_overrides", {}) or {}
    if not isinstance(all_overrides, dict) or group_index is None:
        return SimpleNamespace(**base)
    overrides = all_overrides.get(str(int(group_index)))
    if overrides is None:
        overrides = all_overrides.get(int(group_index))
    if isinstance(overrides, dict):
        # 禁止覆寫群組映射本身，避免遞迴或污染後續持倉。
        overrides = {k: v for k, v in overrides.items()
                     if k != "entry_group_exit_overrides"}
        base.update(overrides)
    return SimpleNamespace(**base)


def _entry_reason(row: pd.Series, direction: str) -> str:
    """從策略訊號列取出進場條件說明；若舊策略沒有 reasons 欄，仍可相容。"""
    col = "long_entry_reasons" if direction == "long" else "short_entry_reasons"
    reason = row.get(col, "")
    if pd.isna(reason) or str(reason).strip() == "":
        return "long_entry" if direction == "long" else "short_entry"
    return str(reason)



def _triggered_entry_groups(row: pd.Series, direction: str) -> list[int]:
    """取得當根實際成立的 OR 組合編號。舊資料沒有群組欄位時回傳空清單。"""
    prefix = "long_entry_group_" if direction == "long" else "short_entry_group_"
    groups = []
    for col in row.index:
        name = str(col)
        if not name.startswith(prefix) or "reason" in name:
            continue
        try:
            idx = int(name[len(prefix):])
        except ValueError:
            continue
        if bool(row.get(col, False)):
            groups.append(idx)
    return sorted(groups)


def _entry_reason_for_group(row: pd.Series, direction: str, group_index: int | None) -> str:
    if group_index is None:
        return _entry_reason(row, direction)
    col = f"{direction}_entry_group_reason_{int(group_index)}"
    reason = row.get(col, "")
    if pd.isna(reason) or str(reason).strip() == "":
        return _entry_reason(row, direction)
    return str(reason)

def _margin_call_line(entry_price: float, direction: int, cost: CostModel,
                      pos: dict | None = None, realized: float = 0.0, p=None) -> float | None:
    """回傳斷頭判斷價格。

    v0.6.7 動態部位使用帳戶權益與維持保證金；舊策略仍沿用安全緩衝模型。
    """
    if pos is not None and p is not None and (getattr(p, "use_dynamic_position_sizing", False)
                                                or getattr(p, "use_regime_position_sizing", False)
                                                or getattr(p, "use_account_margin_model", False)):
        capital = _positive_float(getattr(p, "position_sizing_capital", None))
        maintenance = _positive_float(pos.get("maintenance_margin_amount"))
        total_point_value = _positive_float(pos.get("point_value_total"))
        if capital is None or maintenance is None or total_point_value is None:
            return None
        loss_capacity = capital + float(realized) - maintenance
        if loss_capacity <= 0:
            return float(entry_price)
        return entry_price - direction * (loss_capacity / total_point_value)
    if not cost.use_margin_call_check or cost.safety_buffer_amount < 0:
        return None
    total_point_value = _position_point_value(pos, cost)
    buffer_points = float(cost.safety_buffer_amount) / total_point_value
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


def _current_unrealized_amount(entry_price: float, direction: int, row: pd.Series,
                               cost: CostModel, pos: dict | None = None) -> float:
    return _current_unrealized_points(entry_price, direction, row) * _position_point_value(pos, cost)


def _tier_reference_amount(pos: dict, entry_price: float, direction: int, row: pd.Series, cost: CostModel, p) -> float:
    """回傳獲利分段依據金額。

    current_unrealized：用當根收盤浮盈；
    max_favorable：用持倉以來最高順向浮盈，較符合「獲利放大後同步放大出場條件」。
    """
    ref = str(getattr(p, "profit_tier_reference", "current_unrealized") or "current_unrealized").lower()
    if ref in {"max_favorable", "max_floating_profit", "max_profit", "peak_profit"}:
        return float(pos.get("max_favorable_amount", 0.0))
    return _current_unrealized_amount(entry_price, direction, row, cost, pos)


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


def _sizing_available_equity(p, realized: float = 0.0) -> tuple[float, str]:
    """回傳部位計算使用的權益與口徑名稱。

    position_compounding=False（預設）保留舊行為：獲利不放大原始資金池，
    但虧損會降低下一筆可用權益；True 才啟用完整複利。
    """
    capital = _positive_float(getattr(p, "position_sizing_capital", None))
    if capital is None:
        return 0.0, "invalid"
    actual_equity = max(capital + float(realized), 0.0)
    if bool(getattr(p, "position_compounding", False)):
        return actual_equity, "compounding"
    return min(capital, actual_equity), "legacy_no_profit_compounding"


def _drawdown_risk_brake_multiplier(p, realized: float = 0.0,
                                        realized_peak_equity: float | None = None) -> tuple[float, float, float]:
    """v0.8.4 已實現權益回撤煞車。

    回傳 (風險乘數, 已實現權益回撤百分比, 已實現權益前高)。
    只影響 dynamic_risk 的新進場部位，不會在持倉中途偷偷改口數。
    """
    capital = _positive_float(getattr(p, "position_sizing_capital", None)) or 0.0
    current_equity = max(float(capital) + float(realized), 0.0)
    peak = max(float(realized_peak_equity or current_equity), current_equity, 1e-9)
    drawdown_pct = max((peak - current_equity) / peak * 100.0, 0.0)
    if not bool(getattr(p, "use_drawdown_risk_brake", False)):
        return 1.0, drawdown_pct, peak
    start = max(float(getattr(p, "position_drawdown_brake_start_pct", 0.0) or 0.0), 0.0)
    full = max(float(getattr(p, "position_drawdown_brake_full_pct", start) or start), start)
    floor = min(max(float(getattr(p, "position_drawdown_brake_floor", 1.0) or 1.0), 0.0), 1.0)
    if drawdown_pct <= start or full <= start:
        multiplier = 1.0 if drawdown_pct <= start else floor
    elif drawdown_pct >= full:
        multiplier = floor
    else:
        progress = (drawdown_pct - start) / (full - start)
        multiplier = 1.0 - progress * (1.0 - floor)
    return max(float(multiplier), 0.0), drawdown_pct, peak


def _stop_threshold_mode(p) -> str:
    mode = str(getattr(p, "stop_threshold_mode", "points") or "points").lower()
    if mode in {"entry_atr", "atr", "atr_multiple", "normalized_atr"}:
        return "entry_atr"
    if mode in {"entry_pct", "pct", "percent", "percentage"}:
        return "entry_pct"
    return "points"


def _stop_distance_points(p, entry_atr, entry_price=None) -> float | None:
    """回傳本筆預定停損距離；ATR使用訊號根，百分比使用實際進場價。"""
    mode = _stop_threshold_mode(p)
    if mode == "entry_atr":
        atr_value = _positive_float(entry_atr)
        multiple = _positive_float(getattr(p, "stop_atr_multiple", None))
        if atr_value is None or multiple is None:
            return None
        return atr_value * multiple
    if mode == "entry_pct":
        price = _positive_float(entry_price)
        pct = _positive_float(getattr(p, "stop_entry_pct", None))
        if price is None or pct is None:
            return None
        return price * pct / 100.0
    return _positive_float(getattr(p, "stop_points", None))


def _planned_stop_risk_amount(stop_distance_points, cost: CostModel, point_value_total: float | None = None) -> float | None:
    distance = _positive_float(stop_distance_points)
    if distance is None:
        return None
    total_value = point_value_total
    if total_value is None:
        total_value = float(cost.point_value) * max(int(cost.quantity), 1)
    return distance * float(total_value)


def _position_point_value(pos: dict | None, cost: CostModel) -> float:
    if pos is not None:
        value = _positive_float(pos.get("point_value_total"))
        if value is not None:
            return value
    return float(cost.point_value) * max(int(cost.quantity), 1)


def _position_fee_per_side(pos: dict | None, cost: CostModel) -> float:
    if pos is not None:
        try:
            return float(pos.get("fee_per_side_total", 0.0))
        except (TypeError, ValueError):
            pass
    return float(cost.fee) * max(int(cost.quantity), 1)


def _fixed_position_spec(cost: CostModel, p) -> dict:
    q = max(int(cost.quantity), 1)
    total_point_value = float(cost.point_value) * q
    # MTX 固定部位視為小台；其他商品退回原始保證金×口數。
    small_point_value = _positive_float(getattr(p, "position_small_point_value", 50.0)) or 50.0
    if abs(float(cost.point_value) - small_point_value) < 1e-9:
        small_qty, micro_qty = q, 0
        margin_per_contract = (_positive_float(getattr(p, "position_small_margin", None))
                               or float(cost.original_margin_amount))
        maintenance_per_contract = (_positive_float(getattr(p, "position_small_maintenance_margin", None))
                                    or margin_per_contract * 0.77)
        margin = margin_per_contract * q
        maintenance = maintenance_per_contract * q
    else:
        small_qty, micro_qty = 0, q
        margin = float(cost.original_margin_amount) * q
        maintenance = margin * 0.77
    return {
        "micro_units": int(round(total_point_value / max(_positive_float(getattr(p, "position_micro_point_value", 10.0)) or 10.0, 1e-9))),
        "large_qty": 0,
        "small_qty": int(small_qty),
        "micro_qty": int(micro_qty),
        "small_equivalent_quantity": total_point_value / small_point_value,
        "point_value_total": total_point_value,
        "fee_per_side_total": float(cost.fee) * q,
        "margin_amount": float(margin),
        "maintenance_margin_amount": float(maintenance),
        "risk_budget_amount": None,
        "stress_risk_amount": None,
        "stress_multiple": None,
        "position_sizing_mode": "fixed",
    }


def _contract_mix_from_micro_units(units: int, p, cost: CostModel | None = None) -> dict:
    """把微台等值曝險轉為實際契約組合。

    v0.8.2 ``min_contract_count`` 模式依序使用大台、小台、微台，
    在不改變總點值曝險的前提下最小化總口數。預設仍保留舊版
    ``small_micro_only``，避免舊策略未宣告時改變成本口徑。
    """
    units = max(int(units), 0)
    micro_pv = _positive_float(getattr(p, "position_micro_point_value", 10.0)) or 10.0
    small_pv = _positive_float(getattr(p, "position_small_point_value", 50.0)) or 50.0
    large_pv = _positive_float(getattr(p, "position_large_point_value", 200.0)) or 200.0
    small_ratio = max(int(round(small_pv / micro_pv)), 1)
    large_ratio = max(int(round(large_pv / micro_pv)), 1)
    mode = str(getattr(p, "position_contract_mix_mode", "small_micro_only") or "small_micro_only").lower()
    max_contract_pv = (_positive_float(getattr(p, "position_max_contract_point_value", large_pv))
                       or large_pv)

    large_qty = 0
    remaining = units
    if mode in {"min_contract_count", "min_contracts", "auto_min_contracts"}:
        if max_contract_pv + 1e-9 >= large_pv:
            large_qty = remaining // large_ratio
            remaining %= large_ratio
        small_qty = remaining // small_ratio if max_contract_pv + 1e-9 >= small_pv else 0
        remaining -= small_qty * small_ratio
        micro_qty = remaining
    else:
        small_qty = remaining // small_ratio
        micro_qty = remaining % small_ratio

    total_point_value = large_qty * large_pv + small_qty * small_pv + micro_qty * micro_pv
    large_margin = _positive_float(getattr(p, "position_large_margin", 636000.0)) or 636000.0
    small_margin = _positive_float(getattr(p, "position_small_margin", 159000.0)) or 159000.0
    micro_margin = _positive_float(getattr(p, "position_micro_margin", 32000.0)) or 32000.0
    large_maint = _positive_float(getattr(p, "position_large_maintenance_margin", 488000.0)) or 488000.0
    small_maint = _positive_float(getattr(p, "position_small_maintenance_margin", 122000.0)) or 122000.0
    micro_maint = _positive_float(getattr(p, "position_micro_maintenance_margin", 24400.0)) or 24400.0
    large_fee = max(float(getattr(p, "position_large_fee", 50.0) or 0.0), 0.0)
    small_fee = max(float(getattr(p, "position_small_fee", 20.0) or 0.0), 0.0)
    micro_fee = max(float(getattr(p, "position_micro_fee", 12.0) or 0.0), 0.0)
    return {
        "micro_units": units,
        "large_qty": int(large_qty),
        "small_qty": int(small_qty),
        "micro_qty": int(micro_qty),
        "small_equivalent_quantity": total_point_value / small_pv if small_pv else 0.0,
        "point_value_total": total_point_value,
        "fee_per_side_total": large_qty * large_fee + small_qty * small_fee + micro_qty * micro_fee,
        "margin_amount": large_qty * large_margin + small_qty * small_margin + micro_qty * micro_margin,
        "maintenance_margin_amount": large_qty * large_maint + small_qty * small_maint + micro_qty * micro_maint,
        "position_equity_basis": "fixed",
        "position_compounding": False,
        "position_contract_mix_mode": mode,
        "position_max_contract_point_value": max_contract_pv,
    }


def _dynamic_position_spec(stop_distance_points, p, realized: float = 0.0,
                           cost: CostModel | None = None,
                           realized_peak_equity: float | None = None) -> dict | None:
    """依停損距離與權益風險率決定部位，支援已實現權益回撤煞車。"""
    distance = _positive_float(stop_distance_points)
    capital = _positive_float(getattr(p, "position_sizing_capital", None))
    risk_fraction = _positive_float(getattr(p, "position_risk_fraction", None))
    micro_pv = _positive_float(getattr(p, "position_micro_point_value", 10.0)) or 10.0
    if distance is None or capital is None or risk_fraction is None:
        return None
    available_equity, equity_basis = _sizing_available_equity(p, realized)
    if available_equity <= 0:
        return None
    brake_multiplier, realized_dd_pct, realized_peak = _drawdown_risk_brake_multiplier(
        p, realized, realized_peak_equity)
    effective_risk_fraction = risk_fraction * brake_multiplier
    risk_budget = available_equity * effective_risk_fraction
    per_unit_risk = distance * micro_pv
    if per_unit_risk <= 0 or risk_budget <= 0:
        return None
    raw_units = int(risk_budget // per_unit_risk)
    max_units = max(int(getattr(p, "position_max_micro_units", 10) or 0), 0)
    # 0代表不設人為口數上限；實際曝險仍受風險預算、保證金與壓力檢查限制。
    units = min(raw_units, max_units) if max_units > 0 else raw_units
    if units <= 0:
        return None
    stress_multiple = _positive_float(getattr(p, "position_stress_multiple", 4.0)) or 4.0
    use_stress = bool(getattr(p, "position_use_stress_capital_check", True))

    def candidate(unit_count: int):
        spec = _contract_mix_from_micro_units(unit_count, p, cost)
        normal_risk = distance * float(spec["point_value_total"])
        stress_risk = normal_risk * stress_multiple
        required = float(spec["margin_amount"]) + (stress_risk if use_stress else 0.0)
        return spec, normal_risk, stress_risk, required

    # 保證金與風險隨曝險單調增加，二分搜尋最大可行部位。
    lo, hi, best = 1, int(units), None
    while lo <= hi:
        mid = (lo + hi) // 2
        spec, normal_risk, stress_risk, required = candidate(mid)
        if required <= available_equity:
            best = (mid, spec, normal_risk, stress_risk)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    unit_count, spec, normal_risk, stress_risk = best
    spec.update({
        "risk_budget_amount": risk_budget,
        "base_risk_fraction": risk_fraction,
        "effective_risk_fraction": effective_risk_fraction,
        "drawdown_brake_multiplier": brake_multiplier,
        "realized_equity_drawdown_pct": realized_dd_pct,
        "realized_equity_peak": realized_peak,
        "planned_stop_risk_amount": normal_risk,
        "stress_risk_amount": stress_risk,
        "stress_multiple": stress_multiple,
        "available_equity_at_entry": available_equity,
        "position_equity_basis": equity_basis,
        "position_compounding": bool(getattr(p, "position_compounding", False)),
        "position_sizing_mode": "dynamic_risk",
        "requested_micro_units": int(raw_units),
    })
    return spec


def _regime_position_spec(target_units, stop_distance_points, p, realized: float = 0.0, cost: CostModel | None = None) -> dict | None:
    """v0.7.0 核心＋條件式加碼部位。

    目標部位由訊號根的盤勢條件決定；ATR只負責估算停損與壓力風險，
    不會因高波動自動把一口核心部位縮成微台。若資金/壓力檢查不通過，
    可依 position_allow_downsize 逐一降低微台等值單位。
    """
    try:
        units = max(int(target_units or 0), 0)
    except (TypeError, ValueError):
        return None
    max_units = max(int(getattr(p, "position_max_micro_units", 10) or 0), 0)
    units = min(units, max_units)
    if units <= 0:
        return None
    capital = _positive_float(getattr(p, "position_sizing_capital", None))
    if capital is None:
        return None
    available_equity, equity_basis = _sizing_available_equity(p, realized)
    if available_equity <= 0:
        return None
    distance = _positive_float(stop_distance_points)
    stress_multiple = _positive_float(getattr(p, "position_stress_multiple", 4.0)) or 4.0
    use_stress = bool(getattr(p, "position_use_stress_capital_check", True))
    allow_downsize = bool(getattr(p, "position_allow_downsize", True))
    while units > 0:
        spec = _contract_mix_from_micro_units(units, p, cost)
        normal_risk = distance * float(spec["point_value_total"]) if distance is not None else 0.0
        stress_risk = normal_risk * stress_multiple
        margin_ok = float(spec["margin_amount"]) <= available_equity
        stress_ok = (not use_stress) or (float(spec["margin_amount"]) + stress_risk <= available_equity)
        if margin_ok and stress_ok:
            spec.update({
                "risk_budget_amount": None,
                "planned_stop_risk_amount": normal_risk if distance is not None else None,
                "stress_risk_amount": stress_risk if distance is not None else None,
                "stress_multiple": stress_multiple if distance is not None else None,
                "available_equity_at_entry": available_equity,
                "position_equity_basis": equity_basis,
                "position_compounding": bool(getattr(p, "position_compounding", False)),
                "position_sizing_mode": "core_regime",
                "requested_micro_units": int(target_units or 0),
            })
            return spec
        if not allow_downsize:
            return None
        units -= 1
    return None


def _safe_capital_position_spec(stop_distance_points, p, realized: float = 0.0, cost: CostModel | None = None) -> dict | None:
    """v0.8.1 可選複利的安全資金單位部位。"""
    capital = _positive_float(getattr(p, "position_sizing_capital", None))
    unit_capital = _positive_float(getattr(p, "position_safe_capital_per_micro_unit", None))
    if capital is None or unit_capital is None:
        return None
    available_equity, equity_basis = _sizing_available_equity(p, realized)
    min_buffer = max(float(getattr(p, "position_min_cash_buffer", 0.0) or 0.0), 0.0)
    allocatable = max(available_equity - min_buffer, 0.0)
    raw_units = int(allocatable // unit_capital)
    max_units = max(int(getattr(p, "position_max_micro_units", 10) or 0), 0)
    max_small = max(int(getattr(p, "position_max_small_contracts", 10) or 0), 0)
    small_pv = _positive_float(getattr(p, "position_small_point_value", 50.0)) or 50.0
    micro_pv = _positive_float(getattr(p, "position_micro_point_value", 10.0)) or 10.0
    ratio = max(int(round(small_pv / micro_pv)), 1)
    # v0.8.2：明確設為0代表不使用人為曝險上限，實際口數只受安全資金、
    # 保證金、停損、跳空與回撤準備限制。正整數才視為上限。
    units = raw_units
    if max_units > 0:
        units = min(units, max_units)
    if max_small > 0:
        units = min(units, max_small * ratio)
    distance = _positive_float(stop_distance_points)
    stress_multiple = _positive_float(getattr(p, "position_stress_multiple", 4.0)) or 4.0
    gap_points = max(float(getattr(p, "position_gap_stress_points", 0.0) or 0.0), 0.0)
    dd_fraction = max(float(getattr(p, "position_drawdown_reserve_fraction", 0.0) or 0.0), 0.0)
    use_stress = bool(getattr(p, "position_use_stress_capital_check", True))
    drawdown_reserve = available_equity * dd_fraction

    def candidate(unit_count: int):
        spec = _contract_mix_from_micro_units(unit_count, p, cost)
        pv = float(spec["point_value_total"])
        normal_risk = distance * pv if distance is not None else 0.0
        stress_risk = max(normal_risk * stress_multiple, gap_points * pv)
        required = float(spec["margin_amount"]) + min_buffer + drawdown_reserve
        if use_stress:
            required += stress_risk
        return spec, normal_risk, stress_risk, required

    # v0.8.3：安全條件對曝險單位具單調性，以二分搜尋取代逐口遞減。
    # 複利帳戶成長到數百／數千單位時，不會因 while units -= 1 卡住回測。
    lo, hi, best = 1, int(units), None
    while lo <= hi:
        mid = (lo + hi) // 2
        spec, normal_risk, stress_risk, required = candidate(mid)
        if required <= available_equity:
            best = (mid, spec, normal_risk, stress_risk)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    units, spec, normal_risk, stress_risk = best
    safe_used = units * unit_capital
    spec.update({
        "risk_budget_amount": None,
        "planned_stop_risk_amount": normal_risk if distance is not None else None,
        "stress_risk_amount": stress_risk,
        "stress_multiple": stress_multiple,
        "gap_stress_points": gap_points,
        "drawdown_reserve_amount": drawdown_reserve,
        "available_equity_at_entry": available_equity,
        "position_equity_basis": equity_basis,
        "position_compounding": bool(getattr(p, "position_compounding", False)),
        "position_sizing_mode": str(getattr(p, "position_sizing_mode", "dynamic_safe_capital")),
        "safe_capital_per_micro_unit": unit_capital,
        "safe_capital_used": safe_used,
        "safe_capital_balance": available_equity - safe_used,
        "margin_utilization_pct": float(spec["margin_amount"]) / available_equity * 100.0,
    })
    return spec


def _confirmed_exit(pos: dict, key: str, raw_hit: bool, required_bars: int) -> bool:
    """回傳收盤型出場是否已連續確認足夠根數。"""
    required = max(int(required_bars or 1), 1)
    count_key = f"_{key}_confirm_count"
    if raw_hit:
        pos[count_key] = int(pos.get(count_key, 0)) + 1
    else:
        pos[count_key] = 0
    return int(pos[count_key]) >= required


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
    initial_sizing_capital = float(getattr(p, "position_sizing_capital", 0.0) or 0.0)
    realized_peak_equity = max(initial_sizing_capital, 0.0)
    risk_cap_skipped_entries = 0
    missing_atr_skipped_entries = 0
    dynamic_size_skipped_entries = 0
    last_entry_units = 0
    account_disabled = False
    bollinger_reentry_locked = False
    bollinger_reentry_lock_start_i = None
    bollinger_reentry_lock_activations = 0
    bollinger_reentry_blocked_signals = 0
    reset_groups = {int(x) for x in (getattr(
        p, "bollinger_reentry_long_group_indices", ()) or ())}
    use_bollinger_reset = bool(getattr(
        p, "use_bollinger_reentry_reset_after_fixed_stop", False)) and bool(reset_groups)

    for i in range(n):
        row = df.iloc[i]
        dt = row["datetime"]

        # v0.8.6.6：布林入口固定停損後，必須等「出場後」新的下軌跌破事件才解鎖。
        if (use_bollinger_reset and bollinger_reentry_locked
                and bollinger_reentry_lock_start_i is not None
                and i > bollinger_reentry_lock_start_i
                and bool(row.get("bollinger_reentry_reset_long", False))):
            bollinger_reentry_locked = False
            bollinger_reentry_lock_start_i = None

        # ---- 1) 執行前一根收盤的進場訊號：本根開盤進場 ----
        if pos is None and pending is not None:
            d = 1 if pending["direction"] == "long" else -1
            entry_p = _entry_group_params(
                p, pending["direction"], pending.get("entry_group_index"))
            entry_price = row["open"] + d * cost.slippage_points  # 不利方向滑價
            entry_atr = pending.get("signal_atr")
            planned_stop_points = (_stop_distance_points(entry_p, entry_atr, entry_price)
                                   if getattr(entry_p, "use_fixed_stop", False) else None)
            # v0.8.6.7：實際停損與部位估算距離可分離。
            # 只有明確設定 reference ATR 倍數時才覆寫部位估算距離；
            # 因此舊策略與一般固定停損策略完全維持原行為。
            sizing_reference_multiple = _positive_float(
                getattr(entry_p, "position_sizing_reference_atr_multiple", None))
            position_sizing_reference_points = planned_stop_points
            if sizing_reference_multiple is not None:
                atr_value = _positive_float(entry_atr)
                position_sizing_reference_points = (
                    atr_value * sizing_reference_multiple if atr_value is not None else None)
            skip_entry = False
            needs_atr_reference = (
                (getattr(entry_p, "use_fixed_stop", False)
                 and _stop_threshold_mode(entry_p) == "entry_atr")
                or sizing_reference_multiple is not None
            )
            if needs_atr_reference and position_sizing_reference_points is None:
                # ATR 尚未形成時，不可用未完成資料猜測停損／部位風險距離。
                missing_atr_skipped_entries += 1
                skip_entry = True

            if getattr(entry_p, "use_safe_capital_position_sizing", False) or str(getattr(entry_p, "position_sizing_mode", "fixed")).lower() in {"dynamic_safe_capital", "dynamic_safe_capital_capped"}:
                position_spec = None if skip_entry else _safe_capital_position_spec(
                    position_sizing_reference_points, entry_p, realized, cost)
                if not skip_entry and position_spec is None:
                    dynamic_size_skipped_entries += 1
                    skip_entry = True
            elif getattr(entry_p, "use_regime_position_sizing", False):
                target_units = pending.get("target_micro_units")
                position_spec = None if skip_entry else _regime_position_spec(
                    target_units, position_sizing_reference_points, entry_p, realized, cost)
                if not skip_entry and position_spec is None:
                    dynamic_size_skipped_entries += 1
                    skip_entry = True
            elif getattr(entry_p, "use_dynamic_position_sizing", False):
                position_spec = None if skip_entry else _dynamic_position_spec(
                    position_sizing_reference_points, entry_p, realized, cost, realized_peak_equity)
                if not skip_entry and position_spec is None:
                    dynamic_size_skipped_entries += 1
                    skip_entry = True
            else:
                position_spec = _fixed_position_spec(cost, entry_p)

            planned_stop_risk_amount = None
            position_sizing_reference_risk_amount = None
            if position_spec is not None:
                planned_stop_risk_amount = _planned_stop_risk_amount(
                    planned_stop_points, cost, position_spec.get("point_value_total"))
                position_sizing_reference_risk_amount = _planned_stop_risk_amount(
                    position_sizing_reference_points, cost, position_spec.get("point_value_total"))
                position_spec.setdefault("planned_stop_risk_amount", planned_stop_risk_amount)
                position_spec.setdefault(
                    "position_sizing_reference_risk_amount",
                    position_sizing_reference_risk_amount)

            cap = _positive_float(getattr(entry_p, "max_entry_risk_amount", None))
            if (not skip_entry and getattr(entry_p, "use_entry_risk_cap", False)
                    and _stop_threshold_mode(entry_p) in {"entry_atr", "entry_pct", "points"}
                    and cap is not None and planned_stop_risk_amount is not None
                    and planned_stop_risk_amount > cap):
                risk_cap_skipped_entries += 1
                skip_entry = True

            if not skip_entry:
                current_units = int((position_spec or {}).get("micro_units", 0))
                if last_entry_units <= 0:
                    position_action = "initial"
                elif current_units > last_entry_units:
                    position_action = "increase"
                elif current_units < last_entry_units:
                    position_action = "decrease"
                else:
                    position_action = "maintain"
                pos = {
                    "direction": d,
                    "entry_price": entry_price,
                    "entry_i": i,
                    "entry_date": dt,                 # 相容舊欄位：實際進場日
                    "entry_execution_date": dt,       # v0.3 明確標示執行日
                    "signal_i": pending["signal_i"],
                    "signal_date": pending["signal_date"],
                    "entry_reason": pending["entry_reason"],
                    "entry_group_index": pending.get("entry_group_index"),
                    "position_regime": pending.get("position_regime", "fixed"),
                    "requested_micro_units": pending.get("target_micro_units"),
                    # 進場發生在本根開盤，不能使用本根尚未完成的 ATR；固定保存訊號根 ATR。
                    "entry_atr": entry_atr,
                    "r_reference_ma": pending.get("r_reference_ma"),
                    "initial_r_points": max(
                        abs(float(entry_price) - float(pending.get("r_reference_ma")))
                        if _positive_float(pending.get("r_reference_ma")) is not None else 0.0,
                        (float(entry_atr) * max(float(getattr(entry_p, "initial_r_atr_floor_multiple", 0.5) or 0.0), 0.0))
                        if _positive_float(entry_atr) is not None else 0.0,
                    ) or None,
                    "partial_r_done": False,
                    "planned_stop_points": planned_stop_points,
                    "planned_stop_risk_amount": planned_stop_risk_amount,
                    "position_sizing_reference_points": position_sizing_reference_points,
                    "position_sizing_reference_risk_amount": position_sizing_reference_risk_amount,
                    "entry_risk_cap_amount": cap
                    if (getattr(entry_p, "use_entry_risk_cap", False)
                        and _stop_threshold_mode(entry_p) in {"entry_atr", "entry_pct", "points"}) else None,
                    "position_action": position_action,
                    "previous_entry_micro_units": int(last_entry_units),
                    **(position_spec or {}),
                    "effective_leverage": ((float(entry_price) * float((position_spec or {}).get("point_value_total", cost.point_value))) /
                                           max(float((position_spec or {}).get("available_equity_at_entry", getattr(entry_p, "position_sizing_capital", 0.0) or 0.0)), 1.0)),
                    "highest": row["high"],  # 供移動停損用（本根結束後才生效）
                    "lowest": row["low"],
                    "max_adverse_points": _adverse_points(entry_price, d, row),
                    "max_favorable_points": _favorable_points(entry_price, d, row),
                    "max_favorable_amount": _favorable_points(entry_price, d, row) * float((position_spec or {}).get("point_value_total", cost.point_value * cost.quantity)),
                }
                last_entry_units = int(pos.get("micro_units", 0))
            pending = None

        # ---- 2) 出場判斷 ----
        exit_price, exit_reason = None, None
        if pos is not None:
            d, ep = pos["direction"], pos["entry_price"]
            active_p = _entry_group_params(
                p, "long" if d == 1 else "short", pos.get("entry_group_index"))
            pos["max_adverse_points"] = max(
                float(pos.get("max_adverse_points", 0.0)),
                _adverse_points(ep, d, row),
            )
            pos["max_favorable_points"] = max(
                float(pos.get("max_favorable_points", 0.0)),
                _favorable_points(ep, d, row),
            )
            pos["max_favorable_amount"] = float(pos.get("max_favorable_points", 0.0)) * _position_point_value(pos, cost)
            margin_line = _margin_call_line(ep, d, cost, pos, realized, active_p)

            # a0) 斷頭開盤跳空：開盤已吃光安全緩衝金額，優先視同斷頭強制平倉
            if margin_line is not None:
                if d == 1 and row["open"] <= margin_line:
                    exit_price = row["open"]
                    exit_reason = "margin_call"
                elif d == -1 and row["open"] >= margin_line:
                    exit_price = row["open"]
                    exit_reason = "margin_call"

            # a00) v0.8.6.7 無效交易退出：前一根收盤確認，當根開盤執行。
            # 斷頭開盤跳空仍保有更高優先級；其餘停損與收盤出場不再重複判斷。
            pending_time_exit_i = pos.get("pending_time_invalid_exit_i")
            if (exit_price is None and pending_time_exit_i is not None
                    and i > int(pending_time_exit_i)):
                exit_price = row["open"]
                exit_reason = "time_invalid_exit"

            pending_max_exit_i = pos.get("pending_max_holding_exit_i")
            if (exit_price is None and pending_max_exit_i is not None
                    and i > int(pending_max_exit_i)):
                exit_price = row["open"]
                exit_reason = "max_holding_exit"

            pending_mfe_rebound_i = pos.get("pending_mfe_rebound_exit_i")
            if (exit_price is None and pending_mfe_rebound_i is not None
                    and i > int(pending_mfe_rebound_i)):
                exit_price = row["open"]
                exit_reason = "mfe_rebound_exit"

            # a) 停損（固定點數或進場 ATR 倍數；盤中觸價）
            if exit_price is None and active_p.use_fixed_stop:
                stop_distance = _positive_float(pos.get("planned_stop_points"))
                if stop_distance is None:
                    stop_distance = _stop_distance_points(active_p, pos.get("entry_atr"), ep)
                stop = ep - d * float(stop_distance or 0.0)
                if d == 1 and row["low"] <= stop:
                    exit_price = min(row["open"], stop)
                    exit_reason = "fixed_stop"
                elif d == -1 and row["high"] >= stop:
                    exit_price = max(row["open"], stop)
                    exit_reason = "fixed_stop"

            # a2) v0.8.7.0 R倍數部分出場（盤中觸價，只執行一次）。
            if (exit_price is None and bool(getattr(active_p, "use_partial_r_exit", False))
                    and not bool(pos.get("partial_r_done", False))):
                r_points = _positive_float(pos.get("initial_r_points"))
                r_mult = _positive_float(getattr(active_p, "partial_r_multiple", 3.0))
                units_now = int(pos.get("micro_units", 0) or 0)
                fraction = min(max(float(getattr(active_p, "partial_exit_fraction", 0.5) or 0.5), 0.0), 1.0)
                if r_points is not None and r_mult is not None and units_now >= 2 and fraction > 0:
                    target = ep + d * r_points * r_mult
                    hit = (d == 1 and row["high"] >= target) or (d == -1 and row["low"] <= target)
                    if hit:
                        partial_px = max(row["open"], target) if d == 1 else min(row["open"], target)
                        exit_units = min(max(int(round(units_now * fraction)), 1), units_now - 1)
                        partial_spec = _contract_mix_from_micro_units(exit_units, active_p, cost)
                        partial_exec_px = partial_px - d * cost.slippage_points
                        partial_pts = (partial_exec_px - ep) * d
                        partial_pv = float(partial_spec.get("point_value_total", 0.0))
                        partial_fee = float(partial_spec.get("fee_per_side_total", 0.0))
                        partial_tax = (ep + partial_exec_px) * partial_pv * cost.tax_rate
                        partial_amount = partial_pts * partial_pv - 2 * partial_fee - partial_tax
                        trades.append({
                            "signal_date": pos["signal_date"], "signal_bar_index": pos["signal_i"],
                            "entry_date": pos["entry_date"], "entry_execution_date": pos["entry_execution_date"],
                            "entry_bar_index": pos["entry_i"], "exit_date": dt, "exit_bar_index": i,
                            "direction": "long" if d == 1 else "short",
                            "entry_price": round(ep, 2), "exit_price": round(partial_exec_px, 2),
                            "quantity": round(float(partial_spec.get("small_equivalent_quantity", 0.0)), 2),
                            "large_quantity": int(partial_spec.get("large_qty", 0)),
                            "small_quantity": int(partial_spec.get("small_qty", 0)),
                            "micro_quantity": int(partial_spec.get("micro_qty", 0)),
                            "position_micro_units": exit_units,
                            "point_value_total": round(partial_pv, 1),
                            "position_margin_amount": round(float(partial_spec.get("margin_amount", 0.0)), 1),
                            "maintenance_margin_amount": round(float(partial_spec.get("maintenance_margin_amount", 0.0)), 1),
                            "position_sizing_mode": str(pos.get("position_sizing_mode", "fixed")),
                            "pnl_points": round(partial_pts, 2), "pnl_amount": round(partial_amount, 1),
                            "holding_bars": i - pos["entry_i"] + 1, "exit_reason": "partial_r_exit",
                            "entry_reason": pos.get("entry_reason"), "entry_group_index": pos.get("entry_group_index"),
                            "entry_atr": pos.get("entry_atr"), "initial_r_points": r_points,
                            "partial_exit_fraction": exit_units / units_now,
                        })
                        realized += partial_amount
                        realized_peak_equity = max(realized_peak_equity, initial_sizing_capital + realized)
                        remain_spec = _contract_mix_from_micro_units(units_now - exit_units, active_p, cost)
                        for key in ("micro_units", "large_qty", "small_qty", "micro_qty",
                                    "small_equivalent_quantity", "point_value_total", "fee_per_side_total",
                                    "margin_amount", "maintenance_margin_amount"):
                            pos[key] = remain_spec.get(key)
                        pos["partial_r_done"] = True
                        pos["partial_r_exit_units"] = exit_units
                        pos["partial_r_exit_price"] = partial_exec_px

            # b) 固定停利（盤中觸價）
            if exit_price is None and active_p.use_take_profit:
                tp = ep + d * active_p.take_profit_points
                if d == 1 and row["high"] >= tp:
                    exit_price = max(row["open"], tp)
                    exit_reason = "take_profit"
                elif d == -1 and row["low"] <= tp:
                    exit_price = min(row["open"], tp)
                    exit_reason = "take_profit"

            # c) 移動停損（用進場後、前一根以前的極值追蹤；進場當根不觸發）
            if exit_price is None and active_p.use_trailing_stop and i > pos["entry_i"]:
                if d == 1:
                    trail = pos["highest"] - active_p.trailing_points
                    if row["low"] <= trail:
                        exit_price = min(row["open"], trail)
                        exit_reason = "trailing_stop"
                else:
                    trail = pos["lowest"] + active_p.trailing_points
                    if row["high"] >= trail:
                        exit_price = max(row["open"], trail)
                        exit_reason = "trailing_stop"

            # c1) Parabolic SAR 自適應移動停損（盤中觸價）。
            # sar_stop_long / short 是由前一根以前的狀態推算出的當根停損線，
            # 不使用當根完成後才知道的轉向 SAR 顯示值。
            if exit_price is None and getattr(active_p, "use_sar_exit", False):
                sar_col = "sar_stop_long" if d == 1 else "sar_stop_short"
                sar_stop = row.get(sar_col)
                if pd.notna(sar_stop):
                    sar_stop = float(sar_stop)
                    if d == 1 and row["low"] <= sar_stop:
                        exit_price = min(row["open"], sar_stop)
                        exit_reason = "sar_stop"
                    elif d == -1 and row["high"] >= sar_stop:
                        exit_price = max(row["open"], sar_stop)
                        exit_reason = "sar_stop"

            # c2) 斷頭盤中觸價：安全緩衝金額被吃光，視同斷頭強制平倉
            if exit_price is None and margin_line is not None:
                if d == 1 and row["low"] <= margin_line:
                    exit_price = margin_line
                    exit_reason = "margin_call"
                elif d == -1 and row["high"] >= margin_line:
                    exit_price = margin_line
                    exit_reason = "margin_call"

            holding_now = i - pos["entry_i"] + 1
            min_hold = max(int(getattr(active_p, "minimum_holding_bars", 0) or 0), 0)
            discretionary_exit_allowed = holding_now > min_hold

            # d) 吊燈出場（收盤確認）
            # v0.5.0：支援「獲利分段吊燈」：依目前浮盈金額選擇不同吊燈倍數。
            if exit_price is None and (getattr(active_p, "use_chandelier", False) or getattr(active_p, "use_profit_tier_chandelier", False)):
                ch_label = "chandelier"
                if getattr(active_p, "use_profit_tier_chandelier", False):
                    tier_value = _tier_reference_value(pos, ep, d, row, cost, active_p)
                    mult = _select_profit_tier_mult(
                        tier_value,
                        _tier_thresholds(active_p),
                        getattr(active_p, "profit_tier_mults", ()),
                        getattr(active_p, "chandelier_mult", 3.0),
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
                    raw_ch_hit = bool(pd.notna(ch) and row["close"] < ch)
                else:
                    ch = row.get(short_col)
                    raw_ch_hit = bool(pd.notna(ch) and row["close"] > ch)
                ch_ok = _confirmed_exit(
                    pos, "chandelier", raw_ch_hit and discretionary_exit_allowed,
                    getattr(active_p, "chandelier_exit_confirmation_bars", 1))
                if exit_price is None and ch_ok:
                    exit_price, exit_reason = row["close"], ch_label

            # e) MACD 反向（收盤確認）：多單 hist<0 出場、空單 hist>0 出場
            # v0.5.1：若已達指定最高浮盈，可排除 MACD 反向，避免獲利擴大後被短期反向訊號提前洗出。
            macd_reverse_blocked = False
            if exit_price is None and getattr(active_p, "use_profit_scaled_macd_exclusion", False):
                if _profit_tier_mode(p) == "entry_atr":
                    block_at = float(getattr(active_p, "macd_reverse_exclude_atr_multiple", 0.0) or 0.0)
                else:
                    block_at = float(getattr(active_p, "macd_reverse_exclude_profit_amount", 0.0) or 0.0)
                ref_value = _tier_reference_value(pos, ep, d, row, cost, active_p)
                if block_at > 0 and ref_value >= block_at:
                    macd_reverse_blocked = True

            raw_macd_hit = False
            if active_p.use_macd_reverse and (not macd_reverse_blocked) and "macd_hist" in df.columns:
                h = row["macd_hist"]
                raw_macd_hit = bool(pd.notna(h) and ((d == 1 and h < 0) or (d == -1 and h > 0)))
            macd_ok = _confirmed_exit(
                pos, "macd", raw_macd_hit and discretionary_exit_allowed,
                getattr(active_p, "macd_exit_confirmation_bars", 1))
            if exit_price is None and macd_ok:
                exit_price, exit_reason = row["close"], "macd_reverse"

            # e2) 條件出場（收盤確認；v0.3.4 新增、可選）
            #     只有 params.use_signal_exit=True 且策略層有產生
            #     exit_long_signal / exit_short_signal 欄位時才會啟用，
            #     否則完全不影響既有出場行為（self_check 8 cases 不變）。
            raw_signal_exit = False
            raw_opposite_exit = False
            signal_reason = "signal_exit"
            if getattr(active_p, "use_signal_exit", False):
                direction_name = "long" if d == 1 else "short"
                opposite_col = f"exit_{direction_name}_opposite_signal"
                opposite_reason_col = f"exit_{direction_name}_opposite_reason"
                raw_opposite_exit = bool(
                    opposite_col in df.columns and row.get(opposite_col, False))
                if raw_opposite_exit:
                    reason_value = row.get(opposite_reason_col, "")
                    signal_reason = (str(reason_value) if pd.notna(reason_value)
                                     and str(reason_value).strip() else "opposite_signal_exit")
                group_idx = pos.get("entry_group_index")
                group_col = None
                if group_idx is not None:
                    group_col = f"exit_{direction_name}_group_{int(group_idx)}_signal"
                if group_col and group_col in df.columns:
                    raw_group_exit = bool(row.get(group_col, False))
                    raw_signal_exit = raw_opposite_exit or raw_group_exit
                    if raw_group_exit and not raw_opposite_exit:
                        signal_reason = f"signal_exit_group_{int(group_idx)}"
                else:
                    sig_col = f"exit_{direction_name}_signal"
                    raw_signal_exit = bool(sig_col in df.columns and row.get(sig_col, False))
            bypass_min_hold = bool(
                raw_opposite_exit
                and getattr(active_p, "opposite_signal_exit_bypass_minimum_holding", False))
            signal_allowed = raw_signal_exit and (discretionary_exit_allowed or bypass_min_hold)
            signal_ok = _confirmed_exit(
                pos, "signal", signal_allowed,
                getattr(active_p, "signal_exit_confirmation_bars", 1))
            if exit_price is None and signal_ok:
                exit_price, exit_reason = row["close"], signal_reason

            # e3) v0.8.6.7 五根K無效交易退出。
            # 只在指定持有根數的收盤檢查一次；若成立，下一根開盤退出。
            if exit_price is None and bool(getattr(active_p, "use_time_invalid_exit", False)):
                invalid_bars = max(int(getattr(active_p, "time_invalid_exit_bars", 5) or 5), 1)
                if holding_now == invalid_bars and pos.get("pending_time_invalid_exit_i") is None:
                    entry_atr_value = _positive_float(pos.get("entry_atr"))
                    mfe_limit = max(float(getattr(
                        active_p, "time_invalid_max_favorable_atr_multiple", 0.5) or 0.0), 0.0)
                    mfe_multiple = None
                    if entry_atr_value is not None:
                        mfe_multiple = float(pos.get("max_favorable_points", 0.0)) / entry_atr_value
                    losing_close = ((d == 1 and float(row["close"]) < ep)
                                    or (d == -1 and float(row["close"]) > ep))
                    require_losing = bool(getattr(
                        active_p, "time_invalid_require_losing_close", True))
                    if (mfe_multiple is not None and mfe_multiple < mfe_limit
                            and ((not require_losing) or losing_close) and i < n - 1):
                        pos["pending_time_invalid_exit_i"] = i
                        pos["time_invalid_mfe_atr_multiple"] = mfe_multiple


            # v0.8.7.0 最大持有根數：第N根收盤確認，下一根開盤退出。
            if exit_price is None and bool(getattr(active_p, "use_max_holding_exit", False)):
                max_bars = max(int(getattr(active_p, "max_holding_bars", 60) or 60), 1)
                if holding_now >= max_bars and pos.get("pending_max_holding_exit_i") is None and i < n - 1:
                    pos["pending_max_holding_exit_i"] = i

            # v0.8.7.4 獲利成熟／無門檻快速反彈退出。
            # activation>0 時先確認最大順向浮盈達進場ATR倍數；activation=0 時不設獲利門檻。
            # 再以單日收盤反向漲跌幅觸發，收盤確認後於下一根開盤執行。
            if exit_price is None and bool(getattr(active_p, "use_mfe_rebound_exit", False)):
                entry_atr_value = _positive_float(pos.get("entry_atr"))
                activation = max(float(getattr(
                    active_p, "mfe_rebound_activation_atr_multiple", 4.0) or 0.0), 0.0)
                rebound_pct = max(float(getattr(
                    active_p, "mfe_rebound_close_return_pct", 3.0) or 0.0), 0.0)
                mfe_multiple = None
                close_return_pct = None
                if entry_atr_value is not None:
                    mfe_multiple = float(pos.get("max_favorable_points", 0.0)) / entry_atr_value
                if i > 0:
                    prev_close = _positive_float(df.iloc[i - 1].get("close"))
                    if prev_close is not None:
                        close_return_pct = (float(row["close"]) / prev_close - 1.0) * 100.0
                rebound_hit = bool(
                    close_return_pct is not None and rebound_pct > 0
                    and ((d == -1 and close_return_pct >= rebound_pct)
                         or (d == 1 and close_return_pct <= -rebound_pct))
                )
                activation_reached = bool(
                    activation <= 0
                    or (mfe_multiple is not None and mfe_multiple >= activation)
                )
                if (activation_reached and rebound_hit
                        and pos.get("pending_mfe_rebound_exit_i") is None and i < n - 1):
                    pos["pending_mfe_rebound_exit_i"] = i
                    pos["mfe_rebound_trigger_mfe_atr_multiple"] = mfe_multiple
                    pos["mfe_rebound_trigger_close_return_pct"] = close_return_pct

            # f) 資料結束強制平倉
            if exit_price is None and i == n - 1:
                exit_price, exit_reason = row["close"], "end_of_data"

        # ---- 3) 結算出場 ----
        if pos is not None and exit_price is not None:
            d = pos["direction"]
            px = exit_price - d * cost.slippage_points  # 出場滑價（不利方向）
            pts = (px - pos["entry_price"]) * d
            point_value_total = _position_point_value(pos, cost)
            fee_per_side_total = _position_fee_per_side(pos, cost)
            tax = (pos["entry_price"] + px) * point_value_total * cost.tax_rate
            amount = pts * point_value_total - 2 * fee_per_side_total - tax
            max_adverse_points = float(pos.get("max_adverse_points", 0.0))
            max_adverse_amount = max_adverse_points * point_value_total
            max_favorable_points = float(pos.get("max_favorable_points", 0.0))
            max_favorable_amount = max_favorable_points * point_value_total
            entry_atr = _positive_float(pos.get("entry_atr"))
            max_favorable_atr_multiple = (max_favorable_points / entry_atr) if entry_atr else None
            required_safety_capital = float(pos.get("margin_amount", cost.original_margin_amount)) + max_adverse_amount
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
                "quantity": round(float(pos.get("small_equivalent_quantity", 1.0)), 2),
                "large_quantity": int(pos.get("large_qty", 0)),
                "small_quantity": int(pos.get("small_qty", 0)),
                "micro_quantity": int(pos.get("micro_qty", 0)),
                "position_micro_units": int(pos.get("micro_units", 0)),
                "point_value_total": round(point_value_total, 1),
                "position_margin_amount": round(float(pos.get("margin_amount", 0.0)), 1),
                "maintenance_margin_amount": round(float(pos.get("maintenance_margin_amount", 0.0)), 1),
                "position_sizing_mode": str(pos.get("position_sizing_mode", "fixed")),
                "position_compounding": bool(pos.get("position_compounding", False)),
                "position_equity_basis": str(pos.get("position_equity_basis", "fixed")),
                "position_regime": str(pos.get("position_regime", "fixed")),
                "requested_micro_units": int(pos.get("requested_micro_units") or pos.get("micro_units", 0)),
                "previous_entry_micro_units": int(pos.get("previous_entry_micro_units", 0)),
                "position_action": str(pos.get("position_action", "maintain")),
                "effective_leverage": round(float(pos.get("effective_leverage", 0.0)), 4),
                "margin_utilization_pct": round(float(pos.get("margin_utilization_pct", 0.0)), 4),
                "safe_capital_per_micro_unit": round(float(pos.get("safe_capital_per_micro_unit", 0.0)), 1) if pos.get("safe_capital_per_micro_unit") is not None else None,
                "safe_capital_used": round(float(pos.get("safe_capital_used", 0.0)), 1) if pos.get("safe_capital_used") is not None else None,
                "safe_capital_balance": round(float(pos.get("safe_capital_balance", 0.0)), 1) if pos.get("safe_capital_balance") is not None else None,
                "drawdown_reserve_amount": round(float(pos.get("drawdown_reserve_amount", 0.0)), 1) if pos.get("drawdown_reserve_amount") is not None else None,
                "gap_stress_points": round(float(pos.get("gap_stress_points", 0.0)), 2) if pos.get("gap_stress_points") is not None else None,
                "pnl_points": round(pts, 2),
                "pnl_amount": round(amount, 1),
                "holding_bars": i - pos["entry_i"] + 1,
                "exit_reason": exit_reason,
                "entry_reason": pos["entry_reason"],
                "entry_group_index": int(pos.get("entry_group_index"))
                    if pos.get("entry_group_index") is not None else None,
                "max_adverse_points": round(max_adverse_points, 2),
                "max_adverse_amount": round(max_adverse_amount, 1),
                "max_favorable_points": round(max_favorable_points, 2),
                "max_favorable_amount": round(max_favorable_amount, 1),
                "entry_atr": round(entry_atr, 4) if entry_atr else None,
                "initial_r_points": round(float(pos.get("initial_r_points")), 4)
                    if _positive_float(pos.get("initial_r_points")) else None,
                "partial_exit_fraction": None,
                "planned_stop_points": round(float(pos.get("planned_stop_points")), 4)
                    if _positive_float(pos.get("planned_stop_points")) else None,
                "planned_stop_risk_amount": round(float(pos.get("planned_stop_risk_amount")), 1)
                    if _positive_float(pos.get("planned_stop_risk_amount")) else None,
                "position_sizing_reference_points": round(
                    float(pos.get("position_sizing_reference_points")), 4)
                    if _positive_float(pos.get("position_sizing_reference_points")) else None,
                "position_sizing_reference_risk_amount": round(
                    float(pos.get("position_sizing_reference_risk_amount")), 1)
                    if _positive_float(pos.get("position_sizing_reference_risk_amount")) else None,
                "time_invalid_mfe_atr_multiple": round(
                    float(pos.get("time_invalid_mfe_atr_multiple")), 4)
                    if pos.get("time_invalid_mfe_atr_multiple") is not None else None,
                "entry_risk_cap_amount": round(float(pos.get("entry_risk_cap_amount")), 1)
                    if _positive_float(pos.get("entry_risk_cap_amount")) else None,
                "risk_budget_amount": round(float(pos.get("risk_budget_amount")), 1)
                    if _positive_float(pos.get("risk_budget_amount")) else None,
                "stress_risk_amount": round(float(pos.get("stress_risk_amount")), 1)
                    if _positive_float(pos.get("stress_risk_amount")) else None,
                "stress_multiple": round(float(pos.get("stress_multiple")), 2)
                    if _positive_float(pos.get("stress_multiple")) else None,
                "available_equity_at_entry": round(float(pos.get("available_equity_at_entry")), 1)
                    if _positive_float(pos.get("available_equity_at_entry")) else None,
                "base_risk_fraction": round(float(pos.get("base_risk_fraction")), 6)
                    if _positive_float(pos.get("base_risk_fraction")) else None,
                "effective_risk_fraction": round(float(pos.get("effective_risk_fraction")), 6)
                    if _positive_float(pos.get("effective_risk_fraction")) else None,
                "drawdown_brake_multiplier": round(float(pos.get("drawdown_brake_multiplier")), 4)
                    if pos.get("drawdown_brake_multiplier") is not None else None,
                "realized_equity_drawdown_pct": round(float(pos.get("realized_equity_drawdown_pct")), 4)
                    if pos.get("realized_equity_drawdown_pct") is not None else None,
                "realized_equity_peak": round(float(pos.get("realized_equity_peak")), 1)
                    if pos.get("realized_equity_peak") is not None else None,
                "max_favorable_atr_multiple": round(max_favorable_atr_multiple, 4)
                    if max_favorable_atr_multiple is not None else None,
                "mfe_rebound_trigger_mfe_atr_multiple": round(
                    float(pos.get("mfe_rebound_trigger_mfe_atr_multiple")), 4)
                    if pos.get("mfe_rebound_trigger_mfe_atr_multiple") is not None else None,
                "mfe_rebound_trigger_close_return_pct": round(
                    float(pos.get("mfe_rebound_trigger_close_return_pct")), 4)
                    if pos.get("mfe_rebound_trigger_close_return_pct") is not None else None,
                "required_safety_capital": round(required_safety_capital, 1),
            })
            realized += amount
            realized_peak_equity = max(realized_peak_equity, initial_sizing_capital + realized)
            if (use_bollinger_reset and exit_reason == "fixed_stop"
                    and pos.get("direction") == 1
                    and pos.get("entry_group_index") in reset_groups):
                bollinger_reentry_locked = True
                bollinger_reentry_lock_start_i = i
                bollinger_reentry_lock_activations += 1
            if exit_reason == "margin_call" and bool(getattr(active_p, "stop_trading_after_margin_call", True)):
                account_disabled = True
                pending = None
            pos = None

        # ---- 4) 收盤訊號 -> 下一根開盤進場（空手且帳戶未停用才接單）----
        if not account_disabled and pos is None and pending is None and i < n - 1:
            long_accepted = False
            if bool(row["long_entry"]):
                triggered_groups = _triggered_entry_groups(row, "long")
                allowed_groups = triggered_groups
                if use_bollinger_reset and bollinger_reentry_locked:
                    allowed_groups = [g for g in triggered_groups if g not in reset_groups]
                    if triggered_groups and not allowed_groups:
                        bollinger_reentry_blocked_signals += 1
                # 舊策略沒有群組欄時，維持原行為；新策略則取第一個未鎖定組合。
                if not triggered_groups or allowed_groups:
                    chosen_group = allowed_groups[0] if allowed_groups else None
                    pending_p = _entry_group_params(p, "long", chosen_group)
                    pending = {
                        "direction": "long",
                        "signal_i": i,
                        "signal_date": dt,
                        "entry_reason": _entry_reason_for_group(row, "long", chosen_group),
                        "entry_group_index": chosen_group,
                        "signal_atr": _positive_float(row.get("long_entry_atr", row.get("atr"))),
                        "r_reference_ma": _positive_float(row.get("long_r_reference_ma")),
                        "target_micro_units": int(row.get("long_position_micro_units", 0) or 0)
                            if getattr(pending_p, "use_regime_position_sizing", False) else None,
                        "position_regime": str(row.get("long_position_regime", "fixed")),
                    }
                    long_accepted = True
            if not long_accepted and bool(row["short_entry"]):
                short_groups = _triggered_entry_groups(row, "short")
                chosen_group = short_groups[0] if short_groups else None
                pending_p = _entry_group_params(p, "short", chosen_group)
                pending = {
                    "direction": "short",
                    "signal_i": i,
                    "signal_date": dt,
                    "entry_reason": _entry_reason_for_group(row, "short", chosen_group),
                    "entry_group_index": chosen_group,
                    "signal_atr": _positive_float(row.get("short_entry_atr", row.get("atr"))),
                    "r_reference_ma": _positive_float(row.get("short_r_reference_ma")),
                    "target_micro_units": int(row.get("short_position_micro_units", 0) or 0)
                        if getattr(pending_p, "use_regime_position_sizing", False) else None,
                    "position_regime": str(row.get("short_position_regime", "fixed")),
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
            pos["max_favorable_amount"] = float(pos.get("max_favorable_points", 0.0)) * _position_point_value(pos, cost)

        # ---- 6) 權益曲線（已實現 + 未實現以收盤價估）----
        unreal = 0.0
        if pos is not None:
            unreal = ((row["close"] - pos["entry_price"]) * pos["direction"]
                      * _position_point_value(pos, cost))
        # v0.8.6.2：逐日記錄「下一筆新進場」會採用的回撤煞車狀態。
        # 這裡只依已實現權益計算，與實際部位計算規則一致；不會把未實現損益
        # 誤當成煞車依據，也不會在持倉途中變更既有口數。
        daily_brake_multiplier, daily_realized_dd_pct, daily_realized_peak = (
            _drawdown_risk_brake_multiplier(p, realized, realized_peak_equity)
        )
        equity_rows.append({
            "datetime": dt,
            "equity": realized + unreal,
            "account_equity": float(getattr(p, "position_sizing_capital", 0.0) or 0.0) + realized + unreal,
            "position_margin_amount": float(pos.get("margin_amount", 0.0)) if pos is not None else 0.0,
            "maintenance_margin_amount": float(pos.get("maintenance_margin_amount", 0.0)) if pos is not None else 0.0,
            "open_position_micro_units": int(pos.get("micro_units", 0)) if pos is not None else 0,
            "risk_cap_skipped_entries": risk_cap_skipped_entries,
            "missing_atr_skipped_entries": missing_atr_skipped_entries,
            "dynamic_size_skipped_entries": dynamic_size_skipped_entries,
            "account_disabled": bool(account_disabled),
            "bollinger_reentry_locked": bool(bollinger_reentry_locked),
            "bollinger_reentry_lock_activations": int(bollinger_reentry_lock_activations),
            "bollinger_reentry_blocked_signals": int(bollinger_reentry_blocked_signals),
            "daily_drawdown_brake_multiplier": round(float(daily_brake_multiplier), 6),
            "daily_drawdown_brake_active": bool(daily_brake_multiplier < 0.999999),
            "daily_realized_equity_drawdown_pct": round(float(daily_realized_dd_pct), 6),
            "daily_realized_equity_peak": round(float(daily_realized_peak), 2),
        })

    trades_df = pd.DataFrame(trades, columns=[
        "signal_date", "signal_bar_index",
        "entry_date", "entry_execution_date", "entry_bar_index",
        "exit_date", "exit_bar_index", "direction",
        "entry_price", "exit_price", "quantity",
        "large_quantity", "small_quantity", "micro_quantity", "position_micro_units",
        "point_value_total", "position_margin_amount", "maintenance_margin_amount",
        "position_sizing_mode", "position_compounding", "position_equity_basis",
        "position_regime", "requested_micro_units",
        "previous_entry_micro_units", "position_action", "effective_leverage",
        "margin_utilization_pct", "safe_capital_per_micro_unit", "safe_capital_used",
        "safe_capital_balance", "drawdown_reserve_amount", "gap_stress_points",
        "pnl_points", "pnl_amount", "holding_bars",
        "exit_reason", "entry_reason", "entry_group_index",
        "max_adverse_points", "max_adverse_amount",
        "max_favorable_points", "max_favorable_amount",
        "entry_atr", "initial_r_points", "partial_exit_fraction", "planned_stop_points", "planned_stop_risk_amount",
        "position_sizing_reference_points", "position_sizing_reference_risk_amount",
        "time_invalid_mfe_atr_multiple",
        "entry_risk_cap_amount", "risk_budget_amount", "base_risk_fraction",
        "effective_risk_fraction", "drawdown_brake_multiplier",
        "realized_equity_drawdown_pct", "realized_equity_peak", "stress_risk_amount",
        "stress_multiple", "available_equity_at_entry",
        "max_favorable_atr_multiple",
        "mfe_rebound_trigger_mfe_atr_multiple",
        "mfe_rebound_trigger_close_return_pct",
        "required_safety_capital"])
    equity_df = pd.DataFrame(equity_rows)
    return trades_df, equity_df
