# -*- coding: utf-8 -*-
"""
strategies.py - 策略層：策略 JSON 結構 + 條件組合 + 訊號產生。

v2 起策略不再寫死：進場條件由 condition_blocks.py 的條件組合而成，
策略本身是一份 JSON（dict），結構如下：

{
  "name": "MACD_BB_Chandelier_v1",
  "symbol": "MTX",
  "timeframe": "1D",
  "direction": "both",                     # long / short / both
  "entry_long":  {"logic": "AND", "conditions": [ {"type": ...}, ... ]},
  "entry_short": {"logic": "AND", "conditions": [ ... ]},
  "exit": {
    "use_chandelier": true, "chandelier_period": 22, "chandelier_mult": 3.0,
    "use_macd_reverse": true,
    "use_fixed_stop": true, "stop_points": 100,
    "use_take_profit": false, "take_profit_points": 200,
    "use_trailing_stop": false, "trailing_points": 150
  }
}

流程：run_strategy_config(df, config) -> 帶 long_entry/short_entry 的 DataFrame
      -> 丟給 backtester.run_backtest()（backtester 不需要知道條件內容）

新增條件：只改 condition_blocks.py。
新增策略：組一份新的 JSON 即可，不需要重寫 backtester.py。
"""
import json
from dataclasses import asdict, dataclass, field, fields

import pandas as pd

import indicators as ind
from condition_blocks import (evaluate_block, evaluate_block_with_reasons,
                              evaluate_condition)
from config import DEFAULT_MA_PERIODS


# =====================================================================
# StrategyParams：出場模組 + 顯示用指標參數（UI 與 backtester 共用）
# =====================================================================
@dataclass
class StrategyParams:
    # MACD（示範策略進場 + MACD反向出場用）
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    # Bollinger
    bb_period: int = 20
    bb_std: float = 2.0
    # 趨勢過濾均線（可開關）
    ma_filter_enabled: bool = True
    ma_filter_period: int = 20
    ma_filter_type: str = "SMA"     # SMA / EMA / WMA
    # 交易方向: long / short / both
    direction: str = "both"
    # ---- 出場條件開關（backtester 執行）----
    use_chandelier: bool = True
    chandelier_period: int = 22
    chandelier_mult: float = 3.0
    # v0.5.0：獲利分段吊燈（獲利越高，吊燈倍數可放寬）
    use_profit_tier_chandelier: bool = False
    profit_tier_chandelier_period: int = 22
    # 舊版固定金額門檻（元），保留向下相容。
    profit_tier_amounts: tuple = ()
    # v0.6.5：ATR 標準化門檻。例：[2, 4, 8] 代表最大順向浮盈 / 進場可用 ATR。
    profit_tier_atr_multiples: tuple = ()
    # amount=固定金額（舊版）；entry_atr=相對於進場可用 ATR（建議）。
    profit_tier_threshold_mode: str = "amount"
    # 各段吊燈倍數。數量需為門檻數量 + 1，例如 [2.5, 3.0, 3.5, 5.0]
    profit_tier_mults: tuple = ()
    # current_unrealized=用當根收盤浮盈；max_favorable=用持倉以來最高順向浮盈。
    profit_tier_reference: str = "current_unrealized"
    # 獲利放大後，可排除 MACD 反向出場，改讓較寬的吊燈線決定出場。
    use_profit_scaled_macd_exclusion: bool = False
    macd_reverse_exclude_profit_amount: float = 0.0
    # v0.6.5：ATR 模式下，達此 ATR 倍數後排除 MACD 反向。
    macd_reverse_exclude_atr_multiple: float = 0.0
    use_macd_reverse: bool = True
    use_fixed_stop: bool = True
    # v0.6.6：停損距離可使用固定點數或「進場前已完成 K 棒 ATR × 倍數」。
    stop_threshold_mode: str = "points"  # points / entry_atr
    stop_points: float = 100.0
    stop_atr_multiple: float = 0.75
    # ATR 停損換算的正常價格風險若超過上限，直接略過該次進場。
    # 僅限制預定停損距離，不保證跳空時的實際損失不超過上限。
    use_entry_risk_cap: bool = False
    max_entry_risk_amount: float = 20000.0
    # v0.6.7：依帳戶資金與單筆風險率動態決定部位。
    # 以「微台等值單位」計算：1 單位=1口微台；5單位=1口小台；10單位=2口小台。
    use_dynamic_position_sizing: bool = False
    use_account_margin_model: bool = False
    position_sizing_capital: float = 500000.0
    position_risk_fraction: float = 0.04
    position_stress_multiple: float = 4.0
    position_max_micro_units: int = 10
    position_use_stress_capital_check: bool = True
    position_micro_point_value: float = 10.0
    position_small_point_value: float = 50.0
    position_large_point_value: float = 200.0
    position_micro_margin: float = 32000.0
    position_small_margin: float = 159000.0
    position_large_margin: float = 636000.0
    position_micro_maintenance_margin: float = 24400.0
    position_small_maintenance_margin: float = 122000.0
    position_large_maintenance_margin: float = 488000.0
    position_micro_fee: float = 12.0
    position_small_fee: float = 20.0
    position_large_fee: float = 50.0
    # v0.8.2：執行商品自動換算。min_contract_count 會以大台→小台→微台
    # 由大到小組合相同曝險，盡量減少總口數；可用 max_contract_point_value
    # 限制高風險進場只能使用小台或微台。
    position_contract_mix_mode: str = "small_micro_only"
    position_max_contract_point_value: float = 200.0
    # v0.8.0：真正依帳戶權益複利的「安全資金單位」部位。
    # 每累積一個安全資金單位才增加一個微台等值單位，獲利可增倉、回撤會減倉。
    position_sizing_mode: str = "fixed"  # fixed / dynamic_risk / dynamic_safe_capital / dynamic_safe_capital_capped
    # 預設 False 保留 v0.7 舊口徑：獲利不放大部位、虧損仍會減倉。
    # 設為 True 才使用「初始資金＋已實現損益」複利增減口數。
    position_compounding: bool = False
    use_safe_capital_position_sizing: bool = False
    position_safe_capital_per_micro_unit: float = 100000.0
    position_min_cash_buffer: float = 0.0
    position_drawdown_reserve_fraction: float = 0.10
    position_gap_stress_points: float = 500.0
    position_max_small_contracts: int = 10
    stop_trading_after_margin_call: bool = True
    # v0.7.0：盤勢條件式部位。核心部位不因 ATR 變大而縮小；
    # 只有強趨勢條件成立才增加第二層部位，防禦條件可減碼或略過。
    use_regime_position_sizing: bool = False
    position_core_micro_units: int = 5
    position_addon_micro_units: int = 5
    position_defensive_micro_units: int = 0
    position_allow_downsize: bool = True
    # v0.8.4：動態風險部位可依「已實現權益」自前高的回撤線性降風險。
    # start_pct 前維持原風險；full_pct 時降至 floor 倍，之後不再降低。
    use_drawdown_risk_brake: bool = False
    position_drawdown_brake_start_pct: float = 4.0
    position_drawdown_brake_full_pct: float = 10.0
    position_drawdown_brake_floor: float = 0.4
    # v0.7.0：長期持有控制。固定停損與斷頭不受最短持有期限制。
    minimum_holding_bars: int = 0
    chandelier_exit_confirmation_bars: int = 1
    macd_exit_confirmation_bars: int = 1
    signal_exit_confirmation_bars: int = 1
    # 研究基準目標：只做比較輸出，不影響交易。
    benchmark_name: str = ""
    benchmark_annual_return_target: float = 0.0
    use_take_profit: bool = False
    take_profit_points: float = 200.0
    use_trailing_stop: bool = False
    trailing_points: float = 150.0
    use_signal_exit: bool = False   # v0.3.4 條件出場（策略層產生訊號欄位）
    # ---- 顯示用指標參數 ----
    kd_period: int = 9
    rsi_period: int = 14
    atr_period: int = 14
    vol_ma_period: int = 20
    ma_periods: tuple = tuple(DEFAULT_MA_PERIODS)
    # v0.8.0：多空出場可完全分離；未指定欄位自動沿用共用 exit。
    long_exit_overrides: dict = field(default_factory=dict)
    short_exit_overrides: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        """從 dict 建立，忽略未知欄位（相容舊參數檔）。"""
        valid = {f.name for f in fields(cls)}
        clean = {k: v for k, v in d.items() if k in valid}
        if "ma_periods" in clean:
            clean["ma_periods"] = tuple(int(x) for x in clean["ma_periods"])
        if "profit_tier_amounts" in clean and clean["profit_tier_amounts"] is not None:
            clean["profit_tier_amounts"] = tuple(float(x) for x in clean["profit_tier_amounts"])
        if "profit_tier_atr_multiples" in clean and clean["profit_tier_atr_multiples"] is not None:
            clean["profit_tier_atr_multiples"] = tuple(float(x) for x in clean["profit_tier_atr_multiples"])
        if "profit_tier_mults" in clean and clean["profit_tier_mults"] is not None:
            clean["profit_tier_mults"] = tuple(float(x) for x in clean["profit_tier_mults"])
        return cls(**clean)


EXIT_FIELDS = ["use_chandelier", "chandelier_period", "chandelier_mult",
               "use_profit_tier_chandelier", "profit_tier_chandelier_period",
               "profit_tier_amounts", "profit_tier_atr_multiples", "profit_tier_threshold_mode",
               "profit_tier_mults", "profit_tier_reference",
               "use_profit_scaled_macd_exclusion", "macd_reverse_exclude_profit_amount",
               "macd_reverse_exclude_atr_multiple",
               "use_macd_reverse",
               "use_fixed_stop", "stop_threshold_mode", "stop_points", "stop_atr_multiple",
               "use_entry_risk_cap", "max_entry_risk_amount",
               "use_dynamic_position_sizing", "use_account_margin_model",
               "position_sizing_capital",
               "position_risk_fraction", "position_stress_multiple",
               "position_max_micro_units", "position_use_stress_capital_check",
               "position_micro_point_value", "position_small_point_value", "position_large_point_value",
               "position_micro_margin", "position_small_margin", "position_large_margin",
               "position_micro_maintenance_margin", "position_small_maintenance_margin",
               "position_large_maintenance_margin",
               "position_micro_fee", "position_small_fee", "position_large_fee",
               "position_contract_mix_mode", "position_max_contract_point_value",
               "position_sizing_mode", "position_compounding", "use_safe_capital_position_sizing",
               "position_safe_capital_per_micro_unit", "position_min_cash_buffer",
               "position_drawdown_reserve_fraction", "position_gap_stress_points",
               "position_max_small_contracts", "stop_trading_after_margin_call",
               "use_regime_position_sizing",
               "position_core_micro_units", "position_addon_micro_units",
               "position_defensive_micro_units", "position_allow_downsize",
               "use_drawdown_risk_brake", "position_drawdown_brake_start_pct",
               "position_drawdown_brake_full_pct", "position_drawdown_brake_floor",
               "minimum_holding_bars",
               "chandelier_exit_confirmation_bars", "macd_exit_confirmation_bars",
               "signal_exit_confirmation_bars",
               "benchmark_name", "benchmark_annual_return_target",
               "use_take_profit", "take_profit_points",
               "use_trailing_stop", "trailing_points"]


# =====================================================================
# 策略 JSON <-> StrategyParams 轉換
# =====================================================================
def config_from_params(p: StrategyParams, name: str = "MACD_BB_Chandelier_v1",
                       symbol: str = "MTX", timeframe: str = "1D") -> dict:
    """用目前參數組出示範策略（MACD + Bollinger + Chandelier）的 JSON。"""
    long_conds = [
        {"type": "macd_hist_cross_up",
         "fast": p.macd_fast, "slow": p.macd_slow, "signal": p.macd_signal},
        {"type": "close_above_bollinger_mid",
         "period": p.bb_period, "std": p.bb_std},
    ]
    short_conds = [
        {"type": "macd_hist_cross_down",
         "fast": p.macd_fast, "slow": p.macd_slow, "signal": p.macd_signal},
        {"type": "close_below_bollinger_mid",
         "period": p.bb_period, "std": p.bb_std},
    ]
    if p.ma_filter_enabled:
        long_conds.append({"type": "close_above_ma",
                           "ma_type": p.ma_filter_type,
                           "period": p.ma_filter_period})
        short_conds.append({"type": "close_below_ma",
                            "ma_type": p.ma_filter_type,
                            "period": p.ma_filter_period})
    return {
        "name": name,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": p.direction,
        "entry_long": {"logic": "AND", "conditions": long_conds},
        "entry_short": {"logic": "AND", "conditions": short_conds},
        "exit": {k: getattr(p, k) for k in EXIT_FIELDS},
        "long_exit": dict(getattr(p, "long_exit_overrides", {}) or {}),
        "short_exit": dict(getattr(p, "short_exit_overrides", {}) or {}),
        # 顯示用指標參數一併保存，載回時介面才能還原
        "display": {"kd_period": p.kd_period, "rsi_period": p.rsi_period,
                    "atr_period": p.atr_period, "vol_ma_period": p.vol_ma_period,
                    "ma_periods": list(p.ma_periods)},
    }


def params_from_config(cfg: dict, base: StrategyParams = None) -> StrategyParams:
    """
    從策略 JSON 還原 StrategyParams（出場設定、方向、
    以及示範策略可對應的條件參數：MACD/BB/均線過濾）。
    自訂條件組合不會遺失——回測請直接用 run_strategy_config(df, cfg)。
    """
    d = asdict(base) if base else asdict(StrategyParams())
    d["direction"] = cfg.get("direction", d["direction"])
    for k, v in (cfg.get("exit") or {}).items():
        if k in d:
            d[k] = v
    for k, v in (cfg.get("display") or {}).items():
        if k in d:
            d[k] = v
    # v0.7.0：位置政策與研究設定也可放在頂層，避免每次新增欄位都改 JSON 結構。
    for section in ("position_policy", "research", "holding_policy"):
        for k, v in (cfg.get(section) or {}).items():
            if k in d:
                d[k] = v
    # 從進場條件回填示範策略參數（若存在對應條件）
    d["ma_filter_enabled"] = False
    _el = cfg.get("entry_long") or {}
    if isinstance(_el, list):                     # v0.3.3 多組合：取第一組回填參數
        _el = _el[0] if _el else {}
    for c in _el.get("conditions", []):
        t = c.get("type")
        if t in ("macd_hist_cross_up", "macd_hist_cross_down",
                 "macd_dif_cross_up", "macd_dif_cross_down"):
            d["macd_fast"] = int(c.get("fast", d["macd_fast"]))
            d["macd_slow"] = int(c.get("slow", d["macd_slow"]))
            d["macd_signal"] = int(c.get("signal", d["macd_signal"]))
        elif "bollinger" in str(t):
            d["bb_period"] = int(c.get("period", d["bb_period"]))
            d["bb_std"] = float(c.get("std", d["bb_std"]))
        elif t in ("close_above_ma", "close_below_ma"):
            d["ma_filter_enabled"] = True
            d["ma_filter_type"] = str(c.get("ma_type", d["ma_filter_type"]))
            d["ma_filter_period"] = int(c.get("period", d["ma_filter_period"]))
    d["long_exit_overrides"] = dict(cfg.get("long_exit") or cfg.get("exit_long") or {})
    d["short_exit_overrides"] = dict(cfg.get("short_exit") or cfg.get("exit_short") or {})
    # 新模式可只寫 position_sizing_mode，不必再同步舊布林欄位。
    mode = str(d.get("position_sizing_mode") or "fixed").lower()
    if mode in {"dynamic_safe_capital", "dynamic_safe_capital_capped"}:
        d["use_safe_capital_position_sizing"] = True
        d["use_account_margin_model"] = True
    elif mode == "dynamic_risk":
        d["use_dynamic_position_sizing"] = True
        d["use_account_margin_model"] = True
    return StrategyParams.from_dict(d)


def save_strategy_json(cfg: dict, path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def load_strategy_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# =====================================================================
# 訊號產生
# =====================================================================
def _chandelier_suffix(mult: float) -> str:
    """把吊燈倍數轉成安全欄位尾碼，例如 2.5 -> 2_5。"""
    return str(float(mult)).replace(".", "_").replace("-", "m")


def add_indicator_columns(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """加入畫圖與出場所需的指標欄位（macd_hist / chandelier_* 為出場必要）。"""
    out = df.copy().reset_index(drop=True)
    close = out["close"]
    m = ind.macd(close, p.macd_fast, p.macd_slow, p.macd_signal)
    b = ind.bollinger(close, p.bb_period, p.bb_std)
    ch = ind.chandelier_exit(out, p.chandelier_period, p.chandelier_mult)
    out = pd.concat([out, m, b, ch], axis=1)

    # v0.5.0：若啟用獲利分段吊燈，預先計算各倍數的吊燈線，供 backtester 依浮盈選用。
    if getattr(p, "use_profit_tier_chandelier", False):
        tier_period = int(getattr(p, "profit_tier_chandelier_period", p.chandelier_period) or p.chandelier_period)
        tier_mults = list(getattr(p, "profit_tier_mults", ()) or ())
        for mult in sorted({float(x) for x in tier_mults}):
            ch_t = ind.chandelier_exit(out, tier_period, mult)
            suf = _chandelier_suffix(mult)
            out[f"chandelier_long_m_{suf}"] = ch_t["chandelier_long"]
            out[f"chandelier_short_m_{suf}"] = ch_t["chandelier_short"]
    kd_df = ind.kd(out, p.kd_period)
    out["k"], out["d"] = kd_df["k"], kd_df["d"]
    out["rsi"] = ind.rsi(close, p.rsi_period)
    out["atr"] = ind.atr(out, p.atr_period)
    out["vol_ma"] = ind.volume_ma(out["volume"], p.vol_ma_period)
    for n in p.ma_periods:
        out[f"sma_{n}"] = ind.sma(close, n)
    if p.ma_filter_enabled:
        out["ma_filter"] = ind.ma(close, p.ma_filter_period, p.ma_filter_type)
    return out


def evaluate_combo(out: pd.DataFrame, block: dict):
    """
    v0.3.4：單一條件組合求值，支援 kcTrader 式三種條件槽（皆可選）：
    - conditions（滿足）    ：當根全部成立（AND），沿用 evaluate_block_with_reasons
    - ever（曾經滿足/前提） ：{"n": N, "conditions":[...]}，
      每個條件在「最近 N 根（含當根）」內至少成立過一次（rolling max，
      只用過去資料、無未來函數）
    - exclude（排除）       ：{"conditions":[...]}，任一當根成立則不進場
    """
    idx = out.index
    must = block.get("conditions") or []
    if must:
        sig, reasons = evaluate_block_with_reasons(
            out, {"logic": block.get("logic", "AND"), "conditions": must})
        sig = sig.fillna(False)
    else:
        sig = pd.Series(True, index=idx)
        reasons = pd.Series("", index=idx, dtype="object")

    ev = block.get("ever") or {}
    if ev.get("conditions"):
        n = max(1, int(ev.get("n", 10)))
        for spec_ in ev["conditions"]:
            s_now = evaluate_condition(out, spec_).fillna(False)
            s_ever = s_now.rolling(n, min_periods=1).max().astype(bool)
            sig = sig & s_ever
        reasons = reasons.where(~sig, reasons + f"（含前提·{n}根內）")

    ex = block.get("exclude") or {}
    if ex.get("conditions"):
        excl = pd.Series(False, index=idx)
        for spec_ in ex["conditions"]:
            excl = excl | evaluate_condition(out, spec_).fillna(False)
        sig = sig & ~excl

    return sig.fillna(False), reasons


def evaluate_entry(out: pd.DataFrame, spec):
    """
    進場條件求值（v0.3.3 新增，向下相容）：
    - spec 為 dict：單一條件組合（沿用 evaluate_block_with_reasons，行為不變）
    - spec 為 list：多個條件組合。組合內為 AND，組合之間為 OR，
      任一組合全部成立即進場。reasons 會標註是哪一個組合觸發。
    只是把既有函式的結果做 OR 彙總，不改變任何單一條件的計算。
    """
    if isinstance(spec, list):
        total = pd.Series(False, index=out.index)
        reasons = pd.Series("", index=out.index, dtype="object")
        for i, blk in enumerate(spec, 1):
            sig_i, r_i = evaluate_combo(out, blk)
            newly = sig_i & ~total          # 先觸發的組合優先留下說明
            prefix = f"組合{i}：" if len(spec) > 1 else ""
            reasons = reasons.where(~newly, prefix + r_i)
            total = total | sig_i
        return total, reasons
    return evaluate_block_with_reasons(out, spec)


def _apply_optional_filter(out: pd.DataFrame, signal: pd.Series, block) -> pd.Series:
    """把選用的盤勢過濾條件套在進場訊號上；未設定時完全相容舊策略。"""
    if not block:
        return signal.fillna(False)
    filt, _ = evaluate_combo(out, block)
    return (signal.fillna(False) & filt.fillna(False)).fillna(False)


def _position_units_for_direction(out: pd.DataFrame, cfg: dict, p: StrategyParams, direction: str):
    """依 JSON 盤勢條件產生每根訊號可使用的微台等值單位與盤勢標籤。

    優先順序：skip > defensive > addon > core。
    1口小台=5單位；2口小台=10單位。這裡只決定目標部位，
    保證金與跳空壓力仍由 backtester 在實際進場時檢查。
    """
    idx = out.index
    core = max(int(getattr(p, "position_core_micro_units", 5) or 0), 0)
    addon = max(int(getattr(p, "position_addon_micro_units", 5) or 0), 0)
    defensive = max(int(getattr(p, "position_defensive_micro_units", 0) or 0), 0)
    max_units = max(int(getattr(p, "position_max_micro_units", 10) or 0), 0)
    units = pd.Series(min(core, max_units), index=idx, dtype="int64")
    label = pd.Series("core", index=idx, dtype="object")
    policy = cfg.get("position_policy") or {}
    prefix = "long" if direction == "long" else "short"
    addon_block = policy.get(f"{prefix}_addon_block")
    defensive_block = policy.get(f"{prefix}_defensive_block")
    skip_block = policy.get(f"{prefix}_skip_block")
    if addon_block:
        sig, _ = evaluate_combo(out, addon_block)
        units.loc[sig] = min(core + addon, max_units)
        label.loc[sig] = "core+addon"
    if defensive_block:
        sig, _ = evaluate_combo(out, defensive_block)
        units.loc[sig] = min(defensive, max_units)
        label.loc[sig] = "defensive"
    if skip_block:
        sig, _ = evaluate_combo(out, skip_block)
        units.loc[sig] = 0
        label.loc[sig] = "skip"
    return units, label


def run_strategy_config(df: pd.DataFrame, cfg: dict,
                        p: StrategyParams = None) -> pd.DataFrame:
    """
    由策略 JSON 產生訊號。回傳含 long_entry / short_entry 與指標欄位的
    DataFrame，可直接餵給 backtester.run_backtest()。
    """
    if p is None:
        p = params_from_config(cfg)
    out = add_indicator_columns(df, p)

    direction = cfg.get("direction", p.direction)
    long_sig, long_reasons = evaluate_entry(out, cfg.get("entry_long"))
    short_sig, short_reasons = evaluate_entry(out, cfg.get("entry_short"))

    # v0.7.0：盤勢過濾與原始進場條件分離。可在不改策略積木的情況下
    # 比較「相同訊號、不同市場狀態」；未提供 filter 時與舊版完全一致。
    long_sig = _apply_optional_filter(out, long_sig, cfg.get("entry_long_filter"))
    short_sig = _apply_optional_filter(out, short_sig, cfg.get("entry_short_filter"))

    out["long_entry"] = long_sig if direction in ("long", "both") else False
    out["short_entry"] = short_sig if direction in ("short", "both") else False
    out["long_entry_reasons"] = long_reasons.where(out["long_entry"], "")
    out["short_entry_reasons"] = short_reasons.where(out["short_entry"], "")

    # v0.7.0：核心＋條件式加碼部位。只在進場訊號根決定目標部位，
    # 不使用未完成的下一根資料，也不改變進出場訊號。
    if getattr(p, "use_regime_position_sizing", False):
        lu, ll = _position_units_for_direction(out, cfg, p, "long")
        su, sl = _position_units_for_direction(out, cfg, p, "short")
        out["long_position_micro_units"] = lu
        out["short_position_micro_units"] = su
        out["long_position_regime"] = ll
        out["short_position_regime"] = sl

    # v0.3.4 條件出場：策略層產生訊號欄位，backtester 依 use_signal_exit 取用
    elb, esb = cfg.get("exit_long_block"), cfg.get("exit_short_block")
    if elb:
        out["exit_long_signal"], _ = evaluate_combo(out, elb)
    if esb:
        out["exit_short_signal"], _ = evaluate_combo(out, esb)
    return out


def macd_bollinger_chandelier(df: pd.DataFrame,
                              p: StrategyParams) -> pd.DataFrame:
    """
    示範策略：MACD + Bollinger + Chandelier（v2 起由條件模組組合而成）。
    多方進場：MACD histogram 由負轉正 AND 收盤>布林中線 AND（可開關）收盤>過濾均線
    空方進場：完全對稱。出場由 backtester 依 StrategyParams 開關執行。
    """
    return run_strategy_config(df, config_from_params(p), p)


# 策略註冊表：介面下拉選單來源。
# 新增 JSON 策略：STRATEGIES["我的策略"] = lambda df, p: run_strategy_config(df, my_cfg, p)
STRATEGIES = {
    "MACD + Bollinger + Chandelier": macd_bollinger_chandelier,
}
