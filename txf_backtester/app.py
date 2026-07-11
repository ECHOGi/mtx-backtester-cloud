# -*- coding: utf-8 -*-
"""
app.py - 台指期回測工具 Streamlit 介面（v0.6.1 手機批次操作修正版）。

v0.3.3 重點（回測核心零修改）：
- 「策略設定面板」彈出視窗（st.dialog + st.form）：
  進場條件 → 出場條件 → 按「開始回測」才執行；旁邊有「取消」。
- 禁止每調一個欄位就重跑回測：回測結果存在 session_state，
  只有按「開始回測」才重新計算；圖表顯示設定只重畫、不重算。
- 進出場條件遊戲化：條件組合（組合內 AND、組合之間 OR），
  每個條件都有中文名稱與簡短說明；新增 KD、RSI 條件。
- 左側欄只留低頻設定：策略設定面板／操作模式／圖表顯示／成本資金／
  資料設定／參數檔。移除「選策略」下拉（目前僅單一內建策略）。

啟動：py -m streamlit run app.py
"""
import copy
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile


def _safe_filename_part(text: str) -> str:
    """移除 Windows 檔名不允許字元。"""
    text = str(text).strip().replace("/", "-").replace("\\", "-")
    return re.sub(r'[<>:"|?*]+', "_", text) or "未命名"


def detect_record_dir() -> str:
    """偵測 Google Drive / Obsidian 回測紀錄資料夾。

    支援不同電腦路徑，例如：
    - G:\\我的雲端硬碟\\MTX Test Record
    - C:\\Users\\<使用者>\\我的雲端硬碟\\MTX Test Record
    也可用環境變數 MTX_TEST_RECORD_DIR 指定。
    """
    env = os.environ.get("MTX_TEST_RECORD_DIR")
    user_home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    candidates = [
        env,
        os.path.join(user_home, "我的雲端硬碟", "MTX Test Record"),
        r"C:\Users\PG\我的雲端硬碟\MTX Test Record",
        r"G:\我的雲端硬碟\MTX Test Record",
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    for c in candidates:
        if c and os.path.isdir(os.path.dirname(c)):
            return c
    return os.path.join(user_home, "我的雲端硬碟", "MTX Test Record")

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from backtester import CostModel, run_backtest
from config import DEFAULT_SYMBOL, SYMBOLS
from continuous_contract import build_continuous
from data_loader import DataError, clean_data, load_folder
from metrics import compute_metrics, metrics_to_df, yearly_stats
from strategies import (StrategyParams, params_from_config,
                        run_strategy_config)
from utils import df_to_csv_bytes, load_params_json, params_to_json_str

# ---------------- 頁面與樣式 ----------------
st.set_page_config(page_title="台指期回測工具", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
.stApp { background-color: #d9e2ea; }
.block-container { padding-top: 2.0rem; max-width: 1500px; }
[data-testid="stHeader"] { height: 2.0rem; }
[data-testid="stHeader"] > div { display: none; }
[data-testid="stToolbar"] { display: none; }
[data-testid="stDecoration"] { display: none; }
[data-testid="stSidebar"] { background-color: #16283c; }
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p,
[data-testid="stSidebar"] .stMarkdown, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] summary { color: #d8e4ef !important; }
[data-testid="stSidebar"] hr { border-color: #33506b; }
.metric-card { background:#f3f7fa; border:1px solid #c2d1dd; border-radius:12px;
  padding:10px 6px; text-align:center; box-shadow:0 1px 3px rgba(25,45,65,.18); }
.metric-card .lbl { font-size:.78rem; color:#5a6c7d; margin-bottom:2px; }
.metric-card .val { font-size:1.18rem; font-weight:700; color:#1c2b3a; }
.metric-card .pos { color:#c0392b; }
.metric-card .neg { color:#1e8449; }
.summary-card { background:#f3f7fa; border:1px solid #b8c9d8; border-radius:14px;
  padding:14px 18px; margin: 0 0 14px 0; box-shadow:0 1px 4px rgba(25,45,65,.16); }
.summary-card .title { font-size:1.08rem; font-weight:800; color:#1c2b3a; margin-bottom:8px; }
.summary-card .grid { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:8px 14px; }
.summary-card .item { color:#25384a; font-size:.93rem; }
.summary-card .label { color:#687b8c; font-size:.78rem; display:block; }
.combo-chip { display:inline-block; background:#e8f0f7; border:1px solid #a9c2d8;
  border-radius:16px; padding:2px 10px; margin:2px 4px 2px 0; font-size:.85rem; color:#1c2b3a; }
.strategy-banner { background:#1c2b3a; color:#e8f0f7; border-radius:12px;
  padding:10px 16px; margin-bottom:12px; font-size:.95rem; }
h1, h2, h3 { color:#1c2b3a; }
/* 大按鈕 */
div[data-testid="stButton"] > button[kind="primary"] {
  font-size:1.15rem; font-weight:800; padding:0.7rem 1rem; border-radius:14px; }
.cloud-card { background:#edf5fb; border:1px solid #adc4d8; border-radius:14px;
  padding:14px 16px; margin:10px 0 14px 0; color:#1c2b3a; }
.mobile-quick-card { background:#f7fbff; border:1px solid #a9c2d8; border-radius:16px;
  padding:14px 16px; margin-bottom:14px; box-shadow:0 1px 4px rgba(25,45,65,.12); }
@media (max-width: 768px) {
  .block-container { padding: 0.75rem 0.55rem 1.5rem 0.55rem; max-width: 100%; }
  .strategy-banner { font-size:.85rem; padding:8px 10px; border-radius:10px; }
  .summary-card { padding:10px 10px; }
  .summary-card .grid { grid-template-columns: 1fr; gap:6px; }
  .metric-card .val { font-size:1.02rem; }
  .cloud-card, .mobile-quick-card { padding:10px 10px; }
  h1 { font-size:1.35rem !important; }
  h2 { font-size:1.20rem !important; }
  h3 { font-size:1.05rem !important; }
  div[data-testid="stButton"] > button { min-height: 2.8rem; }
}
</style>
""", unsafe_allow_html=True)

UP_COLOR = "#d64550"
DOWN_COLOR = "#2f9e63"

SESSION_LABELS = {"一般盤": "regular", "盤後盤": "after_hours", "全部": "all"}
METHOD_LABELS = {"穩定換倉（預設）": "stable_rollover",
                 "成交量最大（每日）": "volume_max_daily",
                 "未沖銷契約數最大（每日）": "oi_max_daily"}
DIR_LABELS = {"多空雙向": "both", "只做多": "long", "只做空": "short"}
DIR_LABELS_INV = {v: k for k, v in DIR_LABELS.items()}
EXIT_REASON_LABELS = {
    "fixed_stop": "固定停損", "take_profit": "固定停利",
    "trailing_stop": "移動停損", "chandelier_long": "吊燈出場（多）",
    "chandelier_short": "吊燈出場（空）", "chandelier": "吊燈出場",
    "macd_reverse": "MACD 反向出場", "signal_exit": "條件出場",
    "margin_call": "斷頭強制平倉",
    "end_of_data": "資料結束強制平倉",
}
DIRECTION_LABELS = {"long": "多單", "short": "空單"}

COND_ZH = {
    "close_above_bollinger_upper": "收盤>布林上軌",
    "close_below_bollinger_lower": "收盤<布林下軌",
    "close_cross_up_bollinger_upper": "突破布林上軌",
    "close_cross_down_bollinger_upper": "跌回布林上軌下",
    "close_cross_up_bollinger_lower": "站回布林下軌",
    "close_cross_down_bollinger_lower": "跌破布林下軌",
    "close_cross_up_bollinger_mid": "收盤突破布林中線",
    "close_cross_down_bollinger_mid": "收盤跌破布林中線",
    "close_above_bollinger_mid": "收盤站上布林中線",
    "close_below_bollinger_mid": "收盤跌破布林中線",
    "macd_hist_cross_up": "MACD柱翻多", "macd_hist_cross_down": "MACD柱翻空",
    "macd_hist_positive": "MACD柱為正", "macd_hist_negative": "MACD柱為負",
    "macd_dif_above_zero": "DIF零軸上", "macd_dif_below_zero": "DIF零軸下",
    "ma_slope_up": "均線翻揚", "ma_slope_down": "均線下彎",
    "kd_k_above_d": "K在D上", "kd_k_below_d": "K在D下",
    "rsi_cross_up": "RSI上穿門檻", "rsi_cross_down": "RSI下穿門檻",
    "volume_above_prev": "單日爆量",
    "macd_dif_cross_up": "DIF上穿DEA", "macd_dif_cross_down": "DIF下穿DEA",
    "ma_bullish_alignment": "均線多頭排列", "ma_bearish_alignment": "均線空頭排列",
    "ma_cross_up": "均線黃金交叉", "ma_cross_down": "均線死亡交叉",
    "close_above_ma": "均線趨勢向上", "close_below_ma": "均線趨勢向下",
    "kd_cross_up": "KD黃金交叉", "kd_cross_down": "KD死亡交叉",
    "kd_k_above": "KD高檔", "kd_k_below": "KD低檔",
    "rsi_above": "RSI強勢", "rsi_below": "RSI弱勢",
    "volume_above_ma": "成交量放大", "volume_below_ma": "成交量萎縮",
    "long_entry": "多方進場訊號", "short_entry": "空方進場訊號",
}

TRADE_COL_ZH = {
    "signal_date": "訊號日", "signal_bar_index": "訊號K棒序",
    "entry_date": "進場日", "entry_execution_date": "進場執行日",
    "entry_bar_index": "進場K棒序",
    "exit_date": "出場日", "exit_bar_index": "出場K棒序",
    "direction": "方向", "entry_price": "進場價", "exit_price": "出場價",
    "quantity": "口數", "pnl_points": "損益點數", "pnl_amount": "損益金額",
    "holding_bars": "持倉K棒數", "exit_reason": "出場原因",
    "entry_reason": "進場條件",
    "max_adverse_points": "最大反向浮動點數",
    "max_adverse_amount": "最大反向浮動金額",
    "max_favorable_points": "最大順向浮動點數",
    "max_favorable_amount": "最大順向浮動金額",
    "required_safety_capital": "當筆最低所需安全資金",
}
TRADE_DISPLAY_COLS = ["訊號日", "進場日", "出場日", "方向", "進場價", "出場價",
                      "口數", "損益點數", "損益金額", "持倉K棒數",
                      "出場原因", "進場條件"]

# ============ 條件目錄：類型 → 型態（多空通用、二層勾選） ============
# PATTERNS[key] = (類型, 型態名稱, 簡短說明)；進場/出場/前提/排除皆可用
PATTERNS = {
    # --- MACD ---
    "macd_hist_up":   ("MACD", "柱狀圖翻多", "柱狀圖由負轉正"),
    "macd_hist_dn":   ("MACD", "柱狀圖翻空", "柱狀圖由正轉負"),
    "macd_hist_pos":  ("MACD", "柱狀圖為正", "多方動能持續中"),
    "macd_hist_neg":  ("MACD", "柱狀圖為負", "空方動能持續中"),
    "macd_dif_gold":  ("MACD", "DIF黃金交叉", "DIF 由下往上穿過 DEA"),
    "macd_dif_death": ("MACD", "DIF死亡交叉", "DIF 由上往下穿過 DEA"),
    "macd_dif_pos":   ("MACD", "DIF在零軸上", "中期趨勢偏多"),
    "macd_dif_neg":   ("MACD", "DIF在零軸下", "中期趨勢偏空"),
    # --- 布林通道 ---
    "bb_above_mid":   ("布林通道", "收盤在中線上", "收盤價大於布林中線"),
    "bb_below_mid":   ("布林通道", "收盤在中線下", "收盤價小於布林中線"),
    "bb_cross_up_mid": ("布林通道", "突破中線", "收盤由下往上穿越中線"),
    "bb_cross_dn_mid": ("布林通道", "跌破中線", "收盤由上往下穿越中線"),
    "bb_above_upper": ("布林通道", "收盤在上軌上", "強勢區，沿上軌行進"),
    "bb_below_lower": ("布林通道", "收盤在下軌下", "弱勢區，沿下軌行進"),
    "bb_break_upper": ("布林通道", "突破上軌", "收盤由下往上突破上軌（噴出）"),
    "bb_fall_upper":  ("布林通道", "跌回上軌下", "從上軌之上跌回（漲勢衰竭）"),
    "bb_back_lower":  ("布林通道", "站回下軌上", "跌破下軌後站回（超跌反轉）"),
    "bb_break_lower": ("布林通道", "跌破下軌", "收盤由上往下跌破下軌（趕底）"),
    # --- 均線 ---
    "ma_above":       ("均線", "收盤站上均線", "收盤價大於趨勢均線"),
    "ma_below":       ("均線", "收盤跌破均線", "收盤價小於趨勢均線"),
    "ma_gold":        ("均線", "黃金交叉", "短均線由下往上穿過長均線"),
    "ma_death":       ("均線", "死亡交叉", "短均線由上往下穿過長均線"),
    "ma_bull":        ("均線", "多頭排列", "短中長均線由上而下依序排列"),
    "ma_bear":        ("均線", "空頭排列", "短中長均線由下而上依序排列"),
    "ma_slope_up":    ("均線", "均線翻揚", "趨勢均線今日高於昨日"),
    "ma_slope_dn":    ("均線", "均線下彎", "趨勢均線今日低於昨日"),
    # --- KD ---
    "kd_gold":        ("KD", "黃金交叉", "K 由下往上穿過 D"),
    "kd_death":       ("KD", "死亡交叉", "K 由上往下穿過 D"),
    "kd_k_gt_d":      ("KD", "K在D上", "多方格局持續"),
    "kd_k_lt_d":      ("KD", "K在D下", "空方格局持續"),
    "kd_low":         ("KD", "低檔超賣", "K 值低於門檻（預設20）"),
    "kd_high":        ("KD", "高檔超買", "K 值高於門檻（預設80）"),
    # --- RSI ---
    "rsi_above":      ("RSI", "高於門檻", "RSI 大於門檻（預設50）"),
    "rsi_below":      ("RSI", "低於門檻", "RSI 小於門檻（預設50）"),
    "rsi_cross_up":   ("RSI", "上穿門檻", "RSI 由下往上穿越門檻"),
    "rsi_cross_dn":   ("RSI", "下穿門檻", "RSI 由上往下穿越門檻"),
    # --- 成交量 ---
    "vol_gt_ma":      ("成交量", "量增(勝量均)", "成交量大於量均線×倍數"),
    "vol_lt_ma":      ("成交量", "量縮(低量均)", "成交量小於量均線×倍數"),
    "vol_gt_prev":    ("成交量", "單日爆量", "成交量大於昨日量×倍數"),
}
CATEGORY_ORDER = ["MACD", "布林通道", "均線", "KD", "RSI", "成交量"]
CATEGORIES = {c: [k for k, v in PATTERNS.items() if v[0] == c]
              for c in CATEGORY_ORDER}
COND_LABELS = {k: f"{v[0]}·{v[1]}" for k, v in PATTERNS.items()}
# v0.3.4 以前的舊 key 對照（載入舊參數檔時自動遷移）
OLD_KEY_MAP = {
    "macd_up": "macd_hist_up", "macd_dn": "macd_hist_dn",
    "bb_mid_up": "bb_above_mid", "bb_mid_dn": "bb_below_mid",
    "bb_up_break": "bb_break_upper", "bb_dn_break": "bb_break_lower",
    "ma_trend_up": "ma_above", "ma_trend_dn": "ma_below",
    "ma_cross_up": "ma_gold", "ma_cross_dn": "ma_death",
    "ma_align_up": "ma_bull", "ma_align_dn": "ma_bear",
    "kd_cross_up": "kd_gold", "kd_cross_dn": "kd_death",
    "rsi_strong": "rsi_above", "rsi_weak": "rsi_below",
    "vol_burst": "vol_gt_ma", "vol_burst_s": "vol_gt_ma",
}
EXIT_DEFS = [
    ("use_fixed_stop",    "固定停損", "虧損達設定點數就出場，控制單筆風險"),
    ("use_take_profit",   "固定停利", "獲利達設定點數就出場，落袋為安"),
    ("use_trailing_stop", "移動停損", "從進場後最高（低）點回落設定點數出場"),
    ("use_chandelier",    "吊燈出場", "跌破 N 日極值 ± ATR×倍數的追蹤線出場"),
    ("use_macd_reverse",  "MACD 反向出場", "MACD 柱狀圖轉向（多單轉負／空單轉正）出場"),
]

PARAM_DEFAULTS = {
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "bb_period": 20, "bb_std": 2.0,
    "ma_filter_enabled": True, "ma_filter_period": 20, "ma_filter_type": "SMA",
    "use_chandelier": True, "chandelier_period": 22, "chandelier_mult": 3.0,
    "use_macd_reverse": True,
    "use_fixed_stop": True, "stop_points": 100.0,
    "use_take_profit": False, "take_profit_points": 200.0,
    "use_trailing_stop": False, "trailing_points": 150.0,
    "kd_period": 9, "rsi_period": 14, "atr_period": 14, "vol_ma_period": 20,
}
TH_DEFAULTS = {"ma_fast": 5, "ma_slow": 20, "align_periods": "5,10,20",
               "kd_low": 20.0, "kd_high": 80.0,
               "rsi_value": 50.0, "vol_mult": 1.5, "vol_prev_mult": 1.5}
def _empty_combo() -> dict:
    return {"must": [], "ever": [], "ever_n": 10, "exclude": []}


def _norm_combo(c) -> dict:
    """相容 v0.3.3 的 list 格式，統一成 must/ever/ever_n/exclude。"""
    base = _empty_combo()
    if isinstance(c, list):
        base["must"] = list(c)
    elif isinstance(c, dict):
        for k in base:
            if k in c:
                base[k] = c[k]
    base["ever_n"] = int(base.get("ever_n", 10) or 10)
    # 舊版條件 key 自動遷移 + 過濾未知 key
    for slot in ("must", "ever", "exclude"):
        keys = [OLD_KEY_MAP.get(k, k) for k in base[slot]]
        seen, out = set(), []
        for k in keys:
            if k in PATTERNS and k not in seen:
                out.append(k)
                seen.add(k)
        base[slot] = out
    return base


def combo_active(c: dict) -> bool:
    return bool(c.get("must") or c.get("ever"))


DEFAULT_STRAT = {
    "params": dict(PARAM_DEFAULTS),
    "direction": "both",
    "combos_long": [
        {"must": ["macd_hist_up", "bb_above_mid", "ma_above"],
         "ever": [], "ever_n": 10, "exclude": []},
        _empty_combo()],
    "combos_short": [
        {"must": ["macd_hist_dn", "bb_below_mid", "ma_below"],
         "ever": [], "ever_n": 10, "exclude": []},
        _empty_combo()],
    "exit_long": _empty_combo(),   # 條件出場（多單），可留空
    "exit_short": _empty_combo(),  # 條件出場（空單），可留空
    "th": dict(TH_DEFAULTS),
}

CHART_DEFAULTS = {
    "show_candlestick": True, "show_trade_markers": True,
    "show_bollinger": True, "show_chandelier_lines": True,
    "show_ma": True, "show_volume": True,
    "show_macd_panel": True, "show_kd_panel": True, "show_equity_curve": True,
}
for k, v in CHART_DEFAULTS.items():
    st.session_state.setdefault("w_" + k, v)
st.session_state.setdefault("w_ma_periods_text", "5,10,20,60,120,240")
st.session_state.setdefault("strat", copy.deepcopy(DEFAULT_STRAT))
st.session_state.setdefault("bt", None)
st.session_state.setdefault("run_request", False)


# ============ 條件 key -> 條件積木 JSON ============
def _align_list(th):
    try:
        return [int(x) for x in str(th["align_periods"]).replace("，", ",").split(",") if str(x).strip()]
    except ValueError:
        return [5, 10, 20]


def cond_spec(key: str, p: dict, th: dict) -> dict:
    m = {"fast": int(p["macd_fast"]), "slow": int(p["macd_slow"]),
         "signal": int(p["macd_signal"])}
    bb = {"period": int(p["bb_period"]), "std": float(p["bb_std"])}
    ma_f = {"ma_type": p["ma_filter_type"], "period": int(p["ma_filter_period"])}
    cross = {"ma_type": "SMA", "fast": int(th["ma_fast"]), "slow": int(th["ma_slow"])}
    kd = {"period": int(p["kd_period"])}
    rsi_v = {"value": float(th.get("rsi_value", th.get("rsi_long", 50))),
             "period": int(p["rsi_period"])}
    table = {
        "macd_hist_up": {"type": "macd_hist_cross_up", **m},
        "macd_hist_dn": {"type": "macd_hist_cross_down", **m},
        "macd_hist_pos": {"type": "macd_hist_positive", **m},
        "macd_hist_neg": {"type": "macd_hist_negative", **m},
        "macd_dif_gold": {"type": "macd_dif_cross_up", **m},
        "macd_dif_death": {"type": "macd_dif_cross_down", **m},
        "macd_dif_pos": {"type": "macd_dif_above_zero", **m},
        "macd_dif_neg": {"type": "macd_dif_below_zero", **m},
        "bb_above_mid": {"type": "close_above_bollinger_mid", **bb},
        "bb_below_mid": {"type": "close_below_bollinger_mid", **bb},
        "bb_cross_up_mid": {"type": "close_cross_up_bollinger_mid", **bb},
        "bb_cross_dn_mid": {"type": "close_cross_down_bollinger_mid", **bb},
        "bb_above_upper": {"type": "close_above_bollinger_upper", **bb},
        "bb_below_lower": {"type": "close_below_bollinger_lower", **bb},
        "bb_break_upper": {"type": "close_cross_up_bollinger_upper", **bb},
        "bb_fall_upper": {"type": "close_cross_down_bollinger_upper", **bb},
        "bb_back_lower": {"type": "close_cross_up_bollinger_lower", **bb},
        "bb_break_lower": {"type": "close_cross_down_bollinger_lower", **bb},
        "ma_above": {"type": "close_above_ma", **ma_f},
        "ma_below": {"type": "close_below_ma", **ma_f},
        "ma_gold": {"type": "ma_cross_up", **cross},
        "ma_death": {"type": "ma_cross_down", **cross},
        "ma_bull": {"type": "ma_bullish_alignment", "ma_type": "SMA",
                    "periods": _align_list(th)},
        "ma_bear": {"type": "ma_bearish_alignment", "ma_type": "SMA",
                    "periods": _align_list(th)},
        "ma_slope_up": {"type": "ma_slope_up", **ma_f},
        "ma_slope_dn": {"type": "ma_slope_down", **ma_f},
        "kd_gold": {"type": "kd_cross_up", **kd},
        "kd_death": {"type": "kd_cross_down", **kd},
        "kd_k_gt_d": {"type": "kd_k_above_d", **kd},
        "kd_k_lt_d": {"type": "kd_k_below_d", **kd},
        "kd_low": {"type": "kd_k_below", "value": float(th["kd_low"]), **kd},
        "kd_high": {"type": "kd_k_above", "value": float(th["kd_high"]), **kd},
        "rsi_above": {"type": "rsi_above", **rsi_v},
        "rsi_below": {"type": "rsi_below", **rsi_v},
        "rsi_cross_up": {"type": "rsi_cross_up", **rsi_v},
        "rsi_cross_dn": {"type": "rsi_cross_down", **rsi_v},
        "vol_gt_ma": {"type": "volume_above_ma",
                      "period": int(p["vol_ma_period"]),
                      "multiplier": float(th["vol_mult"])},
        "vol_lt_ma": {"type": "volume_below_ma",
                      "period": int(p["vol_ma_period"]),
                      "multiplier": float(th["vol_mult"])},
        "vol_gt_prev": {"type": "volume_above_prev",
                        "multiplier": float(th.get("vol_prev_mult", 1.5))},
    }
    return table[key]


EXIT_FIELDS = ["use_chandelier", "chandelier_period", "chandelier_mult",
               "use_macd_reverse", "use_fixed_stop", "stop_points",
               "use_take_profit", "take_profit_points",
               "use_trailing_stop", "trailing_points"]


def strat_to_config(strat: dict, symbol: str) -> dict:
    """把遊戲化設定轉成策略 JSON。
    entry 為組合 list（組合間 OR）；每個組合含三種條件槽（皆可選）：
    conditions=滿足(AND)、ever=曾經滿足(前提)、exclude=排除。"""
    p, th = strat["params"], strat["th"]

    def _block(combo: dict) -> dict:
        blk = {"logic": "AND",
               "conditions": [cond_spec(k, p, th) for k in combo["must"]]}
        if combo["ever"]:
            blk["ever"] = {"n": int(combo["ever_n"]),
                           "conditions": [cond_spec(k, p, th) for k in combo["ever"]]}
        if combo["exclude"]:
            blk["exclude"] = {"conditions": [cond_spec(k, p, th)
                                             for k in combo["exclude"]]}
        return blk

    combos_l = [_norm_combo(c) for c in strat["combos_long"]]
    combos_s = [_norm_combo(c) for c in strat["combos_short"]]
    exit_l = _norm_combo(strat.get("exit_long", {}))
    exit_s = _norm_combo(strat.get("exit_short", {}))
    use_signal_exit = combo_active(exit_l) or combo_active(exit_s)

    cfg = {
        "name": "MACD_BB_Chandelier_KD_RSI",
        "symbol": symbol, "timeframe": "1D",
        "direction": strat["direction"],
        "entry_long": [_block(c) for c in combos_l if combo_active(c)],
        "entry_short": [_block(c) for c in combos_s if combo_active(c)],
        "exit": {**{k: p[k] for k in EXIT_FIELDS},
                 "use_signal_exit": use_signal_exit},
        "display": {"kd_period": p["kd_period"], "rsi_period": p["rsi_period"],
                    "atr_period": p["atr_period"], "vol_ma_period": p["vol_ma_period"]},
        # 供介面完整還原遊戲化設定
        "ui_combos": {"long": combos_l, "short": combos_s,
                      "exit_long": exit_l, "exit_short": exit_s,
                      "th": th, "params": p},
    }
    if combo_active(exit_l):
        cfg["exit_long_block"] = _block(exit_l)
    if combo_active(exit_s):
        cfg["exit_short_block"] = _block(exit_s)
    return cfg


def strat_use_signal_exit(strat: dict) -> bool:
    return (combo_active(_norm_combo(strat.get("exit_long", {}))) or
            combo_active(_norm_combo(strat.get("exit_short", {}))))


def strat_to_params(strat: dict, ma_periods: tuple) -> StrategyParams:
    return StrategyParams(**strat["params"],
                          use_signal_exit=strat_use_signal_exit(strat),
                          direction=strat["direction"],
                          ma_periods=ma_periods)


def zh_strategy_summary(strat: dict) -> str:
    def _chips(keys):
        return "".join(f'<span class="combo-chip">{COND_LABELS[k]}</span>' for k in keys)

    def combos_txt(combos):
        parts = []
        for i, c in enumerate(combos):
            c = _norm_combo(c)
            if combo_active(c):
                txt = f"組合{'AB'[i]}：{_chips(c['must'])}" if c["must"] else f"組合{'AB'[i]}："
                if c["ever"]:
                    txt += f" ｜前提({c['ever_n']}根內曾滿足)：{_chips(c['ever'])}"
                if c["exclude"]:
                    txt += f" ｜排除：{_chips(c['exclude'])}"
                parts.append(txt)
        return "<br>".join(parts) if parts else "（未設定）"

    exits = "、".join(n for k, n, _ in EXIT_DEFS if strat["params"].get(k)) or "無"
    for side, nm in (("exit_long", "多單條件出場"), ("exit_short", "空單條件出場")):
        c = _norm_combo(strat.get(side, {}))
        if combo_active(c):
            exits += f"<br>{nm}：{_chips(c['must'] + c['ever'])}"
            if c["exclude"]:
                exits += f" ｜排除：{_chips(c['exclude'])}"
    return (f'<div class="summary-card"><div class="title">目前策略設定</div>'
            f'<div class="item"><span class="label">交易方向</span>'
            f'{DIR_LABELS_INV[strat["direction"]]}</div>'
            f'<div class="item"><span class="label">多單進場（任一組合成立即進場，組合內須全部成立）</span>'
            f'{combos_txt(strat["combos_long"])}</div>'
            f'<div class="item"><span class="label">空單進場</span>'
            f'{combos_txt(strat["combos_short"])}</div>'
            f'<div class="item"><span class="label">出場條件</span>{exits}</div></div>')


def zh_entry_reason(s) -> str:
    if not isinstance(s, str) or not s.strip():
        return s if isinstance(s, str) else ""
    s = re.sub(r"\(\{[^}]*\}\)", "", s)
    s = re.sub(r"\([^)]*=(?:[^)]*)\)", "", s)
    for en in sorted(COND_ZH, key=len, reverse=True):
        s = s.replace(en, COND_ZH[en])
    return s.replace(" AND ", " 且 ").replace(" OR ", " 或 ")


def zh_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame(columns=TRADE_DISPLAY_COLS)
    t = trades.copy()
    for c in ["signal_date", "entry_date", "entry_execution_date", "exit_date"]:
        if c in t.columns:
            t[c] = pd.to_datetime(t[c]).dt.strftime("%Y/%m/%d")
    t["direction"] = t["direction"].map(DIRECTION_LABELS).fillna(t["direction"])
    t["exit_reason"] = t["exit_reason"].map(EXIT_REASON_LABELS).fillna(t["exit_reason"])
    if "entry_reason" in t.columns:
        t["entry_reason"] = t["entry_reason"].map(zh_entry_reason)
    return t.rename(columns=TRADE_COL_ZH)


PREPARED_MTX_METHOD = "stable_rollover"
PREPARED_MTX_SESSION = "regular"
PREPARED_MTX_N_CONFIRM = 3
PREPARED_MTX_EXCLUDE_WEEKLY = True
PREPARED_MTX_FILE = "MTX_stable_rollover_daily.csv"
PREPARED_MTX_LOG_FILE = "MTX_stable_rollover_rollover_log.csv"

# v0.3.9：Obsidian 回測紀錄自動保存位置。
# Streamlit 的 download_button 由瀏覽器控制，不能強制指定使用者下載資料夾；
# 因此本機執行時，另外自動建立一個可由 Obsidian 直接閱讀的回測紀錄資料夾。
DEFAULT_RECORD_DIR = detect_record_dir()

# v0.4.0：概略模式固定以一口 MTX 做安全資金檢查。
MTX_ORIGINAL_MARGIN = 159000.0
MTX_SAFETY_STRESS_RATE = 0.25

# v0.4.1：批次回測策略數上限。v0.4.6：10 → 20。
BATCH_MAX_STRATEGIES = 20

# v0.4.2：策略投放箱。
# ChatGPT / 其他 AI 只要把批次策略 JSON 放到這個 Google Drive 同步資料夾，
# 平台就能直接讀取，不必經過 Codex，也不必用瀏覽器手動上傳。
STRATEGY_DROPBOX_DIR = os.path.join(DEFAULT_RECORD_DIR, "_策略投放箱")

# v0.5.2：新電腦預設使用雲端策略連結，不再依賴 Google Drive 桌面版同步資料夾。
# v0.5.3：Google Drive 若回傳 HTML 分享頁，會自動改用內建雲端失效測試檔，不中斷平台故障診斷。
# v0.5.4：雲端作業模式預設不依賴本機 Google Drive；結果以下載 ZIP／複製總覽文字為主。
# v0.5.5：雲端作業模式優先自動上傳回測結果到 Google Drive。
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CLOUD_BATCH_JSON_URL = "https://drive.google.com/file/d/1aLnJSRQJ1HW1_7GgR32VXzCtexmSsd_A/view?usp=drivesdk"
BUNDLED_FALLBACK_FILENAME = "雲端失效測試用檔案-測試平台故障或連線異常.json"
BUNDLED_FALLBACK_PATH = os.path.join(APP_DIR, "bundled_strategies", BUNDLED_FALLBACK_FILENAME)

# v0.5.5：Streamlit 雲端部署時，程式從 repo 根目錄啟動；app.py 位於 txf_backtester/。
# 因此資料預設要優先指向 txf_backtester/data，才能讀到 txf_backtester/data/prepared/*.csv。
DEFAULT_GDRIVE_RESULTS_PARENT_FOLDER_ID = "1KhjGNzHqPTXzIcDEM_fy0clOCZoy25Fa"  # MTX Test Record / _批次回測結果
DEFAULT_GDRIVE_STRATEGY_FOLDER_ID = "1boC1wtRriJv1SADAOZ-d9uA3KLkmqWtR"  # MTX Test Record / _策略投放箱


def prepared_mtx_path(folder: str) -> str:
    """回傳 MTX prepared 預設日K檔路徑。"""
    return os.path.join(folder, "prepared", PREPARED_MTX_FILE)


def has_prepared_mtx(folder: str) -> bool:
    """是否已建立 MTX prepared 預設日K檔。"""
    return os.path.exists(prepared_mtx_path(folder))


def detect_default_data_folder() -> str:
    """自動偵測資料路徑，避免本機與 Streamlit Cloud 路徑不同。"""
    candidates = [
        "txf_backtester/data",  # Streamlit Community Cloud：main file path = txf_backtester/app.py
        "data",                 # 本機在 txf_backtester 內啟動
        os.path.join(APP_DIR, "data"),
        "txf_backtester/data/prepared",  # 備援：使用者曾手動指定此層
        "data/prepared",
    ]
    for c in candidates:
        if has_prepared_mtx(c):
            return c
    for c in candidates:
        if os.path.isdir(c):
            return c
    return "txf_backtester/data"


DEFAULT_DATA_FOLDER = detect_default_data_folder()


def calculate_mtx_safety_settings(cont: pd.DataFrame, end_date, point_value: float) -> dict:
    """計算一口 MTX 的安全資金設定。

    正式名詞固定為：
    原始保證金、安全緩衝金額、安全資金。
    """
    original_margin = MTX_ORIGINAL_MARGIN
    hist = cont[pd.to_datetime(cont["datetime"]).dt.date <= end_date].tail(250)
    if hist.empty:
        hist = cont.tail(250)
    base_high = float(hist["high"].max()) if not hist.empty else 0.0
    buffer_points = base_high * MTX_SAFETY_STRESS_RATE
    buffer_amount = buffer_points * float(point_value)
    safety_capital = original_margin + buffer_amount
    return {
        "original_margin": original_margin,
        "safety_stress_rate": MTX_SAFETY_STRESS_RATE,
        "safety_base_high": base_high,
        "safety_buffer_points": buffer_points,
        "safety_buffer_amount": buffer_amount,
        "safety_capital": safety_capital,
    }


def safety_settings_markdown(info: dict) -> str:
    return (
        f"原始保證金：**{info['original_margin']:,.0f} 元**  \n"
        f"安全緩衝金額：**{info['safety_buffer_amount']:,.0f} 元**  \n"
        f"安全資金：**{info['safety_capital']:,.0f} 元**  \n"
        "斷頭檢查：**啟用**"
    )


def ensure_strategy_dropbox() -> tuple:
    """確保策略投放箱存在，回傳 (ok, message)。"""
    try:
        os.makedirs(STRATEGY_DROPBOX_DIR, exist_ok=True)
        return True, STRATEGY_DROPBOX_DIR
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def list_strategy_dropbox_files() -> list:
    """列出策略投放箱內的 JSON 檔，依修改時間由新到舊排序。"""
    ok, _ = ensure_strategy_dropbox()
    if not ok or not os.path.isdir(STRATEGY_DROPBOX_DIR):
        return []
    files = []
    for name in os.listdir(STRATEGY_DROPBOX_DIR):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(STRATEGY_DROPBOX_DIR, name)
        if os.path.isfile(path):
            files.append({
                "name": name,
                "path": path,
                "mtime": os.path.getmtime(path),
            })
    return sorted(files, key=lambda x: x["mtime"], reverse=True)


def load_batch_json_from_dropbox(path: str) -> str:
    """從策略投放箱讀取批次策略 JSON 文字。"""
    if not os.path.abspath(path).startswith(os.path.abspath(STRATEGY_DROPBOX_DIR)):
        raise ValueError("只能讀取策略投放箱內的 JSON 檔。")
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def _extract_google_drive_file_id(url: str) -> str:
    """從常見 Google Drive 檔案連結取出 file id。"""
    text = (url or "").strip()
    m = re.search(r"/file/d/([^/]+)", text)
    if m:
        return m.group(1)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
    if qs.get("id"):
        return qs["id"][0]
    return ""


def normalize_cloud_strategy_url(url: str) -> str:
    """將 Google Drive 預覽網址轉成可下載網址；非 Drive URL 則原樣使用。"""
    text = (url or "").strip()
    file_id = _extract_google_drive_file_id(text)
    if file_id and "drive.google.com" in text:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return text


def load_batch_json_from_cloud_url(url: str) -> str:
    """從雲端連結下載批次策略 JSON。

    支援 Google Drive 檔案分享連結與一般可直接下載的 JSON URL。
    """
    raw_url = (url or "").strip()
    if not raw_url:
        raise ValueError("請先貼上雲端策略 JSON 連結。")
    download_url = normalize_cloud_strategy_url(raw_url)
    req = urllib.request.Request(
        download_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(5 * 1024 * 1024)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"無法下載雲端策略 JSON：{e}") from e

    text = data.decode("utf-8-sig", errors="replace").strip()
    head = text[:300].lower()
    if not text:
        raise ValueError("雲端策略 JSON 是空檔。")
    if "<html" in head or "<!doctype" in head:
        raise ValueError("下載到的是網頁，不是 JSON。請確認 Google Drive 檔案已開放『知道連結者可檢視』，或改貼可直接下載的 JSON 連結。")
    try:
        json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"雲端檔案不是有效 JSON：{e}") from e
    return text


def load_bundled_fallback_json() -> str:
    """讀取內建雲端失效測試檔。

    用途：僅在 Google Drive 分享頁無法提供有效 JSON 時，
    驗證平台故障處理與連線異常備援流程；不代表正式研究批次。
    """
    if not os.path.exists(BUNDLED_FALLBACK_PATH):
        raise FileNotFoundError(BUNDLED_FALLBACK_PATH)
    with open(BUNDLED_FALLBACK_PATH, "r", encoding="utf-8-sig") as f:
        text = f.read()
    json.loads(text)
    return text


def queue_batch_mode(mode_label: str) -> None:
    """依 UI 選項排程批次回測，供雲端、手機、本機與手動上傳共用。"""
    st.session_state.pop("batch_bt", None)
    st.session_state.pop("sample_validation_bt", None)
    if mode_label.startswith("前後期行情對照"):
        st.session_state.pop("batch_period_override", None)
        st.session_state["batch_validation_request"] = True
    elif mode_label.startswith("前期行情回測"):
        st.session_state["batch_period_override"] = ("前期行情 2015～2023", "2015-01-01", "2023-12-31")
        st.session_state["batch_run_request"] = True
    elif mode_label.startswith("後期牛市回測"):
        st.session_state["batch_period_override"] = ("後期牛市 2024～資料末日", "2024-01-01", None)
        st.session_state["batch_run_request"] = True
    else:
        st.session_state.pop("batch_period_override", None)
        st.session_state["batch_run_request"] = True


def load_cloud_or_bundled_batch_json(cloud_url: str, show_message: bool = True) -> tuple[str, str, str]:
    """優先讀雲端 JSON；失敗時回退內建雲端失效測試檔。

    回傳：(json_text, loaded_from, warning_message)。warning_message 為空代表雲端成功。
    """
    try:
        raw = load_batch_json_from_cloud_url(cloud_url)
        if show_message:
            st.success("已成功讀取雲端策略 JSON。")
        return raw, "cloud:" + str(cloud_url), ""
    except Exception as cloud_error:  # noqa: BLE001
        raw = load_bundled_fallback_json()
        msg = "雲端策略 JSON 讀取失敗，已改用『雲端失效測試用檔案』驗證平台是否能正常處理連線異常；此檔不列入正式策略研究。" f" 原因：{cloud_error}"
        if show_message:
            st.warning(msg)
        return raw, "bundled:" + BUNDLED_FALLBACK_FILENAME, msg


def _infer_batch_display_name(raw: str, loaded_from: str = "") -> str:
    """取得手機介面要顯示的策略檔名稱。"""
    source = str(loaded_from or "")
    if source.startswith("bundled:"):
        return source.split(":", 1)[1] or BUNDLED_FALLBACK_FILENAME
    if source.startswith("mobile_upload:"):
        return source.split(":", 1)[1]
    if source.startswith("cloud:"):
        parsed = urllib.parse.urlparse(source.split(":", 1)[1])
        name = os.path.basename(parsed.path)
        if name.lower().endswith(".json"):
            return name
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            batch_name = str(obj.get("batch_name") or obj.get("name") or "").strip()
            if batch_name:
                return batch_name if batch_name.lower().endswith(".json") else batch_name + ".json"
    except Exception:
        pass
    return "已載入策略 JSON"


def set_batch_json_and_queue(raw: str, loaded_from: str, mode_label: str, display_name: str = "") -> None:
    """設定批次 JSON 文字、記錄顯示名稱並排程回測。"""
    json.loads(raw)
    st.session_state["batch_strategy_json_text"] = raw
    st.session_state["batch_loaded_file"] = loaded_from
    st.session_state["batch_loaded_display_name"] = display_name or _infer_batch_display_name(raw, loaded_from)
    queue_batch_mode(mode_label)


def _st_secret_get(key: str, default=None):
    """安全讀取 Streamlit secrets；本機沒有 secrets.toml 時不報錯。"""
    try:
        return st.secrets.get(key, default)
    except Exception:  # noqa: BLE001
        return default


def get_gdrive_service_account_info():
    """讀取舊版 service account 設定，僅作向下相容備援。"""
    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:  # noqa: BLE001
        pass
    raw = _st_secret_get("GDRIVE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "")
    if raw:
        return json.loads(raw)
    return None


def get_gdrive_oauth_config() -> dict | None:
    """讀取 Google Drive OAuth Refresh Token 設定。"""
    client_id = _st_secret_get("GDRIVE_OAUTH_CLIENT_ID", "") or os.environ.get("GDRIVE_OAUTH_CLIENT_ID", "")
    client_secret = _st_secret_get("GDRIVE_OAUTH_CLIENT_SECRET", "") or os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET", "")
    refresh_token = _st_secret_get("GDRIVE_OAUTH_REFRESH_TOKEN", "") or os.environ.get("GDRIVE_OAUTH_REFRESH_TOKEN", "")
    token_uri = (
        _st_secret_get("GDRIVE_OAUTH_TOKEN_URI", "")
        or os.environ.get("GDRIVE_OAUTH_TOKEN_URI", "")
        or "https://oauth2.googleapis.com/token"
    )
    if client_id and client_secret and refresh_token:
        return {
            "auth_type": "oauth",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "token_uri": token_uri,
        }
    return None


def get_gdrive_auth_config() -> dict | None:
    """OAuth 優先；沒有 OAuth 時才使用舊版 service account。"""
    oauth_config = get_gdrive_oauth_config()
    if oauth_config:
        return oauth_config
    service_account_info = get_gdrive_service_account_info()
    if service_account_info:
        return {"auth_type": "service_account", "service_account_info": service_account_info}
    return None


def get_gdrive_results_parent_folder_id() -> str:
    return (
        _st_secret_get("GDRIVE_RESULTS_PARENT_FOLDER_ID", "")
        or os.environ.get("GDRIVE_RESULTS_PARENT_FOLDER_ID", "")
        or DEFAULT_GDRIVE_RESULTS_PARENT_FOLDER_ID
    )


def get_gdrive_strategy_folder_id() -> str:
    return (
        _st_secret_get("GDRIVE_STRATEGY_FOLDER_ID", "")
        or os.environ.get("GDRIVE_STRATEGY_FOLDER_ID", "")
        or DEFAULT_GDRIVE_STRATEGY_FOLDER_ID
    )


@st.cache_data(ttl=60, show_spinner=False)
def list_cloud_strategy_files() -> list:
    """從 Google Drive 的 _策略投放箱列出 JSON，最新者排第一。"""
    auth_config = get_gdrive_auth_config()
    if not auth_config:
        raise ValueError("尚未設定 Google Drive OAuth Secrets。")
    from google_drive_uploader import list_json_files_in_drive_folder

    return list_json_files_in_drive_folder(auth_config, get_gdrive_strategy_folder_id())


def load_batch_json_from_drive_file(file_id: str) -> str:
    """以 OAuth 從策略投放箱下載並驗證一個 batch JSON。"""
    auth_config = get_gdrive_auth_config()
    if not auth_config:
        raise ValueError("尚未設定 Google Drive OAuth Secrets。")
    from google_drive_uploader import download_drive_file_bytes

    data = download_drive_file_bytes(auth_config, file_id)
    text = data.decode("utf-8-sig", errors="strict").strip()
    if not text:
        raise ValueError("雲端策略 JSON 是空檔。")
    json.loads(text)
    return text


def upload_result_zip_to_google_drive(zip_bytes: bytes, zip_name: str, result_folder_name: str) -> tuple[str, str]:
    """將回測 ZIP 與解壓後內容自動上傳到 Google Drive。"""
    auth_config = get_gdrive_auth_config()
    if not auth_config:
        return "", "尚未設定 Google Drive OAuth Secrets。"
    parent_id = get_gdrive_results_parent_folder_id()
    if not parent_id:
        return "", "尚未設定 Google Drive 目標資料夾 ID。"
    try:
        from google_drive_uploader import upload_zip_result_to_drive

        result = upload_zip_result_to_drive(
            auth_config=auth_config,
            parent_folder_id=parent_id,
            result_folder_name=result_folder_name,
            zip_name=zip_name,
            zip_bytes=zip_bytes,
        )
        return result.get("folder_url", ""), ""
    except Exception as e:  # noqa: BLE001
        return "", str(e)


def _load_prepared_mtx(folder: str) -> tuple:
    """讀取已整理好的 MTX stable_rollover 日K資料。

    prepared 檔已經是單一商品、一般盤、穩定換倉後的日K資料，
    因此不再讀取 2015～2026 全商品原始 CSV。
    """
    path = prepared_mtx_path(folder)
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    cont = pd.read_csv(path, encoding="utf-8-sig")
    if "datetime" not in cont.columns:
        raise DataError(f"prepared 檔缺少 datetime 欄位：{path}")
    cont["datetime"] = pd.to_datetime(cont["datetime"], errors="coerce")
    cont = cont.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in cont.columns]
    if missing:
        raise DataError(f"prepared 檔缺少必要欄位 {missing}：{path}")
    for c in required + ["open_interest"]:
        if c in cont.columns:
            cont[c] = pd.to_numeric(cont[c], errors="coerce")
    cont = cont.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    log_path = os.path.join(folder, "prepared", PREPARED_MTX_LOG_FILE)
    if os.path.exists(log_path):
        roll_log = pd.read_csv(log_path, encoding="utf-8-sig")
    else:
        roll_log = pd.DataFrame()
    return cont, roll_log


@st.cache_data(show_spinner="讀取回測資料中...")
def cached_continuous(folder: str, symbol: str, session: str, method: str,
                      exclude_weekly: bool, n_confirm: int) -> tuple:
    # v0.3.7：MTX 優先使用 prepared stable_rollover 日K快取。
    # 目前階段只正式整合 MTX prepared data；若使用其他商品或檔案不存在，
    # 保留原本 raw CSV 載入流程，避免破壞舊功能。
    if symbol == "MTX" and os.path.exists(prepared_mtx_path(folder)):
        return _load_prepared_mtx(folder)

    raw = load_folder(folder, skip_bad=True)
    clean = clean_data(raw, symbol=symbol, session=session)
    return build_continuous(clean, method=method,
                            exclude_weekly=exclude_weekly, n_confirm=n_confirm)


def reset_defaults():
    st.session_state["strat"] = copy.deepcopy(DEFAULT_STRAT)
    for k in list(st.session_state.keys()):
        if k.startswith(("w_", "s_")) and k != "w_ui_mode":
            del st.session_state[k]
    st.session_state.pop("last_symbol", None)
    st.session_state.pop("loaded_file", None)
    st.session_state.pop("dlg_seeded", None)


def apply_loaded_params(d: dict):
    """載入策略 JSON：優先還原遊戲化組合（ui_combos），否則盡量回填。"""
    strat = copy.deepcopy(DEFAULT_STRAT)
    if "ui_combos" in d:
        ui = d["ui_combos"]
        strat["combos_long"] = [_norm_combo(c) for c in ui.get("long", [])][:2]
        strat["combos_short"] = [_norm_combo(c) for c in ui.get("short", [])][:2]
        while len(strat["combos_long"]) < 2:
            strat["combos_long"].append(_empty_combo())
        while len(strat["combos_short"]) < 2:
            strat["combos_short"].append(_empty_combo())
        strat["exit_long"] = _norm_combo(ui.get("exit_long", {}))
        strat["exit_short"] = _norm_combo(ui.get("exit_short", {}))
        strat["th"].update({k: v for k, v in ui.get("th", {}).items() if k in strat["th"]})
        strat["params"].update({k: v for k, v in ui.get("params", {}).items()
                                if k in strat["params"]})
        strat["direction"] = d.get("direction", strat["direction"])
    else:  # 舊版 JSON：回填參數與出場，組合維持預設
        p = params_from_config(d) if ("entry_long" in d or "exit" in d) else None
        src = p.__dict__ if p else d
        strat["params"].update({k: v for k, v in src.items() if k in strat["params"]})
        strat["direction"] = src.get("direction", strat["direction"])
    st.session_state["strat"] = strat
    st.session_state.pop("s_direction_label", None)  # 讓面板下次開啟時重新灌入


# ============ 策略設定面板（彈出視窗 + form） ============
def _seed_slot(prefix: str, keys, n=None):
    """把一個條件槽（型態 key 清單）灌進面板暫存欄位。"""
    keys = set(keys or [])
    for k in PATTERNS:
        st.session_state[f"{prefix}_p_{k}"] = k in keys
    for cat in CATEGORY_ORDER:
        st.session_state[f"{prefix}_cat_{cat}"] = any(
            k in keys for k in CATEGORIES[cat])
    if n is not None:
        st.session_state[f"{prefix}_n"] = int(n)


def _seed_combo(prefix: str, combo: dict):
    combo = _norm_combo(combo)
    _seed_slot(prefix + "m", combo["must"])
    _seed_slot(prefix + "e", combo["ever"], combo["ever_n"])
    _seed_slot(prefix + "x", combo["exclude"])


def _seed_dialog():
    """把已套用的策略設定灌進面板暫存欄位（開啟面板時執行一次）。"""
    strat = st.session_state["strat"]
    for ci in range(2):
        _seed_combo(f"s_L{ci}", strat["combos_long"][ci])
        _seed_combo(f"s_S{ci}", strat["combos_short"][ci])
    _seed_combo("s_XL", strat.get("exit_long", {}))
    _seed_combo("s_XS", strat.get("exit_short", {}))
    for k, v in strat["params"].items():
        st.session_state["s_p_" + k] = v
    for k, v in strat["th"].items():
        st.session_state["s_t_" + k] = v
    st.session_state["s_direction_label"] = DIR_LABELS_INV[strat["direction"]]


def _collect_slot(prefix: str) -> list:
    """收集一個條件槽：只計入「類型有勾」且「型態有勾」的條件。"""
    ss = st.session_state
    out = []
    for cat in CATEGORY_ORDER:
        if ss.get(f"{prefix}_cat_{cat}"):
            out += [k for k in CATEGORIES[cat] if ss.get(f"{prefix}_p_{k}")]
    return out


def _collect_combo(prefix: str) -> dict:
    return {"must": _collect_slot(prefix + "m"),
            "ever": _collect_slot(prefix + "e"),
            "ever_n": int(st.session_state.get(prefix + "e_n", 10) or 10),
            "exclude": _collect_slot(prefix + "x")}


def _collect_dialog() -> dict:
    strat = {"params": {}, "th": {}, "combos_long": [], "combos_short": []}
    for ci in range(2):
        strat["combos_long"].append(_collect_combo(f"s_L{ci}"))
        strat["combos_short"].append(_collect_combo(f"s_S{ci}"))
    strat["exit_long"] = _collect_combo("s_XL")
    strat["exit_short"] = _collect_combo("s_XS")
    for k, dv in PARAM_DEFAULTS.items():
        v = st.session_state.get("s_p_" + k, dv)
        strat["params"][k] = type(dv)(v) if not isinstance(dv, bool) else bool(v)
    for k, dv in TH_DEFAULTS.items():
        strat["th"][k] = st.session_state.get("s_t_" + k, dv)
    strat["direction"] = DIR_LABELS[st.session_state.get("s_direction_label", "多空雙向")]
    return strat


# ============ 面板編輯器：類型 → 型態 二層勾選 ============
def _slot_editor(prefix: str, title: str, hint: str, with_n: bool = False):
    """一個條件槽：先勾「類型」，再展開該類型的「型態」勾選格。"""
    st.markdown(f"**{title}**")
    st.caption(hint)
    ss = st.session_state
    cat_cols = st.columns(len(CATEGORY_ORDER))
    for i, cat in enumerate(CATEGORY_ORDER):
        n_sel = sum(1 for k in CATEGORIES[cat] if ss.get(f"{prefix}_p_{k}"))
        label = f"{cat} ✓{n_sel}" if (n_sel and not ss.get(f"{prefix}_cat_{cat}")) \
            else cat
        cat_cols[i].checkbox(label, key=f"{prefix}_cat_{cat}")
    for cat in CATEGORY_ORDER:
        if ss.get(f"{prefix}_cat_{cat}"):
            with st.container(border=True):
                st.markdown(f"▼ {cat} 型態（可複選）")
                pcols = st.columns(4)
                for j, k in enumerate(CATEGORIES[cat]):
                    _, name, desc = PATTERNS[k]
                    with pcols[j % 4]:
                        st.checkbox(name, key=f"{prefix}_p_{k}", help=desc)
    if with_n:
        st.number_input("前提回看 N 根（最近 N 根內曾成立即可）",
                        1, 120, key=f"{prefix}_n")


def _combo_editor(prefix: str, title: str, expanded: bool = False):
    """一個條件組合 = 滿足 + 曾經滿足（前提）+ 排除，三槽皆為可選。"""
    with st.expander(title, expanded=expanded):
        _slot_editor(prefix + "m", "① 滿足（AND）",
                     "勾選「類型」後展開該類型的型態；勾選的型態當根需全部成立。")
        st.markdown("---")
        _slot_editor(prefix + "e", "② 曾經滿足（前提，可選）",
                     "所選型態只要在最近 N 根 K 棒內成立過一次即可。", with_n=True)
        st.markdown("---")
        _slot_editor(prefix + "x", "③ 排除（可選）",
                     "任一勾選的型態當根成立，就不觸發這個組合。")


@st.dialog("🎮 策略設定面板", width="large")
def strategy_dialog():
    # 哨兵鍵：面板關閉後 Streamlit 會回收未渲染的 widget 狀態，
    # 重新開啟時哨兵不存在 -> 從已套用的策略設定重新灌入暫存值。
    if "s_direction_label" not in st.session_state:
        _seed_dialog()

    st.caption("流程：1️⃣ 勾選進場條件 → 2️⃣ 勾選出場條件 → 3️⃣ 按「開始回測」。"
               "面板內調整不會重新計算，按下開始回測才會執行。")
    tab_l, tab_s, tab_x, tab_p = st.tabs(
        ["1️⃣ 多單進場", "1️⃣ 空單進場", "2️⃣ 出場條件", "🔧 參數細調"])

    with tab_l:
        st.radio("交易方向", list(DIR_LABELS.keys()),
                 key="s_direction_label", horizontal=True)
        st.info("同一組合內：滿足條件全部成立（AND）且前提成立且未被排除，才進場；"
                "設定多個組合時，任一組合成立就進場（OR）。")
        _combo_editor("s_L0", "🅰 多單進場組合 A", expanded=True)
        _combo_editor("s_L1", "🅱 多單進場組合 B（不需要可留空）")

    with tab_s:
        st.info("空單條件與多單獨立設定；只做多時可留空。所有型態多空通用。")
        _combo_editor("s_S0", "🅰 空單進場組合 A", expanded=True)
        _combo_editor("s_S1", "🅱 空單進場組合 B（不需要可留空）")

    with tab_x:
        st.info("勾選要啟用的出場方式（可複選）。觸價類依序檢查：停損 → 停利 → 移動停損。")
        xc = st.columns(2)
        with xc[0]:
            st.checkbox("**固定停損**", key="s_p_use_fixed_stop")
            st.caption("虧損達設定點數就出場")
            st.number_input("停損點數", 1.0, 5000.0, step=10.0, key="s_p_stop_points")
            st.checkbox("**固定停利**", key="s_p_use_take_profit")
            st.caption("獲利達設定點數就出場")
            st.number_input("停利點數", 1.0, 10000.0, step=10.0, key="s_p_take_profit_points")
            st.checkbox("**移動停損**", key="s_p_use_trailing_stop")
            st.caption("從進場後最高（低）點回落設定點數出場")
            st.number_input("移動停損點數", 1.0, 5000.0, step=10.0, key="s_p_trailing_points")
        with xc[1]:
            st.checkbox("**吊燈出場**", key="s_p_use_chandelier")
            st.caption("跌破 N 日極值 ± ATR×倍數的追蹤線出場（收盤確認）")
            st.number_input("吊燈週期", 2, 200, key="s_p_chandelier_period")
            st.number_input("吊燈 ATR 倍數", 0.5, 10.0, step=0.1, key="s_p_chandelier_mult")
            st.checkbox("**MACD 反向出場**", key="s_p_use_macd_reverse")
            st.caption("MACD 柱狀圖轉向就出場（多單轉負／空單轉正，收盤確認）")
        st.markdown("---")
        st.markdown("##### 條件出場（可選）：符合條件組合就平倉（收盤確認）")
        st.caption("和進場一樣有三種條件槽（滿足／曾經滿足／排除），全部留空＝不使用。")
        _combo_editor("s_XL", "🔻 多單條件出場組合")
        _combo_editor("s_XS", "🔺 空單條件出場組合")

    with tab_p:
        st.caption("以下是各條件用到的參數，不改也能直接回測。")
        pc = st.columns(3)
        with pc[0]:
            st.markdown("**MACD**")
            st.number_input("快線週期", 2, 100, key="s_p_macd_fast")
            st.number_input("慢線週期", 3, 300, key="s_p_macd_slow")
            st.number_input("訊號線週期", 2, 100, key="s_p_macd_signal")
            st.markdown("**布林通道**")
            st.number_input("布林週期", 2, 300, key="s_p_bb_period")
            st.number_input("標準差倍數", 0.5, 5.0, step=0.1, key="s_p_bb_std")
        with pc[1]:
            st.markdown("**均線類**")
            st.selectbox("趨勢均線型態", ["SMA", "EMA", "WMA"], key="s_p_ma_filter_type")
            st.number_input("趨勢均線週期", 2, 500, key="s_p_ma_filter_period")
            st.number_input("交叉：短均週期", 2, 200, key="s_t_ma_fast")
            st.number_input("交叉：長均週期", 3, 500, key="s_t_ma_slow")
            st.text_input("排列均線（逗號分隔）", key="s_t_align_periods")
        with pc[2]:
            st.markdown("**KD／RSI／量能**")
            st.number_input("KD 週期", 2, 100, key="s_p_kd_period")
            st.number_input("KD 低檔門檻", 1.0, 50.0, step=1.0, key="s_t_kd_low")
            st.number_input("KD 高檔門檻", 50.0, 99.0, step=1.0, key="s_t_kd_high")
            st.number_input("RSI 週期", 2, 100, key="s_p_rsi_period")
            st.number_input("RSI 門檻", 1.0, 99.0, step=1.0, key="s_t_rsi_value")
            st.number_input("量能倍數（vs 量均）", 0.5, 10.0, step=0.1, key="s_t_vol_mult")
            st.number_input("爆量倍數（vs 昨日量）", 0.5, 10.0, step=0.1,
                            key="s_t_vol_prev_mult")
            st.number_input("量均週期", 2, 200, key="s_p_vol_ma_period")

    st.markdown("---")
    b1, b2 = st.columns([2.5, 1])
    run = b1.button("▶ 開始回測", type="primary", use_container_width=True,
                    key="dlg_run_flag")
    cancel = b2.button("取消", use_container_width=True, key="dlg_cancel_flag")

    # 面板內提交（真實執行時 dialog 為 fragment，會走這裡）
    if run:
        strat = _collect_dialog()
        if not (any(combo_active(c) for c in strat["combos_long"]) or
                any(combo_active(c) for c in strat["combos_short"])):
            st.error("請至少在多單或空單勾選一個進場條件（滿足或前提）。")
            return
        st.session_state["strat"] = strat
        st.session_state["run_request"] = True
        st.rerun()
    if cancel:
        st.rerun()


def handle_pending_dialog_submit():
    """
    後援處理：若按鈕觸發的是「整頁重跑」而非 fragment 重跑（例如測試環境），
    面板本體不會再執行，改由這裡套用提交結果。
    正常 fragment 流程中，本函式看到的按鈕旗標必為 False，不會重複執行。
    """
    if st.session_state.pop("dlg_run_flag", False):
        strat = _collect_dialog()
        if (any(combo_active(c) for c in strat["combos_long"]) or
                any(combo_active(c) for c in strat["combos_short"])):
            st.session_state["strat"] = strat
            st.session_state["run_request"] = True
    elif st.session_state.pop("dlg_cancel_flag", False):
        pass  # 取消：不套用（暫存值會在面板關閉後由 Streamlit 自動回收）


# 提交後援處理（fragment 與整頁重跑皆涵蓋）
handle_pending_dialog_submit()

# ================= 側欄（低頻／輔助設定） =================
with st.sidebar:
    st.markdown("## 台指期回測工具")

    # 1. 策略設定面板
    if st.button("🎮 策略設定面板", type="primary", use_container_width=True,
                 help="設定進場條件、出場條件與參數，按「開始回測」才會執行"):
        strategy_dialog()

    # 2. 操作模式
    st.markdown("### 操作模式")
    work_mode = st.radio(
        "作業環境",
        ["雲端作業模式", "本機桌機模式"],
        index=0,
        key="w_work_mode",
        horizontal=True,
        help="雲端作業模式：不依賴本機 Google Drive 路徑；若已設定 Google Drive API，回測完成會自動上傳結果。",
    )
    cloud_operation_mode = (work_mode == "雲端作業模式")
    layout_mode = st.radio(
        "畫面配置",
        ["桌機完整介面", "手機精簡介面"],
        index=0,
        key="w_layout_mode",
        horizontal=True,
        help="手機精簡介面會在主畫面提供大按鈕與簡化操作；桌機版保留原本介面。",
    )
    mobile_mode = (layout_mode == "手機精簡介面")
    ui_mode = st.radio("介面細節度", ["概略模式", "細節模式"], key="w_ui_mode",
                       horizontal=True,
                       help="概略模式：成本與資金用預設值。細節模式：可逐項調整。")
    simple_mode = (ui_mode == "概略模式")
    st.button("恢復預設值", on_click=reset_defaults, use_container_width=True)

    # 3. 圖表顯示設定
    st.markdown("### 圖表顯示設定")
    with st.expander("勾選要顯示的圖層", expanded=False):
        st.caption("只控制畫面，不會改變回測結果，也不會重新回測。")
        st.checkbox("K 線", key="w_show_candlestick")
        st.checkbox("進場／出場標記", key="w_show_trade_markers")
        st.checkbox("布林通道", key="w_show_bollinger")
        st.checkbox("吊燈線", key="w_show_chandelier_lines")
        st.checkbox("均線", key="w_show_ma")
        st.checkbox("成交量", key="w_show_volume")
        st.checkbox("MACD 副圖", key="w_show_macd_panel")
        st.checkbox("KD 副圖", key="w_show_kd_panel")
        st.checkbox("資金曲線", key="w_show_equity_curve")
        st.text_input("圖表均線週期（逗號分隔）", key="w_ma_periods_text")

    # 4. 交易成本與資金設定
    st.markdown("### 交易成本與資金設定")
    cost_box = st.empty()

    # 5. 資料設定
    st.markdown("### 資料設定")
    folder = st.text_input("CSV 資料夾路徑", value=DEFAULT_DATA_FOLDER,
                           help="雲端預設會自動指向 txf_backtester/data；本機則通常是 data。")
    symbol = st.selectbox("商品", list(SYMBOLS.keys()),
                          index=list(SYMBOLS.keys()).index(DEFAULT_SYMBOL),
                          format_func=lambda s: f"{s} {SYMBOLS[s]['name']}")
    date_box = st.container()
    if not simple_mode:
        session_label = st.selectbox("交易時段", list(SESSION_LABELS.keys()), index=0)
        method_label = st.selectbox("連續契約規則", list(METHOD_LABELS.keys()), index=0)
        n_confirm = st.number_input("換倉確認天數", 1, 10, value=3)
        exclude_weekly = st.checkbox("排除週契約（到期月份含 W）", value=True)
    else:
        session_label, method_label = "一般盤", "穩定換倉（預設）"
        n_confirm, exclude_weekly = 3, True
        st.caption("使用預設：一般盤｜穩定換倉｜換倉確認 3 天｜排除週契約")

    if symbol == "MTX" and has_prepared_mtx(folder):
        session_label, method_label = "一般盤", "穩定換倉（預設）"
        n_confirm, exclude_weekly = 3, True
        st.success(f"已偵測到 MTX prepared 回測資料，本版將直接讀取：{prepared_mtx_path(folder)}")

    # 4.（補填）成本與資金預設值；實際顯示在資料日期確定後補入 cost_box。
    spec = SYMBOLS[symbol]
    if st.session_state.get("last_symbol") != symbol:
        st.session_state["w_point_value"] = float(spec["point_value"])
        st.session_state["w_fee"] = float(spec["fee"])
        st.session_state["w_slippage"] = float(spec["slippage_points"])
        st.session_state["w_initial_capital"] = float(spec["margin_reference"])
        st.session_state["last_symbol"] = symbol
    st.session_state.setdefault("w_point_value", float(spec["point_value"]))
    st.session_state.setdefault("w_fee", float(spec["fee"]))
    st.session_state.setdefault("w_slippage", float(spec["slippage_points"]))
    st.session_state.setdefault("w_initial_capital", float(spec["margin_reference"]))
    st.session_state.setdefault("w_use_tax", True)  # v0.4.6：期交稅預設計入

    # 6. 參數檔／進階設定
    st.markdown("### 參數檔／進階設定")
    up = st.file_uploader("載入策略參數 JSON", type=["json"])
    if up is not None and st.session_state.get("loaded_file") != up.name + str(up.size):
        try:
            apply_loaded_params(load_params_json(up))
            st.session_state["loaded_file"] = up.name + str(up.size)
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"參數檔讀取失敗: {e}")

    st.markdown("#### 批次策略回測")
    with st.expander("雲端策略投放箱（預設）", expanded=True):
        st.caption("直接讀取 Google Drive 的 `_策略投放箱`；最新 batch JSON 會排在第一個。")
        cloud_files = []
        cloud_list_error = ""
        selected_cloud_file = None
        try:
            cloud_files = list_cloud_strategy_files()
        except Exception as e:  # noqa: BLE001
            cloud_list_error = str(e)

        if cloud_list_error:
            st.warning(f"暫時無法讀取雲端策略投放箱：{cloud_list_error}")
        elif cloud_files:
            labels = [
                f"{item.get('name', '未命名.json')}｜{item.get('modifiedTime', '')}"
                for item in cloud_files
            ]
            cloud_pick = st.selectbox(
                "目前選擇的雲端批次策略",
                options=list(range(len(cloud_files))),
                format_func=lambda i: labels[i],
                key="batch_cloud_drive_pick",
            )
            selected_cloud_file = cloud_files[cloud_pick]
            st.success(f"已連線策略投放箱，共找到 {len(cloud_files)} 個 JSON。")
            st.caption(f"目前檔案：`{selected_cloud_file.get('name', '')}`")
        else:
            selected_cloud_file = None
            st.info("雲端策略投放箱目前沒有 JSON 檔。")

        cloud_mode = st.radio(
            "雲端策略批次回測模式",
            [
                "前後期行情對照：2015～2023 一般行情 vs 2024～資料末日牛市行情",
                "前期行情回測：2015～2023",
                "後期牛市回測：2024～資料末日",
                "目前畫面期間回測：使用上方起迄日",
            ],
            index=0,
            key="batch_cloud_mode",
            help="前後期行情對照會自動跑兩段並產生對照表；其餘模式只跑單一期間。",
        )
        refresh_col, run_col = st.columns(2)
        if refresh_col.button("🔄 重新整理策略", use_container_width=True):
            list_cloud_strategy_files.clear()
            st.rerun()
        if run_col.button(
            "▶ 開始雲端批次回測",
            use_container_width=True,
            type="primary",
            disabled=not bool(selected_cloud_file),
        ):
            try:
                raw = load_batch_json_from_drive_file(selected_cloud_file["id"])
                loaded_from = "gdrive:" + selected_cloud_file["id"] + ":" + selected_cloud_file.get("modifiedTime", "")
                set_batch_json_and_queue(raw, loaded_from, cloud_mode, selected_cloud_file.get("name", ""))
            except Exception as e:  # noqa: BLE001
                st.error(f"批次策略讀取失敗：{e}")

        with st.container():
            st.markdown("##### 手動雲端連結（備用）")
            cloud_url = st.text_input(
                "雲端策略 JSON 連結",
                value=st.session_state.get("batch_cloud_url", DEFAULT_CLOUD_BATCH_JSON_URL),
                key="batch_cloud_url",
            )
            st.caption(f"連線異常測試備援檔：{BUNDLED_FALLBACK_FILENAME}（不列入正式策略研究）")
            if st.button("使用手動連結載入", use_container_width=True):
                try:
                    raw, loaded_from, _ = load_cloud_or_bundled_batch_json(cloud_url, show_message=True)
                    set_batch_json_and_queue(raw, loaded_from, cloud_mode)
                except Exception as e:  # noqa: BLE001
                    st.error(f"批次策略讀取失敗：{e}")

    with st.expander("本機策略投放箱（備用）", expanded=False):
        ok, msg = ensure_strategy_dropbox()
        st.caption(f"投放箱路徑：`{STRATEGY_DROPBOX_DIR}`")
        if not ok:
            st.warning(f"無法建立或讀取策略投放箱：{msg}")
        else:
            files = list_strategy_dropbox_files()
            if files:
                st.success(f"策略投放箱已連線，找到 {len(files)} 個 JSON。")
                labels = [f"{f['name']}｜{pd.to_datetime(f['mtime'], unit='s').strftime('%Y-%m-%d %H:%M:%S')}"
                          for f in files]
                pick = st.selectbox("目前選擇的批次策略 JSON",
                                    options=list(range(len(files))),
                                    format_func=lambda i: labels[i],
                                    key="batch_dropbox_pick")
                st.caption(f"目前檔案：`{files[pick]['name']}`")
                dropbox_mode = st.radio(
                    "本機投放箱批次回測模式",
                    [
                        "前後期行情對照：2015～2023 一般行情 vs 2024～資料末日牛市行情",
                        "前期行情回測：2015～2023",
                        "後期牛市回測：2024～資料末日",
                        "目前畫面期間回測：使用上方起迄日",
                    ],
                    index=0,
                    key="batch_dropbox_mode",
                    help="前後期行情對照會自動跑兩段並產生對照表；其餘模式只跑單一期間。",
                )
                if st.button("▶ 開始本機投放箱批次回測", use_container_width=True, type="secondary"):
                    try:
                        raw = load_batch_json_from_dropbox(files[pick]["path"])
                        set_batch_json_and_queue(raw, files[pick]["path"] + str(files[pick]["mtime"]), dropbox_mode, files[pick]["name"])
                    except Exception as e:  # noqa: BLE001
                        st.error(f"讀取投放箱策略 JSON 失敗：{e}")
            else:
                st.info("本機策略投放箱目前沒有 JSON 檔。新電腦建議直接使用上方『雲端策略連結』。")

    st.markdown("#### 手動載入批次策略 JSON")
    batch_up = st.file_uploader("手動載入批次策略 JSON（最多20組）", type=["json"],
                                key="batch_strategy_json_uploader")
    manual_ready = False
    if batch_up is not None:
        batch_key = batch_up.name + str(batch_up.size)
        if st.session_state.get("batch_loaded_file") != batch_key:
            raw = batch_up.read()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            st.session_state["batch_strategy_json_text"] = raw
            st.session_state["batch_loaded_file"] = batch_key
            st.session_state["batch_loaded_display_name"] = batch_up.name
            st.session_state.pop("batch_bt", None)
        manual_ready = True
        st.success(f"已載入手動 JSON：{batch_up.name}")
        st.caption("批次 JSON 可放策略陣列，或 {\"strategies\": [...]}。最多執行 20 組。")
    manual_mode = st.radio(
        "手動 JSON 批次回測模式",
        [
            "前後期行情對照：2015～2023 一般行情 vs 2024～資料末日牛市行情",
            "前期行情回測：2015～2023",
            "後期牛市回測：2024～資料末日",
            "目前畫面期間回測：使用上方起迄日",
        ],
        index=0,
        key="batch_manual_mode",
        disabled=not manual_ready,
    )
    if st.button("▶ 開始手動批次回測", use_container_width=True, type=("primary" if manual_ready else "secondary"), disabled=not manual_ready):
        queue_batch_mode(manual_mode)
    st.caption("選擇一種模式後按「開始批次回測」。前後期行情對照會自動比較 2015～2023 與 2024～資料末日。")

if mobile_mode:
    st.markdown("""
    <style>
    [data-testid="stSidebar"] { width: min(88vw, 21rem); }
    .stDataFrame { font-size: .78rem; }
    </style>
    """, unsafe_allow_html=True)

# ---------------- 目前圖表／策略輔助設定 ----------------
try:
    ma_periods = tuple(int(x) for x in
                       st.session_state["w_ma_periods_text"].replace("，", ",").split(",")
                       if str(x).strip())
except ValueError:
    ma_periods = (5, 10, 20, 60, 120, 240)
chart_options = {k: bool(st.session_state["w_" + k]) for k in CHART_DEFAULTS}

# ---------------- 載入資料（快取，僅供期間選擇與回測） ----------------
try:
    cont, roll_log = cached_continuous(
        folder, symbol, SESSION_LABELS[session_label],
        METHOD_LABELS[method_label], exclude_weekly, int(n_confirm))
except DataError as e:
    st.error(f"資料載入失敗：{e}")
    st.info("若要使用本版 MTX 加速流程，請先執行 prepare_mtx_data.py 產生 data/prepared/MTX_stable_rollover_daily.csv。")
    st.stop()
except FileNotFoundError as e:
    st.error(f"找不到資料夾或檔案：{e}")
    st.info("若要使用本版 MTX 加速流程，請先執行 prepare_mtx_data.py 產生 data/prepared/MTX_stable_rollover_daily.csv。")
    st.stop()

dmin, dmax = cont["datetime"].min().date(), cont["datetime"].max().date()
with date_box:
    c1, c2 = st.columns(2)
    with c1:
        d_start = st.date_input("回測起日", value=dmin, min_value=dmin, max_value=dmax)
    with c2:
        d_end = st.date_input("回測迄日", value=dmax, min_value=dmin, max_value=dmax)

# ---------------- 成本與安全資金設定 ----------------
safety_info = calculate_mtx_safety_settings(cont, d_end, float(st.session_state["w_point_value"])) if symbol == "MTX" else {}
with cost_box.container():
    if simple_mode:
        if symbol == "MTX":
            st.caption(
                f"使用預設值：每點 {st.session_state['w_point_value']:.0f} 元｜"
                f"手續費 {st.session_state['w_fee']:.0f} 元/口｜"
                f"滑價 {st.session_state['w_slippage']:.1f} 點｜"
                f"期交稅 {'計入' if st.session_state['w_use_tax'] else '不計'}｜"
                f"初始資金 {st.session_state['w_initial_capital']:,.0f} 元。")
            st.markdown(safety_settings_markdown(safety_info))
        else:
            st.caption(
                f"使用預設值：每點 {st.session_state['w_point_value']:.0f} 元｜"
                f"手續費 {st.session_state['w_fee']:.0f} 元/口｜"
                f"滑價 {st.session_state['w_slippage']:.1f} 點｜"
                f"期交稅 {'計入' if st.session_state['w_use_tax'] else '不計'}｜"
                f"初始資金 {st.session_state['w_initial_capital']:,.0f} 元。")
    else:
        with st.expander("成本與資金（點開調整）", expanded=False):
            st.number_input("每點價值（元）", min_value=1.0, step=1.0, key="w_point_value")
            st.number_input("單邊手續費（元／口）", min_value=0.0, step=1.0, key="w_fee")
            st.number_input("單邊滑價（點）", min_value=0.0, step=0.5, key="w_slippage")
            st.checkbox("計入期交稅", key="w_use_tax")
            st.number_input("初始資金（元）", min_value=1000.0, step=1000.0,
                            key="w_initial_capital",
                            help="用於報酬率與資金曲線")
            if symbol == "MTX":
                safety_info = calculate_mtx_safety_settings(cont, d_end, float(st.session_state["w_point_value"]))
                st.markdown(safety_settings_markdown(safety_info))

point_value = float(st.session_state["w_point_value"])
fee = float(st.session_state["w_fee"])
slippage = float(st.session_state["w_slippage"])
use_tax = bool(st.session_state["w_use_tax"])
initial_capital = float(st.session_state["w_initial_capital"])
tax_rate = SYMBOLS[symbol]["tax_rate"] if use_tax else 0.0
if symbol == "MTX":
    safety_info = calculate_mtx_safety_settings(cont, d_end, point_value)
else:
    safety_info = {
        "original_margin": float(SYMBOLS[symbol]["margin_reference"]),
        "safety_buffer_amount": 0.0,
        "safety_capital": float(SYMBOLS[symbol]["margin_reference"]),
        "safety_buffer_points": 0.0,
        "safety_base_high": 0.0,
        "safety_stress_rate": 0.0,
    }


def settings_hash() -> str:
    payload = json.dumps({
        "strat": st.session_state["strat"], "symbol": symbol, "folder": folder,
        "session": session_label, "method": method_label,
        "n_confirm": int(n_confirm), "excl_w": bool(exclude_weekly),
        "d0": str(d_start), "d1": str(d_end),
        "pv": point_value, "fee": fee, "slip": slippage,
        "tax": use_tax, "cap": initial_capital,
        "safety_buffer_amount": round(float(safety_info.get("safety_buffer_amount", 0.0)), 2),
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(payload.encode()).hexdigest()


def execute_backtest():
    """只有按「開始回測」才會走到這裡。"""
    strat = st.session_state["strat"]
    mask = (cont["datetime"].dt.date >= d_start) & (cont["datetime"].dt.date <= d_end)
    data = cont[mask].reset_index(drop=True)
    if len(data) < 30:
        st.error("回測區間資料不足 30 根 K 棒，請放寬區間。")
        return
    cfg = strat_to_config(strat, symbol)
    params = strat_to_params(strat, ma_periods)
    cost = CostModel(
        point_value=point_value, fee=fee,
        slippage_points=slippage, tax_rate=tax_rate,
        use_margin_call_check=(symbol == "MTX"),
        safety_buffer_amount=float(safety_info.get("safety_buffer_amount", 0.0)),
        original_margin_amount=float(safety_info.get("original_margin", SYMBOLS[symbol]["margin_reference"])),
    )
    with st.spinner("回測計算中..."):
        sig = run_strategy_config(data, cfg, params)
        trades, equity = run_backtest(sig, cost, params)
        m = compute_metrics(trades, equity,
                            margin_reference=SYMBOLS[symbol]["margin_reference"],
                            quantity=cost.quantity,
                            initial_capital=initial_capital,
                            market_data=data)
    st.session_state["bt"] = {
        "sig": sig, "trades": trades, "equity": equity, "m": m,
        "trades_zh": zh_trades(trades), "cfg": cfg, "params": params,
        "cost": cost, "initial_capital": initial_capital, "symbol": symbol,
        "d_start": d_start, "d_end": d_end, "n_bars": len(data),
        "cont": cont, "roll_log": roll_log,
        "strat": copy.deepcopy(strat), "hash": settings_hash(),
        "ui_mode": ui_mode,
        "safety_info": copy.deepcopy(safety_info),
    }


def _sweep_set_nested(cfg: dict, path: str, value):
    """v0.4.6：依 'exit.chandelier_mult' 這種點記法設定巢狀欄位。"""
    keys = path.split(".")
    node = cfg
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value


def _expand_sweeps(obj: dict) -> list:
    """v0.4.6：展開 sweep 參數掃描設定（與 run_batch_backtest.py 相同格式）。

    {"sweep": {"base": {...策略config...}, "param": "exit.chandelier_mult",
               "values": [2.0, 2.5, 3.0], "name_prefix": "吊燈22x"}}
    sweep 可為單一物件或陣列；可與 strategies（固定對照組）並存。
    """
    sweeps = obj.get("sweep")
    if sweeps is None:
        return []
    if isinstance(sweeps, dict):
        sweeps = [sweeps]
    items = []
    for si, sw in enumerate(sweeps, start=1):
        base = sw.get("base")
        param = sw.get("param")
        values = sw.get("values")
        if not isinstance(base, dict) or not param or not isinstance(values, list):
            raise ValueError(f"sweep 第 {si} 組格式錯誤：需要 base(物件)/param(字串)/values(陣列)。")
        prefix = str(sw.get("name_prefix") or f"{param}=")
        for v in values:
            cfg = copy.deepcopy(base)
            _sweep_set_nested(cfg, param, v)
            cfg["name"] = f"{prefix}{v}"
            items.append(cfg)
    return items


def parse_strategy_batch(text: str) -> tuple:
    """讀取批次策略 JSON，回傳 (batch_name, [(name, cfg), ...])。

    支援三種格式：
    1) [ strategy_config, strategy_config, ... ]
    2) {"batch_name": "名稱", "strategies": [ strategy_config, ... ]}
    3) v0.4.6：{"batch_name": "...", "strategies": [...對照組...], "sweep": {...}}

    每一組也可寫成 {"name": "策略名", "config": strategy_config}。
    """
    obj = json.loads(text)
    batch_name = "MTX批次回測"
    if isinstance(obj, list):
        raw_items = obj
    elif isinstance(obj, dict):
        batch_name = str(obj.get("batch_name") or obj.get("name") or batch_name)
        raw_items = obj.get("strategies") or obj.get("items")
        sweep_items = _expand_sweeps(obj)
        if raw_items is None:
            # 單一策略 JSON 也允許執行，方便測試。
            raw_items = [] if sweep_items else [obj]
        if not isinstance(raw_items, list):
            raise ValueError("strategies 必須是陣列。")
        raw_items = list(raw_items) + sweep_items
    else:
        raise ValueError("批次策略 JSON 必須是策略陣列，或包含 strategies 的物件。")

    if not isinstance(raw_items, list):
        raise ValueError("strategies 必須是陣列。")
    if len(raw_items) == 0:
        raise ValueError("批次策略 JSON 沒有策略。")
    if len(raw_items) > BATCH_MAX_STRATEGIES:
        raise ValueError(f"一次最多只能放 {BATCH_MAX_STRATEGIES} 組策略，目前有 {len(raw_items)} 組。")

    out = []
    for i, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 組策略不是 JSON 物件。")
        if isinstance(item.get("config"), dict):
            cfg = copy.deepcopy(item["config"])
            name = item.get("name") or item.get("label") or cfg.get("name")
        elif isinstance(item.get("strategy_config"), dict):
            cfg = copy.deepcopy(item["strategy_config"])
            name = item.get("name") or item.get("label") or cfg.get("name")
        else:
            cfg = copy.deepcopy(item)
            name = cfg.get("name") or item.get("label")
        name = str(name or f"策略{i:02d}")
        cfg.setdefault("name", name)
        cfg["symbol"] = symbol
        out.append((name, cfg))
    return batch_name, out



def _sort_batch_compare(df: pd.DataFrame) -> pd.DataFrame:
    """依實際判讀優先度排序：報酬回撤比、總損益、回撤、獲利因子。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    rr = pd.to_numeric(out.get("報酬回撤比"), errors="coerce").fillna(float("-inf"))
    pnl = pd.to_numeric(out.get("總損益(元)"), errors="coerce").fillna(float("-inf"))
    dd = pd.to_numeric(out.get("最大回撤(元)"), errors="coerce").abs().fillna(float("inf"))
    pf = pd.to_numeric(out.get("獲利因子"), errors="coerce").fillna(float("-inf"))
    out["__rr"] = rr
    out["__pnl"] = pnl
    out["__dd_abs"] = dd
    out["__pf"] = pf
    out = out.sort_values(
        by=["__rr", "__pnl", "__dd_abs", "__pf"],
        ascending=[False, False, True, False],
        kind="mergesort",
    )
    return out.drop(columns=["__rr", "__pnl", "__dd_abs", "__pf"]).reset_index(drop=True)

def _batch_compare_row(idx: int, name: str, m_: dict) -> dict:
    return {
        "策略編號": idx,
        "策略名稱": name,
        "報酬回撤比": m_.get("報酬回撤比", ""),
        "總損益(元)": m_.get("總損益(元)", 0),
        "最大回撤(元)": m_.get("最大回撤(元)", 0),
        "策略標準最大回撤率(%)": m_.get("策略標準最大回撤率(%)", ""),
        "市場期間漲跌幅(%)": m_.get("市場期間漲跌幅(%)", ""),
        "市場最大回撤率(%)": m_.get("市場最大回撤率(%)", ""),
        "相對市場回撤倍數": m_.get("相對市場回撤倍數", ""),
        "獲利交易加權保留率(%)": m_.get("獲利交易加權保留率(%)", ""),
        "曾有浮盈交易筆數": m_.get("曾有浮盈交易筆數", ""),
        "浮盈轉虧率(%)": m_.get("浮盈轉虧率(%)", ""),
        "獲利因子": m_.get("獲利因子", ""),
        "期望值(元/筆)": m_.get("期望值(元/筆)", ""),
        "交易次數": m_.get("交易次數", 0),
        "勝率(%)": m_.get("勝率(%)", 0),
        "最大連續虧損(次)": m_.get("最大連續虧損(次)", ""),
        "平均持倉K棒數": m_.get("平均持倉K棒數", ""),
        "資金持續未創新高交易天數": m_.get("資金持續未創新高交易天數", ""),
        "年化報酬率(%)": m_.get("年化報酬率(%)", ""),
        "總報酬率(%)": m_.get("總報酬率(%)", 0),
        "是否曾發生斷頭": m_.get("是否曾發生斷頭", "否"),
        "斷頭次數": m_.get("斷頭次數", 0),
        "第一次斷頭日期": m_.get("第一次斷頭日期", "無"),
    }


def _run_batch_core_for_period(text: str, period_start, period_end,
                               progress=None, progress_prefix: str = "批次回測") -> dict:
    """執行同一批策略在指定期間的回測，供一般批次與樣本內外驗證共用。"""
    try:
        batch_name, items = parse_strategy_batch(text)
    except Exception as e:  # noqa: BLE001
        st.error(f"批次策略 JSON 讀取失敗：{e}")
        return None

    period_start = pd.to_datetime(period_start).date()
    period_end = pd.to_datetime(period_end).date()
    mask = (cont["datetime"].dt.date >= period_start) & (cont["datetime"].dt.date <= period_end)
    data = cont[mask].reset_index(drop=True)
    if len(data) < 30:
        st.error(f"{progress_prefix}區間資料不足 30 根 K 棒，請放寬區間。")
        return None

    local_safety = calculate_mtx_safety_settings(cont, period_end, point_value) if symbol == "MTX" else copy.deepcopy(safety_info)
    cost = CostModel(
        point_value=point_value, fee=fee,
        slippage_points=slippage, tax_rate=tax_rate,
        use_margin_call_check=(symbol == "MTX"),
        safety_buffer_amount=float(local_safety.get("safety_buffer_amount", 0.0)),
        original_margin_amount=float(local_safety.get("original_margin", SYMBOLS[symbol]["margin_reference"])),
    )
    results = []
    rows = []
    own_progress = False
    if progress is None:
        progress = st.progress(0, text=f"{progress_prefix}準備中...")
        own_progress = True
    for idx, (name, cfg) in enumerate(items, start=1):
        progress.progress((idx - 1) / len(items), text=f"{progress_prefix}：{idx}/{len(items)} {name}")
        params = params_from_config(cfg)
        if not getattr(params, "ma_periods", None):
            params.ma_periods = ma_periods
        sig = run_strategy_config(data, cfg, params)
        trades_, equity_ = run_backtest(sig, cost, params)
        m_ = compute_metrics(trades_, equity_,
                             margin_reference=SYMBOLS[symbol]["margin_reference"],
                             quantity=cost.quantity, initial_capital=initial_capital,
                             market_data=data)
        tzh_ = zh_trades(trades_)
        row = _batch_compare_row(idx, name, m_)
        rows.append(row)
        results.append({
            "idx": idx, "name": name, "cfg": cfg, "params": params,
            "sig": sig, "trades": trades_, "trades_zh": tzh_, "equity": equity_,
            "metrics": m_, "yearly": yearly_stats(trades_, equity_), "row": row,
        })
    progress.progress(1.0, text=f"{progress_prefix}完成")
    if own_progress:
        progress.empty()
    compare = _sort_batch_compare(pd.DataFrame(rows))
    batch_hash = hashlib.md5((settings_hash() + text + str(period_start) + str(period_end)).encode()).hexdigest()
    return {
        "batch_name": batch_name, "results": results, "compare": compare,
        "cost": cost, "initial_capital": initial_capital, "symbol": symbol,
        "d_start": period_start, "d_end": period_end, "n_bars": len(data),
        "hash": batch_hash, "safety_info": copy.deepcopy(local_safety),
    }


def execute_batch_backtest():
    """批次回測（v0.4.9：支援前期行情、後期牛市、目前畫面期間三種單段模式）。"""
    text = st.session_state.get("batch_strategy_json_text", "")
    override = st.session_state.pop("batch_period_override", None)
    if override:
        label, start_text, end_text = override
        run_start = max(dmin, pd.to_datetime(start_text).date())
        run_end = dmax if end_text is None else min(dmax, pd.to_datetime(end_text).date())
        if run_start > run_end:
            st.error(f"{label} 沒有可用資料區間。")
            return
        batch = _run_batch_core_for_period(text, run_start, run_end, progress_prefix=label)
    else:
        batch = _run_batch_core_for_period(text, d_start, d_end, progress_prefix="目前畫面期間批次回測")
    if batch is None:
        return
    st.session_state["batch_bt"] = batch


def _to_float(v, default=0.0):
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except Exception:  # noqa: BLE001
        return default


def _sample_judgement(in_rr: float, out_rr: float, out_pnl: float) -> str:
    if out_pnl < 0:
        return "後期牛市失效"
    if in_rr >= 2.0 and out_rr >= 1.0:
        return "前後期皆穩定"
    if in_rr < 1.5 and out_rr >= 2.0:
        return "前期普通，牛市強勢"
    if in_rr >= 2.0 and out_rr < 1.0:
        return "前期穩定，後期失效"
    if out_rr >= 1.0:
        return "可觀察"
    return "偏弱"


def build_in_out_compare(sample_in: dict, sample_out: dict) -> pd.DataFrame:
    """產出樣本內 vs 樣本外對照表。"""
    in_df = sample_in["compare"].copy()
    out_df = sample_out["compare"].copy()
    rows = []
    for _, in_row in in_df.iterrows():
        idx = in_row.get("策略編號")
        name = in_row.get("策略名稱")
        out_match = out_df[(out_df["策略編號"] == idx) & (out_df["策略名稱"] == name)]
        if out_match.empty:
            out_match = out_df[out_df["策略編號"] == idx]
        if out_match.empty:
            continue
        out_row = out_match.iloc[0]
        in_rr = _to_float(in_row.get("報酬回撤比"))
        out_rr = _to_float(out_row.get("報酬回撤比"))
        in_pnl = _to_float(in_row.get("總損益(元)"))
        out_pnl = _to_float(out_row.get("總損益(元)"))
        in_dd = _to_float(in_row.get("最大回撤(元)"))
        out_dd = _to_float(out_row.get("最大回撤(元)"))
        ratio = round(out_rr / in_rr, 2) if in_rr not in (0, 0.0) else ""
        rows.append({
            "策略編號": idx,
            "策略名稱": name,
            "前期行情報酬回撤比": in_rr,
            "後期牛市報酬回撤比": out_rr,
            "後期/前期比": ratio,
            "前期行情總損益(元)": in_pnl,
            "後期牛市總損益(元)": out_pnl,
            "前期行情最大回撤(元)": in_dd,
            "後期牛市最大回撤(元)": out_dd,
            "後期牛市獲利因子": _to_float(out_row.get("獲利因子")),
            "後期牛市期望值(元/筆)": _to_float(out_row.get("期望值(元/筆)")),
            "後期牛市交易次數": int(_to_float(out_row.get("交易次數"))),
            "行情適應判讀": _sample_judgement(in_rr, out_rr, out_pnl),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    priority = {
        "前後期皆穩定": 1,
        "前期普通，牛市強勢": 2,
        "可觀察": 3,
        "前期穩定，後期失效": 4,
        "偏弱": 5,
        "後期牛市失效": 6,
    }
    df["__priority"] = df["行情適應判讀"].map(priority).fillna(9)
    df = df.sort_values(
        by=["__priority", "後期牛市報酬回撤比", "前期行情報酬回撤比", "後期牛市總損益(元)"],
        ascending=[True, False, False, False],
        kind="mergesort",
    ).drop(columns=["__priority"]).reset_index(drop=True)
    return df


def execute_sample_validation():
    """v0.4.9：同一批策略一次產出前期行情／後期牛市／對照表。"""
    text = st.session_state.get("batch_strategy_json_text", "")
    in_start = max(dmin, pd.to_datetime("2015-01-01").date())
    in_end = min(dmax, pd.to_datetime("2023-12-31").date())
    out_start = max(dmin, pd.to_datetime("2024-01-01").date())
    out_end = dmax
    if out_start > out_end:
        st.error("目前資料沒有 2024 以後的後期牛市區間。")
        return
    if in_start > in_end:
        st.error("目前資料沒有 2015～2023 的前期行情區間。")
        return

    progress = st.progress(0, text="前後期行情對照準備中...")
    sample_in = _run_batch_core_for_period(text, in_start, in_end, progress=progress,
                                           progress_prefix="前期行情 2015～2023")
    if sample_in is None:
        return
    sample_out = _run_batch_core_for_period(text, out_start, out_end, progress=progress,
                                            progress_prefix="後期牛市 2024～資料末日")
    if sample_out is None:
        return
    compare = build_in_out_compare(sample_in, sample_out)
    progress.progress(1.0, text="前後期行情對照完成")
    validation_hash = hashlib.md5((settings_hash() + text + str(in_start) + str(out_end) + "validation").encode()).hexdigest()
    st.session_state["sample_validation_bt"] = {
        "batch_name": sample_in.get("batch_name", "MTX批次回測"),
        "sample_in": sample_in,
        "sample_out": sample_out,
        "compare": compare,
        "hash": validation_hash,
        "in_start": in_start,
        "in_end": in_end,
        "out_start": out_start,
        "out_end": out_end,
    }

def build_batch_folder_name(batch: dict) -> str:
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    name = _safe_filename_part(batch.get("batch_name", "MTX批次回測"))
    return f"MTX_批次回測_{ts}_{name}"


def build_single_strategy_summary_md(batch: dict, result: dict) -> str:
    m_ = result["metrics"]
    lines = [
        "---",
        "type: MTX批次回測單一策略",
        f"batch: {batch.get('batch_name', '')}",
        f"strategy_index: {result['idx']}",
        f"strategy_name: {result['name']}",
        "tags:",
        "  - MTX",
        "  - 批次回測",
        "---",
        "",
        f"# 策略 {result['idx']:02d}｜{result['name']}",
        "",
        f"- 回測期間：{batch.get('d_start')} ～ {batch.get('d_end')}",
        f"- 總損益：{m_.get('總損益(元)', 0)} 元",
        f"- 總報酬率：{m_.get('總報酬率(%)', 0)}%",
        f"- 最大回撤：{m_.get('最大回撤(元)', 0)} 元",
        f"- 報酬回撤比：{m_.get('報酬回撤比', '')}",
        f"- 獲利因子：{m_.get('獲利因子', '')}",
        f"- 期望值：{m_.get('期望值(元/筆)', '')} 元/筆",
        f"- 交易次數：{m_.get('交易次數', 0)} 筆",
        f"- 勝率：{m_.get('勝率(%)', 0)}%",
        f"- 是否曾發生斷頭：{m_.get('是否曾發生斷頭', '否')}",
        f"- 斷頭次數：{m_.get('斷頭次數', 0)} 次",
        "",
        "## 年度分解",
        "",
        dataframe_to_markdown_table(result.get("yearly")),
        "",
        "## 相關檔案",
        "- [交易明細 trades.csv](trades.csv)",
        "- [績效統計 metrics.csv](metrics.csv)",
        "- [資金曲線 equity_curve.csv](equity_curve.csv)",
        "- [年度分解 yearly_stats.csv](yearly_stats.csv)",
        "- [策略設定 strategy_config.json](strategy_config.json)",
    ]
    return "\n".join(lines) + "\n"


def dataframe_to_markdown_table(df: pd.DataFrame) -> str:
    """不依賴 tabulate，輸出 Obsidian 可讀的 Markdown 表格。"""
    if df is None or df.empty:
        return "（無資料）"
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join(["---" for _ in cols]) + "|"]
    for _, row in df.iterrows():
        vals = []
        for c in df.columns:
            v = row[c]
            if pd.isna(v):
                vals.append("")
            else:
                vals.append(str(v).replace("|", "／"))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_batch_overview_md(batch: dict, folder_name: str) -> str:
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    compare = batch["compare"].copy()
    lines = [
        "---",
        "type: MTX批次回測",
        f"created: {now}",
        f"batch_name: {batch.get('batch_name', '')}",
        "tags:",
        "  - MTX",
        "  - 批次回測",
        "  - 策略比較",
        "---",
        "",
        f"# MTX 批次回測｜{batch.get('batch_name', '')}",
        "",
        f"- 建立時間：{now}",
        f"- 回測期間：{batch.get('d_start')} ～ {batch.get('d_end')}",
        f"- 策略數量：{len(batch.get('results', []))} / {BATCH_MAX_STRATEGIES}",
        f"- 批次資料夾：`{folder_name}`",
        "",
        "## 批次比較表",
        "",
        dataframe_to_markdown_table(compare),
        "",
        "## 策略連結",
    ]
    for r in batch.get("results", []):
        sub = f"{r['idx']:02d}_{_safe_filename_part(r['name'])}"
        lines.append(f"- [[{sub}/00_策略回測摘要|策略 {r['idx']:02d}｜{r['name']}]]")
    lines += [
        "",
        "## 檢討欄位",
        "- 本批次最值得保留的策略：待檢討",
        "- 本批次最需要淘汰的策略：待檢討",
        "- 下一批策略調整方向：待檢討",
    ]
    return "\n".join(lines) + "\n"


def _equity_out_for_batch(result: dict, initial_capital_: float) -> pd.DataFrame:
    eq = result["equity"].copy()
    if eq.empty:
        return pd.DataFrame(columns=["日期", "資金", "回撤"])
    eq["資金"] = initial_capital_ + eq["equity"]
    eq["回撤"] = eq["資金"] - eq["資金"].cummax()
    return pd.DataFrame({
        "日期": pd.to_datetime(eq["datetime"]).dt.strftime("%Y/%m/%d"),
        "資金": eq["資金"].round(0),
        "回撤": eq["回撤"].round(0),
    })


def build_batch_zip_bytes(batch: dict) -> bytes:
    folder_name = build_batch_folder_name(batch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_批次回測總覽.md", build_batch_overview_md(batch, folder_name))
        z.writestr("batch_comparison.csv", batch["compare"].to_csv(index=False).encode("utf-8-sig"))
        for r in batch.get("results", []):
            sub = f"{r['idx']:02d}_{_safe_filename_part(r['name'])}"
            z.writestr(f"{sub}/00_策略回測摘要.md", build_single_strategy_summary_md(batch, r))
            z.writestr(f"{sub}/trades.csv", r["trades_zh"].to_csv(index=False).encode("utf-8-sig"))
            z.writestr(f"{sub}/metrics.csv", metrics_to_df(r["metrics"]).to_csv(index=False).encode("utf-8-sig"))
            z.writestr(f"{sub}/equity_curve.csv", _equity_out_for_batch(r, batch["initial_capital"]).to_csv(index=False).encode("utf-8-sig"))
            if r.get("yearly") is not None:
                z.writestr(f"{sub}/yearly_stats.csv", r["yearly"].to_csv(index=False).encode("utf-8-sig"))
            z.writestr(f"{sub}/strategy_config.json", json.dumps(r["cfg"], ensure_ascii=False, indent=2))
    return buf.getvalue()



def build_sample_validation_folder_name(validation: dict) -> str:
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    name = _safe_filename_part(validation.get("batch_name", "MTX批次回測"))
    return f"MTX_前後期行情對照_{ts}_{name}"


def build_sample_validation_overview_md(validation: dict, folder_name: str) -> str:
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "---",
        "type: MTX前後期行情對照",
        f"created: {now}",
        f"batch_name: {validation.get('batch_name', '')}",
        "tags:",
        "  - MTX",
        "  - 批次回測",
        "  - 前後期行情對照",
        "---",
        "",
        f"# MTX 前後期行情對照｜{validation.get('batch_name', '')}",
        "",
        f"- 建立時間：{now}",
        f"- 前期行情期間：{validation.get('in_start')} ～ {validation.get('in_end')}（一般行情／盤整震盪期）",
        f"- 後期牛市期間：{validation.get('out_start')} ～ {validation.get('out_end')}（2024 之後強趨勢行情）",
        f"- 資料夾：`{folder_name}`",
        "",
        "## 前期行情 vs 後期牛市對照表",
        "",
        dataframe_to_markdown_table(validation.get("compare")),
        "",
        "## 相關檔案",
        "- [前期行情批次比較 phase_2015_2023/batch_comparison.csv](phase_2015_2023/batch_comparison.csv)",
        "- [後期牛市批次比較 bull_2024_plus/batch_comparison.csv](bull_2024_plus/batch_comparison.csv)",
        "- [前後期行情對照 market_phase_comparison.csv](market_phase_comparison.csv)",
        "",
        "## 檢討欄位",
        "- 前後期皆穩定：待檢討",
        "- 前期普通、牛市強勢：待檢討",
        "- 前期穩定、後期失效／淘汰：待檢討",
        "- 下一批策略調整方向：待檢討",
    ]
    return "\n".join(lines) + "\n"


def _write_batch_into_zip(z: zipfile.ZipFile, prefix: str, batch: dict) -> None:
    folder_name = prefix.rstrip("/")
    z.writestr(f"{prefix}/00_批次回測總覽.md", build_batch_overview_md(batch, folder_name))
    z.writestr(f"{prefix}/batch_comparison.csv", batch["compare"].to_csv(index=False).encode("utf-8-sig"))
    for r in batch.get("results", []):
        sub = f"{prefix}/{r['idx']:02d}_{_safe_filename_part(r['name'])}"
        z.writestr(f"{sub}/00_策略回測摘要.md", build_single_strategy_summary_md(batch, r))
        z.writestr(f"{sub}/trades.csv", r["trades_zh"].to_csv(index=False).encode("utf-8-sig"))
        z.writestr(f"{sub}/metrics.csv", metrics_to_df(r["metrics"]).to_csv(index=False).encode("utf-8-sig"))
        z.writestr(f"{sub}/equity_curve.csv", _equity_out_for_batch(r, batch["initial_capital"]).to_csv(index=False).encode("utf-8-sig"))
        if r.get("yearly") is not None:
            z.writestr(f"{sub}/yearly_stats.csv", r["yearly"].to_csv(index=False).encode("utf-8-sig"))
        z.writestr(f"{sub}/strategy_config.json", json.dumps(r["cfg"], ensure_ascii=False, indent=2))


def build_sample_validation_zip_bytes(validation: dict) -> bytes:
    folder_name = build_sample_validation_folder_name(validation)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_前後期行情對照總覽.md", build_sample_validation_overview_md(validation, folder_name))
        z.writestr("market_phase_comparison.csv", validation["compare"].to_csv(index=False).encode("utf-8-sig"))
        _write_batch_into_zip(z, "phase_2015_2023", validation["sample_in"])
        _write_batch_into_zip(z, "bull_2024_plus", validation["sample_out"])
    return buf.getvalue()


def build_sample_validation_zip_filename(validation: dict) -> str:
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    name = _safe_filename_part(validation.get("batch_name", "MTX批次回測"))
    return f"MTX_前後期行情對照_{ts}_{name}.zip"


def save_batch_to_obsidian(batch: dict, data: bytes, zip_name: str) -> tuple:
    try:
        os.makedirs(DEFAULT_RECORD_DIR, exist_ok=True)
        folder_name = os.path.splitext(_safe_filename_part(zip_name))[0]
        folder = os.path.join(DEFAULT_RECORD_DIR, folder_name)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, zip_name), "wb") as f:
            f.write(data)
        with zipfile.ZipFile(io.BytesIO(data), "r") as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                out_path = os.path.join(folder, info.filename)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(z.read(info.filename))
        return folder, ""
    except Exception as e:  # noqa: BLE001
        return "", str(e)


def build_batch_zip_filename(batch: dict) -> str:
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    name = _safe_filename_part(batch.get("batch_name", "MTX批次回測"))
    return f"MTX_批次回測_{ts}_{name}.zip"


# ================= 主畫面 =================
st.markdown('<div class="strategy-banner">目前內建可選用策略：'
            '<b>MACD＋布林線＋吊燈出場＋KD＋RSI</b>（單一內建策略，'
            '進出場條件可在「策略設定面板」自由組合）</div>', unsafe_allow_html=True)

bc1, bc2, bc3 = st.columns([1.2, 1.2, 2.6])
if bc1.button("🎮 策略設定面板 ", type="primary", use_container_width=True):
    strategy_dialog()
main_run = bc2.button("▶ 開始回測", type="primary", use_container_width=True,
                      help="以目前的策略與資料設定執行回測")
with bc3:
    st.caption("先在「策略設定面板」勾選進出場條件，再按「開始回測」。"
               "調整欄位不會自動重跑，結果只在按下開始回測後更新。")

if mobile_mode:
    st.markdown("""
    <div class="mobile-quick-card">
      <b>📱 手機快速操作</b><br>
      建議手機先使用「雲端策略／內建策略」跑前後期行情對照；若已設定 Google Drive API，結果會自動上傳。
    </div>
    """, unsafe_allow_html=True)
    mq1, mq2 = st.columns(2)
    if mq1.button(
        "▶ 開始雲端批次回測",
        type="primary",
        use_container_width=True,
        key="mobile_cloud_validation_btn",
        disabled=not bool(selected_cloud_file),
    ):
        try:
            # 手機版必須與桌機版共用目前在雲端策略投放箱選取的檔案；
            # 不再讀取寫死的 DEFAULT_CLOUD_BATCH_JSON_URL，也不回退內建雲端失效測試檔。
            raw = load_batch_json_from_drive_file(selected_cloud_file["id"])
            loaded_from = (
                "gdrive:"
                + selected_cloud_file["id"]
                + ":"
                + selected_cloud_file.get("modifiedTime", "")
            )
            set_batch_json_and_queue(
                raw,
                loaded_from,
                "前後期行情對照：2015～2023 一般行情 vs 2024～資料末日牛市行情",
                selected_cloud_file.get("name", ""),
            )
        except Exception as e:  # noqa: BLE001
            st.error(f"手機快速批次讀取失敗：{e}")
    loaded_strategy_name = st.session_state.get("batch_loaded_display_name", "尚未載入策略檔")
    with mq2:
        st.markdown(
            f"""<div style="height:2.65rem;display:flex;align-items:center;justify-content:center;
            padding:0 .65rem;border:1px solid #c6d1dc;border-radius:.5rem;background:#fff;
            color:#243447;font-weight:600;text-align:center;overflow:hidden;white-space:nowrap;
            text-overflow:ellipsis;" title="{loaded_strategy_name}">
            📄 {loaded_strategy_name}</div>""",
            unsafe_allow_html=True,
        )
    with st.expander("手機上傳策略 JSON", expanded=False):
        mobile_up = st.file_uploader("上傳策略 JSON 後直接跑前後期行情對照", type=["json"], key="mobile_batch_json_uploader")
        if mobile_up is not None and st.button("▶ 跑上傳策略", use_container_width=True, key="mobile_uploaded_run_btn"):
            try:
                raw = mobile_up.read()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8-sig")
                set_batch_json_and_queue(raw, "mobile_upload:" + mobile_up.name + str(mobile_up.size), "前後期行情對照：2015～2023 一般行情 vs 2024～資料末日牛市行情", mobile_up.name)
            except Exception as e:  # noqa: BLE001
                st.error(f"手機上傳策略讀取失敗：{e}")

if main_run:
    st.session_state["run_request"] = True
if st.session_state.pop("run_request", False):
    execute_backtest()
if st.session_state.pop("batch_run_request", False):
    execute_batch_backtest()
if st.session_state.pop("batch_validation_request", False):
    execute_sample_validation()

# 目前策略設定摘要
st.markdown(zh_strategy_summary(st.session_state["strat"]), unsafe_allow_html=True)

sample_validation_bt = st.session_state.get("sample_validation_bt")
if sample_validation_bt is not None:
    st.markdown("## 前後期行情對照結果")
    st.caption(f"前期行情：{sample_validation_bt['in_start']} ～ {sample_validation_bt['in_end']}｜後期牛市：{sample_validation_bt['out_start']} ～ {sample_validation_bt['out_end']}")
    st.dataframe(sample_validation_bt["compare"], hide_index=True, use_container_width=True)
    validation_zip_bytes = build_sample_validation_zip_bytes(sample_validation_bt)
    if st.session_state.get("sample_validation_saved_hash") != sample_validation_bt["hash"]:
        validation_zip_name = build_sample_validation_zip_filename(sample_validation_bt)
        if cloud_operation_mode:
            validation_folder_name = os.path.splitext(_safe_filename_part(validation_zip_name))[0]
            validation_saved_path, validation_save_error = upload_result_zip_to_google_drive(
                validation_zip_bytes, validation_zip_name, validation_folder_name
            )
        else:
            validation_saved_path, validation_save_error = save_batch_to_obsidian(sample_validation_bt, validation_zip_bytes, validation_zip_name)
        st.session_state["sample_validation_saved_hash"] = sample_validation_bt["hash"]
        st.session_state["sample_validation_zip_name"] = validation_zip_name
        st.session_state["sample_validation_saved_path"] = validation_saved_path
        st.session_state["sample_validation_save_error"] = validation_save_error
    else:
        validation_zip_name = st.session_state.get("sample_validation_zip_name", "MTX_前後期行情對照.zip")
        validation_saved_path = st.session_state.get("sample_validation_saved_path", "")
        validation_save_error = st.session_state.get("sample_validation_save_error", "")
    if validation_saved_path:
        st.success(f"已自動上傳前後期行情對照結果到 Google Drive：{validation_saved_path}" if cloud_operation_mode else f"已自動建立 Obsidian 前後期行情對照資料夾：{validation_saved_path}")
    elif validation_save_error and not cloud_operation_mode:
        st.warning(f"前後期行情對照 ZIP 下載按鈕仍可使用，但自動保存到 {DEFAULT_RECORD_DIR} 失敗：{validation_save_error}")
    elif cloud_operation_mode:
        st.warning(f"雲端作業模式：Google Drive 自動上傳尚未完成，原因：{validation_save_error}")
    validation_overview_text = build_sample_validation_overview_md(
        sample_validation_bt, os.path.splitext(_safe_filename_part(validation_zip_name))[0]
    )
    with st.expander("複製給 AI 的前後期行情對照總覽", expanded=mobile_mode):
        st.text_area("總覽 Markdown", validation_overview_text, height=(260 if mobile_mode else 360), key="validation_overview_copy")
    st.download_button("下載前後期行情對照 ZIP", validation_zip_bytes,
                       file_name=validation_zip_name, use_container_width=True)

batch_bt = st.session_state.get("batch_bt")
if batch_bt is not None:
    st.markdown("## 批次回測結果")
    st.dataframe(batch_bt["compare"], hide_index=True, use_container_width=True)
    batch_zip_bytes = build_batch_zip_bytes(batch_bt)
    if st.session_state.get("batch_saved_hash") != batch_bt["hash"]:
        batch_zip_name = build_batch_zip_filename(batch_bt)
        if cloud_operation_mode:
            batch_folder_name = os.path.splitext(_safe_filename_part(batch_zip_name))[0]
            batch_saved_path, batch_save_error = upload_result_zip_to_google_drive(
                batch_zip_bytes, batch_zip_name, batch_folder_name
            )
        else:
            batch_saved_path, batch_save_error = save_batch_to_obsidian(batch_bt, batch_zip_bytes, batch_zip_name)
        st.session_state["batch_saved_hash"] = batch_bt["hash"]
        st.session_state["batch_zip_name"] = batch_zip_name
        st.session_state["batch_saved_path"] = batch_saved_path
        st.session_state["batch_save_error"] = batch_save_error
    else:
        batch_zip_name = st.session_state.get("batch_zip_name", "MTX_批次回測.zip")
        batch_saved_path = st.session_state.get("batch_saved_path", "")
        batch_save_error = st.session_state.get("batch_save_error", "")
    if batch_saved_path:
        st.success(f"已自動上傳批次回測結果到 Google Drive：{batch_saved_path}" if cloud_operation_mode else f"已自動建立 Obsidian 批次回測資料夾：{batch_saved_path}")
    elif batch_save_error and not cloud_operation_mode:
        st.warning(f"批次回測 ZIP 下載按鈕仍可使用，但自動保存到 {DEFAULT_RECORD_DIR} 失敗：{batch_save_error}")
    elif cloud_operation_mode:
        st.warning(f"雲端作業模式：Google Drive 自動上傳尚未完成，原因：{batch_save_error}")
    batch_overview_text = build_batch_overview_md(batch_bt, os.path.splitext(_safe_filename_part(batch_zip_name))[0])
    with st.expander("複製給 AI 的批次回測總覽", expanded=mobile_mode):
        st.text_area("總覽 Markdown", batch_overview_text, height=(260 if mobile_mode else 360), key="batch_overview_copy")
    st.download_button("下載批次回測 ZIP", batch_zip_bytes,
                       file_name=batch_zip_name, use_container_width=True, type="primary")
    st.caption("批次回測不會改變目前單次回測結果；每組策略交易邏輯仍沿用既有 backtester。")

bt = st.session_state.get("bt")
if bt is None:
    st.info("尚未執行單次回測。桌機可按「▶ 開始回測」；手機建議使用上方「雲端前後期對照」。")
    st.stop()

if bt["hash"] != settings_hash():
    st.warning("⚠ 設定已變更，以下為【上次回測】的結果；按「▶ 開始回測」以套用新設定。")

# ---- 回測摘要卡片 ----
st.markdown(
    f"""
    <div class="summary-card">
      <div class="title">本次回測摘要</div>
      <div class="grid">
        <div class="item"><span class="label">商品</span>{bt['symbol']} {SYMBOLS[bt['symbol']]['name']}</div>
        <div class="item"><span class="label">策略</span>MACD＋布林線＋吊燈出場＋KD＋RSI</div>
        <div class="item"><span class="label">期間</span>{bt['d_start']} ～ {bt['d_end']}（{bt['n_bars']:,} 根 K 棒）</div>
        <div class="item"><span class="label">初始資金</span>{bt['initial_capital']:,.0f} 元</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

m = bt["m"]
trades, equity, sig = bt["trades"], bt["equity"], bt["sig"]
trades_zh = bt["trades_zh"]


def card(col, label, value, signed=False, suffix=""):
    cls = ""
    if signed and isinstance(value, (int, float)):
        cls = "pos" if value > 0 else ("neg" if value < 0 else "")
    if isinstance(value, float):
        value = f"{value:,.1f}"
    elif isinstance(value, int):
        value = f"{value:,}"
    col.markdown(f'<div class="metric-card"><div class="lbl">{label}</div>'
                 f'<div class="val {cls}">{value}{suffix}</div></div>',
                 unsafe_allow_html=True)


if trades.empty:
    st.warning("此條件組合沒有產生任何交易，請調整策略設定。")
else:
    cols = st.columns(6)
    card(cols[0], "總損益", m["總損益(元)"], signed=True, suffix=" 元")
    card(cols[1], "總報酬率", m.get("總報酬率(%)", 0.0), signed=True, suffix=" %")
    ann = m.get("年化報酬率(%)")
    card(cols[2], "年化報酬率", ann if ann is not None else "—",
         signed=isinstance(ann, (int, float)), suffix=" %" if ann is not None else "")
    card(cols[3], "最大回撤", m.get("最大回撤(元)", 0.0), signed=True, suffix=" 元")
    card(cols[4], "勝率", f"{m['勝率(%)']} %")
    card(cols[5], "交易次數", m["交易次數"])
    with st.expander("進階績效統計 ▼", expanded=False):
        adv_keys = ["總損益(點)", "最大回撤(%)", "策略標準最大回撤率(%)",
                    "市場期間漲跌幅(%)", "市場最大回撤率(%)", "相對市場回撤倍數",
                    "回撤相對市場漲跌幅倍數", "獲利交易加權保留率(%)",
                    "獲利交易中位保留率(%)", "曾有浮盈交易筆數",
                    "浮盈轉虧筆數", "浮盈轉虧率(%)",
                    "獲利因子", "期望值(元/筆)",
                    "平均獲利(元)", "平均虧損(元)", "平均損益(元)",
                    "獲利次數", "虧損次數", "最大獲利(元)", "最大虧損(元)",
                    "最大連續虧損(次)", "平均持倉K棒數", "資金持續未創新高交易天數"]
        adv = {("最大單筆獲利(元)" if k == "最大獲利(元)" else
                "最大單筆虧損(元)" if k == "最大虧損(元)" else k): m[k]
               for k in adv_keys if k in m}
        a_cols = st.columns(4)
        for i, (k, v) in enumerate(adv.items()):
            card(a_cols[i % 4], k, v)
            if i % 4 == 3 and i < len(adv) - 1:
                a_cols = st.columns(4)

st.markdown("")

# ---------------- 主圖 + 右側交易紀錄 ----------------
col_main, col_right = st.columns([3.4, 1])

with col_main:
    range_label = st.radio(
        "圖表顯示範圍（只重畫圖，不重新回測）",
        ["最近 60 個交易日", "最近 120 個交易日", "最近 240 個交易日",
         "全部期間", "自訂日期區間"],
        index=1, horizontal=True)
    if range_label == "自訂日期區間":
        cc1, cc2 = st.columns(2)
        vmin = sig["datetime"].min().date()
        vmax = sig["datetime"].max().date()
        with cc1:
            v_start = st.date_input("圖表起日", value=vmin, min_value=vmin, max_value=vmax)
        with cc2:
            v_end = st.date_input("圖表迄日", value=vmax, min_value=vmin, max_value=vmax)
        vmask = (sig["datetime"].dt.date >= v_start) & (sig["datetime"].dt.date <= v_end)
        view = sig[vmask].reset_index(drop=True)
    elif range_label == "全部期間":
        view = sig.reset_index(drop=True)
    else:
        n_show = int(range_label.replace("最近 ", "").replace(" 個交易日", ""))
        view = sig.tail(n_show).reset_index(drop=True)
    if view.empty:
        st.warning("此顯示區間沒有資料，請調整範圍。")
        st.stop()

    xs = view["datetime"].dt.strftime("%Y/%m/%d")
    x_set = set(xs)

    panel_defs = [("price", "")]
    if chart_options["show_volume"]:
        panel_defs.append(("volume", "成交量"))
    if chart_options["show_macd_panel"]:
        panel_defs.append(("macd", "MACD"))
    if chart_options["show_kd_panel"]:
        panel_defs.append(("kd", "KD"))
    row_map = {name: idx + 1 for idx, (name, _) in enumerate(panel_defs)}
    total_rows = len(panel_defs)
    base_heights = {"price": 0.58, "volume": 0.14, "macd": 0.14, "kd": 0.14}
    row_heights = [base_heights[name] for name, _ in panel_defs]
    total_height = 520 + 120 * (total_rows - 1)

    fig = make_subplots(rows=total_rows, cols=1, shared_xaxes=True,
                        row_heights=row_heights, vertical_spacing=0.035,
                        subplot_titles=[t for _, t in panel_defs])
    if chart_options["show_candlestick"]:
        fig.add_trace(go.Candlestick(
            x=xs, open=view["open"], high=view["high"], low=view["low"],
            close=view["close"], name="K線",
            increasing_line_color=UP_COLOR, increasing_fillcolor=UP_COLOR,
            decreasing_line_color=DOWN_COLOR, decreasing_fillcolor=DOWN_COLOR,
        ), row=row_map["price"], col=1)
    if chart_options["show_bollinger"]:
        for c_, nm, dash in [("bb_upper", "布林上軌", "dot"), ("bb_mid", "布林中線", "solid"),
                             ("bb_lower", "布林下軌", "dot")]:
            fig.add_trace(go.Scatter(x=xs, y=view[c_], name=nm, mode="lines",
                                     line=dict(width=1, dash=dash, color="#7f97ab")),
                          row=row_map["price"], col=1)
    if chart_options["show_ma"]:
        ma_colors = ["#e67e22", "#2980b9", "#8e44ad", "#16a085", "#c0392b", "#2c3e50"]
        for i, nper in enumerate(bt["params"].ma_periods):
            cname = f"sma_{nper}"
            if cname in view.columns:
                fig.add_trace(go.Scatter(x=xs, y=view[cname], name=f"均線{nper}",
                                         mode="lines",
                                         line=dict(width=1.2, color=ma_colors[i % len(ma_colors)])),
                              row=row_map["price"], col=1)
    if chart_options["show_chandelier_lines"]:
        fig.add_trace(go.Scatter(x=xs, y=view["chandelier_long"], name="吊燈線（多）",
                                 mode="lines", line=dict(width=1, dash="dash", color="#b03a2e")),
                      row=row_map["price"], col=1)
        fig.add_trace(go.Scatter(x=xs, y=view["chandelier_short"], name="吊燈線（空）",
                                 mode="lines", line=dict(width=1, dash="dash", color="#1e8449")),
                      row=row_map["price"], col=1)
    if chart_options["show_trade_markers"] and not trades.empty:
        pad = (view["high"].max() - view["low"].min()) * 0.03
        lows = dict(zip(xs, view["low"]))
        highs = dict(zip(xs, view["high"]))

        def marker(dates, sym, color, name, ys):
            pts = [(d, y) for d, y in zip(dates, ys) if d in x_set]
            if pts:
                fig.add_trace(go.Scatter(
                    x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers",
                    name=name, marker=dict(symbol=sym, size=12, color=color,
                                           line=dict(width=1, color="#333"))),
                    row=row_map["price"], col=1)

        t = trades.copy()
        t["entry_x"] = pd.to_datetime(t["entry_date"]).dt.strftime("%Y/%m/%d")
        t["exit_x"] = pd.to_datetime(t["exit_date"]).dt.strftime("%Y/%m/%d")
        tl, ts_ = t[t["direction"] == "long"], t[t["direction"] == "short"]
        marker(tl["entry_x"], "triangle-up", "#d64550", "多單進場",
               [lows.get(d, p) - pad for d, p in zip(tl["entry_x"], tl["entry_price"])])
        marker(ts_["entry_x"], "triangle-down", "#2f9e63", "空單進場",
               [highs.get(d, p) + pad for d, p in zip(ts_["entry_x"], ts_["entry_price"])])
        marker(tl["exit_x"], "triangle-down", "#f0b429", "多單出場",
               [highs.get(d, p) + pad for d, p in zip(tl["exit_x"], tl["exit_price"])])
        marker(ts_["exit_x"], "triangle-up", "#f0b429", "空單出場",
               [lows.get(d, p) - pad for d, p in zip(ts_["exit_x"], ts_["exit_price"])])
    if chart_options["show_volume"]:
        vol_colors = [UP_COLOR if c >= o else DOWN_COLOR
                      for c, o in zip(view["close"], view["open"])]
        fig.add_trace(go.Bar(x=xs, y=view["volume"], name="成交量",
                             marker_color=vol_colors), row=row_map["volume"], col=1)
        fig.add_trace(go.Scatter(x=xs, y=view["vol_ma"], name="成交量均線",
                                 line=dict(width=1, color="#34495e")), row=row_map["volume"], col=1)
    if chart_options["show_macd_panel"]:
        h_colors = [UP_COLOR if v >= 0 else DOWN_COLOR for v in view["macd_hist"].fillna(0)]
        fig.add_trace(go.Bar(x=xs, y=view["macd_hist"], name="MACD柱",
                             marker_color=h_colors), row=row_map["macd"], col=1)
        fig.add_trace(go.Scatter(x=xs, y=view["macd_dif"], name="DIF",
                                 line=dict(width=1, color="#2980b9")), row=row_map["macd"], col=1)
        fig.add_trace(go.Scatter(x=xs, y=view["macd_dea"], name="DEA",
                                 line=dict(width=1, color="#e67e22")), row=row_map["macd"], col=1)
    if chart_options["show_kd_panel"]:
        fig.add_trace(go.Scatter(x=xs, y=view["k"], name="K值",
                                 line=dict(width=1, color="#2980b9")), row=row_map["kd"], col=1)
        fig.add_trace(go.Scatter(x=xs, y=view["d"], name="D值",
                                 line=dict(width=1, color="#e67e22")), row=row_map["kd"], col=1)

    step = max(1, len(xs) // 10)
    fig.update_xaxes(type="category", tickvals=list(xs[::step]),
                     tickangle=-30, rangeslider_visible=False)
    fig.update_layout(height=total_height, margin=dict(l=10, r=10, t=25, b=10),
                      plot_bgcolor="#f7fafc", paper_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=1.02, font=dict(size=10)),
                      dragmode="pan", xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True,
                    config={"scrollZoom": True, "displaylogo": False,
                            "modeBarButtonsToRemove": ["lasso2d", "select2d"]})
    st.caption("圖表操作：滑鼠拖曳＝平移，滾輪＝縮放，雙擊＝還原。")

with col_right:
    st.markdown("##### 交易紀錄")
    if trades_zh.empty:
        st.info("無交易")
    else:
        st.dataframe(trades_zh[["出場日", "方向", "損益點數", "出場原因"]].iloc[::-1],
                     height=520, use_container_width=True, hide_index=True)
    with st.expander("參數摘要"):
        st.code(params_to_json_str(bt["params"]), language="json")

# ---------------- 資金曲線 ----------------
if chart_options["show_equity_curve"]:
    st.markdown("#### 資金曲線")
    st.caption("這張圖用來觀察策略資金是否穩定成長，以及中途最大回落幅度。"
               "資金 = 初始資金 + 累積損益（含未平倉評價）。")
    if not equity.empty:
        eq = equity.copy()
        eq["資金"] = bt["initial_capital"] + eq["equity"]
        eq["peak"] = eq["資金"].cummax()
        eq["回撤"] = eq["資金"] - eq["peak"]
        fig2 = make_subplots(rows=2, cols=1, shared_xaxes=True,
                             row_heights=[0.72, 0.28], vertical_spacing=0.05)
        fig2.add_trace(go.Scatter(x=eq["datetime"], y=eq["資金"], name="資金（元）",
                                  line=dict(color="#2c5f8a", width=1.6),
                                  fill="tozeroy", fillcolor="rgba(60,110,160,.18)"),
                       row=1, col=1)
        fig2.add_trace(go.Scatter(x=eq["datetime"], y=[bt["initial_capital"]] * len(eq),
                                  name="初始資金",
                                  line=dict(color="#888", width=1, dash="dot")),
                       row=1, col=1)
        fig2.add_trace(go.Scatter(x=eq["datetime"], y=eq["回撤"], name="回撤（元）",
                                  fill="tozeroy", line=dict(color="#b03a2e", width=1),
                                  fillcolor="rgba(176,58,46,.3)"), row=2, col=1)
        fig2.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10),
                           plot_bgcolor="#f7fafc", paper_bgcolor="rgba(0,0,0,0)",
                           legend=dict(orientation="h", y=1.05))
        st.plotly_chart(fig2, use_container_width=True, config={"displaylogo": False})

# ---------------- 績效統計 + 交易明細 ----------------
col_a, col_b = st.columns([1, 2])
with col_a:
    st.markdown("#### 績效統計")
    st.dataframe(metrics_to_df(m), hide_index=True, use_container_width=True, height=460)
with col_b:
    st.markdown("#### 交易明細")
    show_cols = [c for c in TRADE_DISPLAY_COLS if c in trades_zh.columns]
    st.dataframe(trades_zh[show_cols] if not trades_zh.empty else trades_zh,
                 hide_index=True, use_container_width=True, height=460)


# ---------------- AI 分析包 ----------------
def build_ai_pack() -> bytes:
    eq = equity.copy()
    eq["資金"] = bt["initial_capital"] + eq["equity"]
    eq["回撤"] = eq["資金"] - eq["資金"].cummax()
    eq_out = pd.DataFrame({
        "日期": pd.to_datetime(eq["datetime"]).dt.strftime("%Y/%m/%d"),
        "資金": eq["資金"].round(0), "回撤": eq["回撤"].round(0)})
    cfg = bt["cfg"]
    strat = bt["strat"]
    exits_zh = "、".join(n for k, n, _ in EXIT_DEFS if strat["params"].get(k)) or "無"
    for side, nm in (("exit_long", "多單條件出場"), ("exit_short", "空單條件出場")):
        c = _norm_combo(strat.get(side, {}))
        if combo_active(c):
            exits_zh += f"；{nm}＝" + " 且 ".join(
                COND_LABELS[k] for k in (c["must"] + c["ever"]))

    def combos_txt(combos):
        out = []
        for i, c in enumerate(combos):
            c = _norm_combo(c)
            if combo_active(c):
                txt = f"組合{'AB'[i]}＝" + " 且 ".join(COND_LABELS[k] for k in c["must"])
                if c["ever"]:
                    txt += f"（前提·{c['ever_n']}根內曾滿足：" +                            "、".join(COND_LABELS[k] for k in c["ever"]) + "）"
                if c["exclude"]:
                    txt += "（排除：" + "、".join(COND_LABELS[k] for k in c["exclude"]) + "）"
                out.append(txt)
        return "；".join(out) if out else "未設定"

    lines = ["# AI 回測分析摘要", ""]
    lines.append("## 一、基本資訊")
    lines.append(f"- 商品：{bt['symbol']} {SYMBOLS[bt['symbol']]['name']}")
    lines.append("- 策略：MACD＋布林線＋吊燈出場＋KD＋RSI（條件組合式）")
    lines.append(f"- 交易方向：{DIR_LABELS_INV[strat['direction']]}")
    lines.append(f"- 多單進場：{combos_txt(strat['combos_long'])}")
    lines.append(f"- 空單進場：{combos_txt(strat['combos_short'])}")
    lines.append(f"- 出場條件：{exits_zh}")
    lines.append(f"- 回測期間：{bt['d_start']} ～ {bt['d_end']}（{bt['n_bars']:,} 根日K）")
    lines.append(f"- 初始資金：{bt['initial_capital']:,.0f} 元（預設 1 口原始保證金）")
    si = bt.get("safety_info", {})
    if bt.get("symbol") == "MTX" and si:
        lines.append(f"- 原始保證金：{si.get('original_margin', 0):,.0f} 元")
        lines.append(f"- 安全緩衝金額：{si.get('safety_buffer_amount', 0):,.0f} 元")
        lines.append(f"- 安全資金：{si.get('safety_capital', 0):,.0f} 元")
        lines.append("- 斷頭定義：持倉期間反向浮動損失 >= 安全緩衝金額，視同斷頭強制平倉")
    lines.append(f"- 成本假設：每點 {bt['cost'].point_value:.0f} 元、"
                 f"單邊手續費 {bt['cost'].fee:.0f} 元、"
                 f"單邊滑價 {bt['cost'].slippage_points:.1f} 點、"
                 f"期交稅{'計入' if bt['cost'].tax_rate > 0 else '不計'}")
    lines.append("- 進場規則：訊號日收盤確認，次一交易日開盤價進場（無未來函數）")
    lines += ["", "## 二、策略參數", "```json",
              json.dumps(cfg, ensure_ascii=False, indent=2), "```", ""]
    lines.append("## 三、績效總覽")
    for k, v in m.items():
        lines.append(f"- {k}：{v}")
    lines += ["", "## 四、代表性交易"]
    if not trades.empty:
        cols = ["訊號日", "進場日", "出場日", "方向", "進場價", "出場價",
                "損益點數", "損益金額", "出場原因", "進場條件"]
        best = trades_zh.loc[trades["pnl_amount"].idxmax(), cols]
        worst = trades_zh.loc[trades["pnl_amount"].idxmin(), cols]
        lines.append("### 最大單筆獲利")
        lines += [f"- {c}：{best[c]}" for c in cols]
        lines.append("### 最大單筆虧損")
        lines += [f"- {c}：{worst[c]}" for c in cols]
        lines.append("### 最近 5 筆交易")
        lines.append("```")
        lines.append(trades_zh[cols[:-1]].tail(5).to_string(index=False))
        lines.append("```")
    lines += ["", "## 五、可以問 AI 的問題",
              "1. 這個策略的最大回撤發生在什麼行情環境？如何降低？",
              "2. 各進場組合的貢獻度如何？哪個組合值得保留或刪除？",
              "3. 依交易明細，哪一種出場原因貢獻最多虧損？參數該怎麼調？",
              "4. 換成只做多／只做空，績效會怎麼變化？",
              "5. 若把固定停損調大／調小，對連續虧損次數的影響？",
              "6. 以資金曲線判斷，這個策略適合的資金規模與口數是多少？", "",
              "（附件：trades.csv 交易明細、metrics.csv 績效統計、",
              "equity_curve.csv 資金曲線、strategy_config.json 策略設定）"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AI_回測分析摘要.md", "\n".join(lines))
        z.writestr("trades.csv", trades_zh.to_csv(index=False).encode("utf-8-sig"))
        z.writestr("metrics.csv", metrics_to_df(m).to_csv(index=False).encode("utf-8-sig"))
        z.writestr("equity_curve.csv", eq_out.to_csv(index=False).encode("utf-8-sig"))
        z.writestr("strategy_config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
    return buf.getvalue()


def build_ai_pack_filename() -> str:
    """建立可長期保存與排序的 AI 分析包檔名。"""
    symbol_part = _safe_filename_part(bt.get("symbol", "MTX"))
    d0 = _safe_filename_part(bt.get("d_start", ""))
    d1 = _safe_filename_part(bt.get("d_end", ""))
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    return f"{symbol_part}_回測分析包_{d0}_{d1}_{ts}.zip"


def build_obsidian_record_folder_name(zip_file_name: str) -> str:
    """用 ZIP 檔名建立同名但更適合 Obsidian 排列的回測紀錄資料夾。"""
    stem = os.path.splitext(_safe_filename_part(zip_file_name))[0]
    stem = stem.replace("回測分析包", "回測紀錄")
    return stem or "MTX_回測紀錄"


def _md_metric(key: str, default: str = "") -> str:
    """從績效 dict 取值並轉為適合 Markdown 顯示的字串。"""
    v = m.get(key, default) if isinstance(m, dict) else default
    if v is None:
        return ""
    return str(v)


def _trade_value(row, col: str) -> str:
    try:
        v = row[col]
    except Exception:  # noqa: BLE001
        return ""
    if pd.isna(v):
        return ""
    return str(v)


def _exit_reason_summary_md() -> list:
    """回傳出場原因統計 Markdown 行。"""
    if trades_zh is None or trades_zh.empty or "出場原因" not in trades_zh.columns:
        return ["- 無交易資料"]
    cnt = trades_zh["出場原因"].value_counts()
    return [f"- {idx}：{int(val)} 筆" for idx, val in cnt.items()]


def build_obsidian_overview_md(record_folder_name: str, zip_file_name: str) -> str:
    """建立 Obsidian 主要閱讀檔：00_回測總覽.md。"""
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    symbol = bt.get("symbol", "MTX")
    symbol_name = SYMBOLS.get(symbol, {}).get("name", "")
    period = f"{bt.get('d_start', '')} ～ {bt.get('d_end', '')}"
    title = f"MTX 回測紀錄｜{now}"

    lines = [
        "---",
        "type: MTX回測紀錄",
        f"created: {now}",
        f"symbol: {symbol}",
        f"period: {period}",
        "status: 待檢討",
        "tags:",
        "  - MTX",
        "  - 回測",
        "  - 策略檢討",
        "---",
        "",
        f"# {title}",
        "",
        "> 這份檔案是給 Obsidian 直接閱讀的主檔。ZIP 是備份包；檢討時請優先看本檔與 `AI_回測分析摘要.md`。",
        "",
        "## 1. 基本資訊",
        f"- 商品：{symbol} {symbol_name}".rstrip(),
        f"- 回測期間：{period}",
        f"- 日K根數：{bt.get('n_bars', '')}",
        f"- 初始資金：{bt.get('initial_capital', 0):,.0f} 元",
        f"- 回測紀錄資料夾：`{record_folder_name}`",
        "",
        "## 2. 核心績效",
        f"- 總損益：{_md_metric('總損益(元)')} 元 / {_md_metric('總損益(點)')} 點",
        f"- 總報酬率：{_md_metric('總報酬率(%)')}%",
        f"- 年化報酬率：{_md_metric('年化報酬率(%)')}%",
        f"- 最大回撤：{_md_metric('最大回撤(元)')} 元 / {_md_metric('最大回撤(%)')}%",
        f"- 交易次數：{_md_metric('交易次數')} 筆",
        f"- 勝率：{_md_metric('勝率(%)')}%",
        f"- 獲利因子：{_md_metric('獲利因子')}",
        f"- 最大連續虧損：{_md_metric('最大連續虧損(次)')} 次",
        "",
        "## 3. 斷頭強制平倉檢查",
        f"- 是否曾發生斷頭：{_md_metric('是否曾發生斷頭')}",
        f"- 斷頭次數：{_md_metric('斷頭次數')} 次",
        f"- 第一次斷頭日期：{_md_metric('第一次斷頭日期')}",
        f"- 歷史最低所需安全資金：{_md_metric('歷史最低所需安全資金')} 元",
        "",
        "## 4. 出場原因統計",
    ]
    lines.extend(_exit_reason_summary_md())

    lines += [
        "",
        "## 5. 本次先填檢討結論",
        "- 這次結果是否值得保留：待檢討",
        "- 最大問題：待檢討",
        "- 下一次要測什麼：待檢討",
        "- 要不要列入候選策略：待檢討",
        "",
        "## 6. 代表性交易",
    ]

    if trades is not None and not trades.empty:
        cols = ["訊號日", "進場日", "出場日", "方向", "進場價", "出場價", "損益點數", "損益金額", "出場原因"]
        best = trades_zh.loc[trades["pnl_amount"].idxmax()] if "pnl_amount" in trades.columns else None
        worst = trades_zh.loc[trades["pnl_amount"].idxmin()] if "pnl_amount" in trades.columns else None
        if best is not None:
            lines.append("### 最大單筆獲利")
            lines.append("| 欄位 | 內容 |")
            lines.append("|---|---|")
            for c in cols:
                lines.append(f"| {c} | {_trade_value(best, c)} |")
        if worst is not None:
            lines.append("")
            lines.append("### 最大單筆虧損")
            lines.append("| 欄位 | 內容 |")
            lines.append("|---|---|")
            for c in cols:
                lines.append(f"| {c} | {_trade_value(worst, c)} |")
    else:
        lines.append("- 無交易資料")

    lines += [
        "",
        "## 7. 相關檔案",
        "- [[AI_回測分析摘要]]：較完整的 AI 分析摘要",
        "- [交易明細 trades.csv](trades.csv)",
        "- [績效統計 metrics.csv](metrics.csv)",
        "- [資金曲線 equity_curve.csv](equity_curve.csv)",
        "- [策略設定 strategy_config.json](strategy_config.json)",
        f"- [完整 ZIP 備份]({zip_file_name})",
        "",
        "## 8. 下次可問 AI 的問題",
        "1. 請根據這份回測紀錄，指出策略最大弱點。",
        "2. 請比較最大單筆虧損與出場原因，判斷是否需要調整停損。",
        "3. 請根據 trades.csv，分析多單與空單哪一邊比較有優勢。",
        "4. 請根據 equity_curve.csv，找出最大回撤期間，並判斷當時策略失效原因。",
    ]
    return "\n".join(lines) + "\n"


def update_obsidian_index(record_dir: str, record_folder_name: str) -> None:
    """在根目錄維護一份 Obsidian 可閱讀的回測索引。"""
    index_path = os.path.join(record_dir, "000_MTX回測索引.md")
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    row = (
        f"| {now} | [[{record_folder_name}/00_回測總覽|總覽]] | "
        f"{_md_metric('總損益(元)')} | {_md_metric('交易次數')} | "
        f"{_md_metric('勝率(%)')}% | {_md_metric('最大回撤(元)')} | "
        f"{_md_metric('斷頭次數')} | 待檢討 |\n"
    )
    header = (
        "# MTX 回測索引\n\n"
        "這份檔案由台指期回測工具自動更新。每次回測會新增一筆紀錄。\n\n"
        "| 建立時間 | 總覽 | 總損益(元) | 交易次數 | 勝率 | 最大回撤(元) | 斷頭次數 | 狀態 |\n"
        "|---|---|---:|---:|---:|---:|---:|---|\n"
    )
    if not os.path.exists(index_path):
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(header)
            f.write(row)
        return

    with open(index_path, "r", encoding="utf-8") as f:
        text = f.read()
    if record_folder_name in text:
        return
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(row)


def save_ai_pack_to_record_folder(data: bytes, file_name: str) -> tuple:
    """建立 Obsidian 可直接閱讀的回測紀錄資料夾，並保存 ZIP 備份。\n\n    回傳 (record_folder_path, error_message)。若成功，error_message 為空字串。\n    """
    try:
        os.makedirs(DEFAULT_RECORD_DIR, exist_ok=True)
        record_folder_name = build_obsidian_record_folder_name(file_name)
        record_folder = os.path.join(DEFAULT_RECORD_DIR, record_folder_name)
        os.makedirs(record_folder, exist_ok=True)

        # 1) 保存完整 ZIP 備份。
        zip_path = os.path.join(record_folder, file_name)
        with open(zip_path, "wb") as f:
            f.write(data)

        # 2) 將 ZIP 內的 Markdown / CSV / JSON 解出，供 Obsidian 直接閱讀與索引。
        with zipfile.ZipFile(io.BytesIO(data), "r") as z:
            for info in z.infolist():
                name = os.path.basename(info.filename)
                if not name:
                    continue
                out_path = os.path.join(record_folder, name)
                with open(out_path, "wb") as f:
                    f.write(z.read(info.filename))

        # 3) 建立 Obsidian 主檔與根目錄索引。
        overview_path = os.path.join(record_folder, "00_回測總覽.md")
        with open(overview_path, "w", encoding="utf-8") as f:
            f.write(build_obsidian_overview_md(record_folder_name, file_name))
        update_obsidian_index(DEFAULT_RECORD_DIR, record_folder_name)
        return record_folder, ""
    except Exception as e:  # noqa: BLE001
        return "", str(e)


# ---------------- 匯出 ----------------
st.markdown("#### 匯出")
ai_pack_bytes = build_ai_pack()

# v0.3.9：每次新的回測結果只自動建立一次 Obsidian 紀錄資料夾，避免畫面重整時重複產生檔案。
if st.session_state.get("ai_pack_saved_hash") != bt["hash"]:
    ai_pack_file_name = build_ai_pack_filename()
    if cloud_operation_mode:
        saved_path, save_error = upload_result_zip_to_google_drive(
            ai_pack_bytes, ai_pack_file_name, build_obsidian_record_folder_name(ai_pack_file_name)
        )
    else:
        saved_path, save_error = save_ai_pack_to_record_folder(ai_pack_bytes, ai_pack_file_name)
    st.session_state["ai_pack_saved_hash"] = bt["hash"]
    st.session_state["ai_pack_file_name"] = ai_pack_file_name
    st.session_state["ai_pack_saved_path"] = saved_path
    st.session_state["ai_pack_save_error"] = save_error
else:
    ai_pack_file_name = st.session_state.get("ai_pack_file_name", "AI回測分析包.zip")
    saved_path = st.session_state.get("ai_pack_saved_path", "")
    save_error = st.session_state.get("ai_pack_save_error", "")

if saved_path:
    st.success(f"已自動上傳單次回測結果到 Google Drive：{saved_path}" if cloud_operation_mode else f"已自動建立 Obsidian 回測紀錄資料夾：{saved_path}")
elif save_error:
    st.warning((f"Google Drive 自動上傳尚未完成，原因：{save_error}" if cloud_operation_mode else f"AI 分析包下載按鈕仍可使用，但自動建立 Obsidian 回測紀錄到 {DEFAULT_RECORD_DIR} 失敗：{save_error}"))

e1, e2, e3, e4, e5, e6 = st.columns(6)
e1.download_button("交易明細 CSV", df_to_csv_bytes(trades_zh),
                   file_name="交易明細.csv", use_container_width=True)
e2.download_button("績效統計 CSV", df_to_csv_bytes(metrics_to_df(m)),
                   file_name="績效統計.csv", use_container_width=True)
e3.download_button("連續契約 CSV", df_to_csv_bytes(bt["cont"]),
                   file_name="clean_continuous.csv", use_container_width=True)
e4.download_button("換倉紀錄 CSV", df_to_csv_bytes(bt["roll_log"]),
                   file_name="rollover_log.csv", use_container_width=True)
e5.download_button("策略 JSON", json.dumps(bt["cfg"], ensure_ascii=False,
                                          indent=2).encode("utf-8"),
                   file_name="strategy.json", use_container_width=True)
e6.download_button("AI 分析包 ZIP", ai_pack_bytes,
                   file_name=ai_pack_file_name, use_container_width=True, type="primary")
st.caption("AI 分析包內含：AI_回測分析摘要.md、trades.csv、metrics.csv、"
           "equity_curve.csv、strategy_config.json，可直接丟給 ChatGPT／Claude／Gemini 分析。"
           f"本機執行時也會自動在 {DEFAULT_RECORD_DIR} 建立 Obsidian 回測紀錄資料夾，"
           "內含 00_回測總覽.md、AI_回測分析摘要.md、CSV/JSON 附件與 ZIP 備份。")
