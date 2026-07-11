# -*- coding: utf-8 -*-
"""
app.py - 台指期回測工具 Streamlit 介面（v0.6.7 動態部位與50萬資金池版）。

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


APP_VERSION = "v0.6.7"
APP_RELEASE_NAME = "動態部位與50萬資金池版"


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
st.set_page_config(page_title=f"MTX 台指期回測 {APP_VERSION}", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
:root {
  --ink: #334155;
  --muted: #64748b;
  --paper: #f7faf9;
  --panel: #ffffff;
  --sidebar: #fff8f0;
  --line: #f0d8c7;
  --accent: #e58a5b;
  --accent-dark: #c96b3d;
  --accent-soft: #fce8da;
  --primary: #467f70;
  --primary-dark: #35685b;
  --success: #467f70;
  --danger: #c85d54;
}
.stApp { background: var(--paper); color: var(--ink); }
.block-container { padding-top: 1.25rem; max-width: 1500px; }
[data-testid="stHeader"] { height: 1.5rem; background: transparent; }
[data-testid="stHeader"] > div, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none; }
h1, h2, h3 { color: var(--ink); letter-spacing: -.01em; }
p, label, .stMarkdown { color: var(--ink); }
[data-testid="stSidebar"] {
  background: var(--sidebar);
  border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] > div:first-child { padding-top: .65rem; }
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] summary { color: #3f4b55 !important; }
[data-testid="stSidebar"] small,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: var(--muted) !important; }
[data-testid="stSidebar"] hr { border-color: var(--line); margin: .65rem 0; }
[data-testid="stSidebar"] [data-baseweb="input"] > div,
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] textarea,
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
  background: #ffffff !important;
  border-color: #e8cdb9 !important;
  color: var(--ink) !important;
}
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea { color: var(--ink) !important; }
[data-testid="stSidebar"] [data-baseweb="input"] > div,
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] textarea,
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] { border-radius: 12px !important; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: .25rem; }
[data-testid="stSidebar"] [role="radiogroup"] label { background: rgba(255,255,255,.72); border: 1px solid var(--line); border-radius: 999px; padding: .25rem .55rem; }
[data-testid="stSidebar"] [data-testid="stCheckbox"] label { border-radius: 10px; }
[data-testid="stPopover"] button { border-radius: 999px !important; min-width: 1.8rem !important; width: 1.8rem !important; height: 1.8rem !important; padding: 0 !important; color: var(--accent-dark) !important; border-color: #edc7af !important; background: #fff !important; font-weight: 800 !important; }
[data-testid="stSidebar"] [data-testid="stExpander"] {
  background: rgba(255,255,255,.48);
  border: 1px solid var(--line);
  border-radius: 12px;
}
[data-testid="stSidebar"] button[aria-label="Help"] { color: var(--accent-dark) !important; }
[data-testid="stSidebar"] [data-baseweb="slider"] [role="slider"] {
  background: var(--accent) !important;
  border-color: #fff !important;
  box-shadow: 0 0 0 1px var(--accent-dark) !important;
}
[data-testid="stSidebar"] [data-baseweb="slider"] > div > div > div { background: var(--accent) !important; }
.sidebar-brand {
  background: linear-gradient(135deg, #ffffff, #fff0e4);
  border: 1px solid var(--line); border-radius: 16px;
  padding: 14px 14px 12px; margin: 0 0 12px;
  box-shadow: 0 4px 14px rgba(101,67,42,.08);
}
.sidebar-brand .name { font-weight: 850; font-size: 1.08rem; color: #2f3a44; }
.sidebar-brand .sub { color: var(--muted); font-size: .76rem; margin-top: 3px; }
.version-pill {
  display: inline-block; background: var(--primary); color: #fff !important;
  border-radius: 999px; padding: 2px 9px; margin-left: 6px;
  font-size: .72rem; font-weight: 800; vertical-align: 1px;
}
.sidebar-section-title {
  font-size: .82rem; font-weight: 850; color: #8a5638;
  letter-spacing: .04em; margin: 12px 0 5px;
}
.sidebar-status {
  background: #ffffff; border: 1px solid var(--line); border-radius: 10px;
  padding: 7px 9px; color: var(--muted); font-size: .78rem; margin: 4px 0 8px;
}
.app-hero {
  background: linear-gradient(135deg, #ffffff 0%, #fff0e5 100%);
  border: 1px solid var(--line); border-radius: 18px;
  padding: 18px 22px; margin: 0 0 14px;
  box-shadow: 0 6px 18px rgba(84,54,34,.08);
}
.app-hero .eyebrow { color: var(--accent-dark); font-size: .76rem; font-weight: 850; letter-spacing: .08em; }
.app-hero .title { color: #2f3a44; font-size: 1.65rem; font-weight: 900; margin: 2px 0 3px; }
.app-hero .desc { color: var(--muted); font-size: .9rem; }
.action-note { color: var(--muted); font-size: .82rem; padding-top: .45rem; }
.metric-card, .summary-card, .cloud-card, .mobile-quick-card {
  background: var(--panel); border: 1px solid var(--line);
  box-shadow: 0 2px 8px rgba(84,54,34,.07);
}
.metric-card { border-radius: 12px; padding: 10px 6px; text-align:center; }
.metric-card .lbl { font-size:.76rem; color:var(--muted); margin-bottom:2px; }
.metric-card .val { font-size:1.16rem; font-weight:800; color:var(--ink); }
.metric-card .pos { color:var(--danger); }
.metric-card .neg { color:var(--success); }
.summary-card { border-radius:14px; padding:14px 18px; margin: 0 0 14px; }
.summary-card .title { font-size:1.03rem; font-weight:850; color:var(--ink); margin-bottom:8px; }
.summary-card .grid { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:8px 14px; }
.summary-card .item { color:#44515c; font-size:.9rem; }
.summary-card .label { color:var(--muted); font-size:.76rem; display:block; }
.combo-chip { display:inline-block; background:#fff0e5; border:1px solid #efcdb6;
  border-radius:16px; padding:2px 10px; margin:2px 4px 2px 0; font-size:.83rem; color:#5a4335; }
.mobile-quick-card { border-radius:14px; padding:12px 14px; margin-bottom:12px; }
div[data-testid="stButton"] > button[kind="primary"],
div[data-testid="stDownloadButton"] > button[kind="primary"] {
  background: var(--primary); border-color: var(--primary); color: white;
  font-weight: 800; border-radius: 12px;
}
div[data-testid="stButton"] > button[kind="primary"]:hover,
div[data-testid="stDownloadButton"] > button[kind="primary"]:hover {
  background: var(--primary-dark); border-color: var(--primary-dark); color: white;
}
div[data-testid="stButton"] > button { border-radius: 11px; }
@media (max-width: 768px) {
  .block-container { padding: .65rem .5rem 1.25rem; max-width: 100%; }
  .app-hero { padding: 13px 14px; border-radius: 14px; }
  .app-hero .title { font-size: 1.3rem; }
  .app-hero .desc { font-size: .82rem; }
  .summary-card { padding:10px; }
  .summary-card .grid { grid-template-columns: 1fr; gap:6px; }
  .metric-card .val { font-size:1rem; }
  h1 { font-size:1.32rem !important; }
  h2 { font-size:1.16rem !important; }
  h3 { font-size:1.02rem !important; }
  div[data-testid="stButton"] > button { min-height: 2.7rem; }
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
    "quantity": "小台等值口數",
    "small_quantity": "小台口數", "micro_quantity": "微台口數",
    "position_micro_units": "微台等值單位", "point_value_total": "部位每點總價值",
    "position_margin_amount": "進場原始保證金",
    "maintenance_margin_amount": "維持保證金",
    "position_sizing_mode": "部位模式",
    "pnl_points": "損益點數", "pnl_amount": "損益金額",
    "holding_bars": "持倉K棒數", "exit_reason": "出場原因",
    "entry_reason": "進場條件",
    "max_adverse_points": "最大反向浮動點數",
    "max_adverse_amount": "最大反向浮動金額",
    "max_favorable_points": "最大順向浮動點數",
    "max_favorable_amount": "最大順向浮動金額",
    "entry_atr": "進場可用ATR",
    "planned_stop_points": "預定停損點數",
    "planned_stop_risk_amount": "預定停損風險金額",
    "entry_risk_cap_amount": "單筆風險上限",
    "risk_budget_amount": "本筆風險預算",
    "stress_risk_amount": "跳空壓力風險",
    "stress_multiple": "跳空壓力倍數",
    "available_equity_at_entry": "進場時可用權益",
    "max_favorable_atr_multiple": "最大順向浮盈ATR倍數",
    "required_safety_capital": "當筆最低所需安全資金",
}
TRADE_DISPLAY_COLS = ["訊號日", "進場日", "出場日", "方向", "進場價", "進場可用ATR",
                      "預定停損點數", "預定停損風險金額", "本筆風險預算",
                      "跳空壓力風險", "小台等值口數", "小台口數", "微台口數",
                      "出場價", "損益點數", "損益金額", "最大順向浮盈ATR倍數",
                      "持倉K棒數", "出場原因", "進場條件"]

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
    ("use_profit_tier_chandelier", "分段吊燈", "依浮盈相對 ATR 或固定金額切換吊燈倍數"),
    ("use_macd_reverse",  "MACD 反向出場", "MACD 柱狀圖轉向（多單轉負／空單轉正）出場"),
]

PARAM_DEFAULTS = {
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "bb_period": 20, "bb_std": 2.0,
    "ma_filter_enabled": True, "ma_filter_period": 20, "ma_filter_type": "SMA",
    "use_chandelier": True, "chandelier_period": 22, "chandelier_mult": 3.0,
    "use_profit_tier_chandelier": False, "profit_tier_chandelier_period": 22,
    "profit_tier_amounts": (), "profit_tier_atr_multiples": (2.0, 4.0, 8.0),
    "profit_tier_threshold_mode": "entry_atr",
    "profit_tier_mults": (2.5, 3.0, 3.5, 5.0),
    "profit_tier_reference": "max_favorable",
    "use_profit_scaled_macd_exclusion": False,
    "macd_reverse_exclude_profit_amount": 0.0,
    "macd_reverse_exclude_atr_multiple": 4.0,
    "use_macd_reverse": True,
    "use_fixed_stop": True, "stop_threshold_mode": "points",
    "stop_points": 100.0, "stop_atr_multiple": 0.75,
    "use_entry_risk_cap": False, "max_entry_risk_amount": 20000.0,
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
               "use_profit_tier_chandelier", "profit_tier_chandelier_period",
               "profit_tier_amounts", "profit_tier_atr_multiples", "profit_tier_threshold_mode",
               "profit_tier_mults", "profit_tier_reference",
               "use_profit_scaled_macd_exclusion", "macd_reverse_exclude_profit_amount",
               "macd_reverse_exclude_atr_multiple",
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


def _zh_exit_reason(value):
    if value in EXIT_REASON_LABELS:
        return EXIT_REASON_LABELS[value]
    m = re.fullmatch(r"profit_tier_chandelier_([0-9.]+)", str(value))
    if m:
        return f"分段吊燈出場（吊燈 ATR×{m.group(1)}）"
    return value


def zh_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame(columns=TRADE_DISPLAY_COLS)
    t = trades.copy()
    for c in ["signal_date", "entry_date", "entry_execution_date", "exit_date"]:
        if c in t.columns:
            t[c] = pd.to_datetime(t[c]).dt.strftime("%Y/%m/%d")
    t["direction"] = t["direction"].map(DIRECTION_LABELS).fillna(t["direction"])
    t["exit_reason"] = t["exit_reason"].map(_zh_exit_reason)
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
        if k.startswith(("w_", "s_")):
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
        for key in ("profit_tier_amounts", "profit_tier_atr_multiples", "profit_tier_mults"):
            if key in strat["params"] and strat["params"][key] is not None:
                strat["params"][key] = tuple(float(x) for x in strat["params"][key])
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
    tuple_fields = {"profit_tier_amounts", "profit_tier_atr_multiples", "profit_tier_mults"}
    for k, v in strat["params"].items():
        if k not in tuple_fields:
            st.session_state["s_p_" + k] = v
    st.session_state["s_p_profit_tier_amounts_text"] = ", ".join(
        f"{float(x):g}" for x in strat["params"].get("profit_tier_amounts", ()))
    st.session_state["s_p_profit_tier_atr_multiples_text"] = ", ".join(
        f"{float(x):g}" for x in strat["params"].get("profit_tier_atr_multiples", (2, 4, 8)))
    st.session_state["s_p_profit_tier_mults_text"] = ", ".join(
        f"{float(x):g}" for x in strat["params"].get("profit_tier_mults", (2.5, 3, 3.5, 5)))
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


def _parse_number_list(text, fallback=()):
    try:
        values = tuple(float(x.strip()) for x in str(text).split(",") if x.strip())
        return values if values else tuple(fallback)
    except (TypeError, ValueError):
        return tuple(fallback)


def _collect_dialog() -> dict:
    strat = {"params": {}, "th": {}, "combos_long": [], "combos_short": []}
    for ci in range(2):
        strat["combos_long"].append(_collect_combo(f"s_L{ci}"))
        strat["combos_short"].append(_collect_combo(f"s_S{ci}"))
    strat["exit_long"] = _collect_combo("s_XL")
    strat["exit_short"] = _collect_combo("s_XS")
    tuple_fields = {"profit_tier_amounts", "profit_tier_atr_multiples", "profit_tier_mults"}
    for k, dv in PARAM_DEFAULTS.items():
        if k in tuple_fields:
            continue
        v = st.session_state.get("s_p_" + k, dv)
        strat["params"][k] = type(dv)(v) if not isinstance(dv, bool) else bool(v)
    strat["params"]["profit_tier_amounts"] = _parse_number_list(
        st.session_state.get("s_p_profit_tier_amounts_text", ""), ())
    strat["params"]["profit_tier_atr_multiples"] = _parse_number_list(
        st.session_state.get("s_p_profit_tier_atr_multiples_text", "2, 4, 8"), (2, 4, 8))
    strat["params"]["profit_tier_mults"] = _parse_number_list(
        st.session_state.get("s_p_profit_tier_mults_text", "2.5, 3, 3.5, 5"),
        (2.5, 3.0, 3.5, 5.0))
    for k, dv in TH_DEFAULTS.items():
        strat["th"][k] = st.session_state.get("s_t_" + k, dv)
    strat["direction"] = DIR_LABELS[st.session_state.get("s_direction_label", "多空雙向")]
    return strat


def _compact_help(text: str):
    with st.popover("?", help="查看說明"):
        st.markdown(text)


def _compact_header(title: str, help_text: str):
    hc1, hc2 = st.columns([12, 1])
    hc1.markdown(f"**{title}**")
    with hc2:
        _compact_help(help_text)


# ============ 面板編輯器：類型 → 型態 二層勾選 ============
def _slot_editor(prefix: str, title: str, hint: str, with_n: bool = False):
    """一個條件槽：先勾「類型」，再展開該類型的「型態」勾選格。"""
    _compact_header(title, hint)
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


@st.dialog("策略設定", width="large")
def strategy_dialog():
    # 哨兵鍵：面板關閉後 Streamlit 會回收未渲染的 widget 狀態，
    # 重新開啟時哨兵不存在 -> 從已套用的策略設定重新灌入暫存值。
    if "s_direction_label" not in st.session_state:
        _seed_dialog()

    st.caption("設定進場、出場與參數；套用後回到主畫面執行回測。")
    tab_l, tab_s, tab_x, tab_p = st.tabs(
        ["多單進場", "空單進場", "出場條件", "進階參數"])

    with tab_l:
        st.radio("交易方向", list(DIR_LABELS.keys()),
                 key="s_direction_label", horizontal=True)
        _compact_header("多單進場組合", "同一組合內採 AND；多個組合之間採 OR。前提、條件與排除皆可留空。")
        _combo_editor("s_L0", "組合 A", expanded=True)
        _combo_editor("s_L1", "組合 B（可留空）")

    with tab_s:
        _compact_header("空單進場組合", "空單與多單獨立設定；只做多時可全部留空。")
        _combo_editor("s_S0", "組合 A", expanded=True)
        _combo_editor("s_S1", "組合 B（可留空）")

    with tab_x:
        _compact_header("出場方式", "可複選。觸價類依序檢查：固定停損、固定停利、移動停損；收盤類再檢查吊燈、MACD 與條件出場。")
        xc = st.columns(2)
        with xc[0]:
            use_stop = st.checkbox("停損", key="s_p_use_fixed_stop", help="可選固定點數或進場可用 ATR 倍數")
            if use_stop:
                st.selectbox(
                    "停損尺度", ["entry_atr", "points"], key="s_p_stop_threshold_mode",
                    format_func=lambda x: "進場可用 ATR 倍數（建議）" if x == "entry_atr" else "固定點數（舊策略相容）")
                if st.session_state.get("s_p_stop_threshold_mode", "entry_atr") == "entry_atr":
                    st.number_input("停損 ATR 倍數", 0.1, 10.0, step=0.05, key="s_p_stop_atr_multiple",
                                    help="固定使用訊號根 ATR，不使用進場當根尚未完成的波動")
                    use_risk_cap = st.checkbox(
                        "超過單筆風險上限則不進場", key="s_p_use_entry_risk_cap",
                        help="預定停損點數×每點價值×口數超過上限時略過；跳空仍可能使實際損失超標")
                    if use_risk_cap:
                        st.number_input("單筆最大可承受金額", 1000.0, 1000000.0, step=1000.0,
                                        key="s_p_max_entry_risk_amount")
                else:
                    st.number_input("停損點數", 1.0, 5000.0, step=10.0, key="s_p_stop_points")
            use_tp = st.checkbox("固定停利", key="s_p_use_take_profit", help="獲利達設定點數就出場")
            if use_tp:
                st.number_input("停利點數", 1.0, 10000.0, step=10.0, key="s_p_take_profit_points")
            use_trailing = st.checkbox("移動停損", key="s_p_use_trailing_stop", help="從進場後最高或最低點回落設定點數出場")
            if use_trailing:
                st.number_input("移動停損點數", 1.0, 5000.0, step=10.0, key="s_p_trailing_points")
        with xc[1]:
            use_chandelier = st.checkbox("吊燈出場", key="s_p_use_chandelier", help="跌破 N 日極值 ± ATR×倍數的追蹤線後，以收盤確認出場")
            if use_chandelier:
                st.number_input("吊燈週期", 2, 200, key="s_p_chandelier_period")
                st.number_input("吊燈 ATR 倍數", 0.5, 10.0, step=0.1, key="s_p_chandelier_mult")
            st.checkbox("MACD 反向出場", key="s_p_use_macd_reverse", help="MACD 柱狀圖反向時，以收盤確認出場")
        with st.expander("ATR 標準化分段出場", expanded=False):
            _compact_header(
                "相對波動階梯",
                "以『最大順向浮盈點數 ÷ 進場前已完成 K 棒 ATR』決定吊燈倍數。"
                "不使用進場當根尚未完成的 ATR，避免未來函數。")
            use_tier = st.checkbox(
                "啟用分段吊燈", key="s_p_use_profit_tier_chandelier",
                help="浮盈相對 ATR 越大，可切換到較寬的吊燈倍數")
            if use_tier:
                st.selectbox(
                    "門檻單位", ["entry_atr", "amount"], key="s_p_profit_tier_threshold_mode",
                    format_func=lambda x: "進場可用 ATR 倍數（建議）" if x == "entry_atr" else "固定金額（舊策略相容）")
                st.selectbox(
                    "分段依據", ["max_favorable", "current_unrealized"], key="s_p_profit_tier_reference",
                    format_func=lambda x: "持倉最大順向浮盈" if x == "max_favorable" else "當根收盤浮盈")
                st.number_input("分段吊燈週期", 2, 200, key="s_p_profit_tier_chandelier_period")
                if st.session_state.get("s_p_profit_tier_threshold_mode", "entry_atr") == "entry_atr":
                    st.text_input(
                        "ATR 階梯（逗號分隔）", key="s_p_profit_tier_atr_multiples_text",
                        help="例如 2, 4, 8；須由小到大")
                else:
                    st.text_input(
                        "金額階梯（逗號分隔）", key="s_p_profit_tier_amounts_text",
                        help="僅供舊策略相容，新的研究建議使用 ATR 倍數")
                st.text_input(
                    "各段吊燈 ATR 倍數", key="s_p_profit_tier_mults_text",
                    help="數量必須比階梯多 1，例如三個階梯填四個倍數")
                use_exclusion = st.checkbox(
                    "達門檻後排除 MACD 反向", key="s_p_use_profit_scaled_macd_exclusion",
                    help="避免大行情被短期 MACD 反向提早洗出")
                if use_exclusion:
                    if st.session_state.get("s_p_profit_tier_threshold_mode", "entry_atr") == "entry_atr":
                        st.number_input(
                            "排除 MACD 的 ATR 倍數", 0.1, 100.0, step=0.5,
                            key="s_p_macd_reverse_exclude_atr_multiple")
                    else:
                        st.number_input(
                            "排除 MACD 的浮盈金額", 1.0, 10000000.0, step=1000.0,
                            key="s_p_macd_reverse_exclude_profit_amount")
        st.divider()
        _compact_header("條件出場", "符合條件組合即平倉；全部留空代表不使用。")
        _combo_editor("s_XL", "多單條件出場")
        _combo_editor("s_XS", "空單條件出場")

    with tab_p:
        st.caption("只有需要微調指標時才展開；維持預設值即可直接回測。")
        with st.expander("MACD 與布林通道", expanded=False):
            pc1, pc2 = st.columns(2)
            with pc1:
                st.number_input("MACD 快線週期", 2, 100, key="s_p_macd_fast")
                st.number_input("MACD 慢線週期", 3, 300, key="s_p_macd_slow")
                st.number_input("MACD 訊號線週期", 2, 100, key="s_p_macd_signal")
            with pc2:
                st.number_input("布林週期", 2, 300, key="s_p_bb_period")
                st.number_input("布林標準差倍數", 0.5, 5.0, step=0.1, key="s_p_bb_std")
        with st.expander("均線", expanded=False):
            pc1, pc2 = st.columns(2)
            with pc1:
                st.selectbox("趨勢均線型態", ["SMA", "EMA", "WMA"], key="s_p_ma_filter_type")
                st.number_input("趨勢均線週期", 2, 500, key="s_p_ma_filter_period")
            with pc2:
                st.number_input("交叉短均週期", 2, 200, key="s_t_ma_fast")
                st.number_input("交叉長均週期", 3, 500, key="s_t_ma_slow")
                st.text_input("排列均線（逗號分隔）", key="s_t_align_periods")
        with st.expander("KD、RSI 與量能", expanded=False):
            pc1, pc2 = st.columns(2)
            with pc1:
                st.number_input("KD 週期", 2, 100, key="s_p_kd_period")
                st.number_input("KD 低檔門檻", 1.0, 50.0, step=1.0, key="s_t_kd_low")
                st.number_input("KD 高檔門檻", 50.0, 99.0, step=1.0, key="s_t_kd_high")
                st.number_input("RSI 週期", 2, 100, key="s_p_rsi_period")
                st.number_input("RSI 門檻", 1.0, 99.0, step=1.0, key="s_t_rsi_value")
            with pc2:
                st.number_input("量能倍數（相對量均）", 0.5, 10.0, step=0.1, key="s_t_vol_mult")
                st.number_input("爆量倍數（相對昨日量）", 0.5, 10.0, step=0.1, key="s_t_vol_prev_mult")
                st.number_input("量均週期", 2, 200, key="s_p_vol_ma_period")

    st.markdown("---")
    b1, b2 = st.columns([2.5, 1])
    run = b1.button("開始回測", type="primary", use_container_width=True,
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

# ================= 左側控制欄（v0.6.6 延續精簡版） =================
selected_cloud_file = None
cloud_mode = "前後期行情對照：2015～2023 一般行情 vs 2024～資料末日牛市行情"
manual_ready = False

BATCH_MODE_MAP = {
    "前後期行情對照": "前後期行情對照：2015～2023 一般行情 vs 2024～資料末日牛市行情",
    "前期行情（2015～2023）": "前期行情回測：2015～2023",
    "後期牛市（2024～資料末日）": "後期牛市回測：2024～資料末日",
    "目前畫面期間": "目前畫面期間回測：使用上方起迄日",
}

with st.sidebar:
    st.markdown(
        f'''<div class="sidebar-brand">
        <div class="name">MTX 台指期回測 <span class="version-pill">{APP_VERSION}</span></div>
        <div class="sub">{APP_RELEASE_NAME}｜可用版本標示確認部署是否更新</div>
        </div>''',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="sidebar-section-title">使用方式</div>', unsafe_allow_html=True)
    usage_profile = st.selectbox(
        "操作環境",
        ["雲端・桌機", "雲端・手機", "本機・桌機"],
        index=0,
        key="w_usage_profile",
        help="雲端模式可讀取策略投放箱並上傳結果；手機模式會調整操作順序與按鈕尺寸。",
    )
    cloud_operation_mode = usage_profile.startswith("雲端")
    mobile_mode = "手機" in usage_profile

    if cloud_operation_mode:
        st.markdown('<div class="sidebar-section-title">雲端批次策略</div>', unsafe_allow_html=True)
        cloud_files = []
        cloud_list_error = ""
        try:
            cloud_files = list_cloud_strategy_files()
        except Exception as e:  # noqa: BLE001
            cloud_list_error = str(e)

        if cloud_list_error:
            st.warning("策略投放箱暫時無法連線。")
        elif cloud_files:
            labels = [item.get("name", "未命名.json") for item in cloud_files]
            cloud_pick = st.selectbox(
                "策略檔",
                options=list(range(len(cloud_files))),
                format_func=lambda i: labels[i],
                key="batch_cloud_drive_pick",
                help="直接讀取 Google Drive `_策略投放箱`；最新修改的 JSON 排在前面。",
            )
            selected_cloud_file = cloud_files[cloud_pick]
            st.markdown(
                f'<div class="sidebar-status">已連線・{len(cloud_files)} 個策略檔</div>',
                unsafe_allow_html=True,
            )
        else:
            st.info("策略投放箱目前沒有 JSON。")

        cloud_mode_label = st.selectbox(
            "回測範圍",
            list(BATCH_MODE_MAP.keys()),
            index=0,
            key="batch_cloud_mode_label",
            help="前後期行情對照會自動跑兩個期間並產生比較表；其餘模式只跑單一期間。",
        )
        cloud_mode = BATCH_MODE_MAP[cloud_mode_label]
        refresh_col, run_col = st.columns([1, 2])
        if refresh_col.button("重新整理", use_container_width=True, help="重新讀取策略投放箱"):
            list_cloud_strategy_files.clear()
            st.rerun()
        if run_col.button(
            "開始批次回測",
            use_container_width=True,
            type="primary",
            disabled=not bool(selected_cloud_file),
            help="執行目前選取的策略 JSON。",
        ):
            try:
                raw = load_batch_json_from_drive_file(selected_cloud_file["id"])
                loaded_from = "gdrive:" + selected_cloud_file["id"] + ":" + selected_cloud_file.get("modifiedTime", "")
                set_batch_json_and_queue(raw, loaded_from, cloud_mode, selected_cloud_file.get("name", ""))
            except Exception as e:  # noqa: BLE001
                st.error(f"批次策略讀取失敗：{e}")
    else:
        st.markdown('<div class="sidebar-section-title">本機批次策略</div>', unsafe_allow_html=True)
        ok, msg = ensure_strategy_dropbox()
        local_files = list_strategy_dropbox_files() if ok else []
        if not ok:
            st.warning(f"本機投放箱無法使用：{msg}")
        elif local_files:
            labels = [f["name"] for f in local_files]
            local_pick = st.selectbox(
                "策略檔",
                options=list(range(len(local_files))),
                format_func=lambda i: labels[i],
                key="batch_dropbox_pick_main",
                help="讀取本機策略投放箱內的 JSON。",
            )
        else:
            local_pick = None
            st.info("本機投放箱目前沒有 JSON。")
        local_mode_label = st.selectbox(
            "回測範圍",
            list(BATCH_MODE_MAP.keys()),
            index=0,
            key="batch_local_mode_label",
        )
        cloud_mode = BATCH_MODE_MAP[local_mode_label]
        if st.button(
            "開始批次回測",
            use_container_width=True,
            type="primary",
            disabled=local_pick is None,
        ):
            try:
                picked = local_files[local_pick]
                raw = load_batch_json_from_dropbox(picked["path"])
                set_batch_json_and_queue(
                    raw,
                    picked["path"] + str(picked["mtime"]),
                    cloud_mode,
                    picked["name"],
                )
            except Exception as e:  # noqa: BLE001
                st.error(f"本機策略讀取失敗：{e}")

    st.markdown('<div class="sidebar-section-title">單次回測資料</div>', unsafe_allow_html=True)
    symbol = st.selectbox(
        "商品",
        list(SYMBOLS.keys()),
        index=list(SYMBOLS.keys()).index(DEFAULT_SYMBOL),
        format_func=lambda s: f"{s} {SYMBOLS[s]['name']}",
        help="目前研究以 MTX 小型台指為主。",
    )
    date_box = st.container()

    with st.expander("顯示與成本", expanded=False):
        custom_costs = st.checkbox(
            "自訂成本與資金",
            value=False,
            key="w_custom_costs",
            help="未勾選時使用商品預設值；勾選後才顯示手續費、滑價、期交稅與初始資金。",
        )
        simple_mode = not custom_costs
        cost_box = st.empty()
        st.markdown("**圖表圖層**")
        gc1, gc2 = st.columns(2)
        with gc1:
            st.checkbox("K 線", key="w_show_candlestick")
            st.checkbox("交易標記", key="w_show_trade_markers")
            st.checkbox("布林通道", key="w_show_bollinger")
            st.checkbox("吊燈線", key="w_show_chandelier_lines")
            st.checkbox("均線", key="w_show_ma")
        with gc2:
            st.checkbox("成交量", key="w_show_volume")
            st.checkbox("MACD", key="w_show_macd_panel")
            st.checkbox("KD", key="w_show_kd_panel")
            st.checkbox("資金曲線", key="w_show_equity_curve")
        st.text_input(
            "圖表均線週期",
            key="w_ma_periods_text",
            help="以逗號分隔，例如 5,10,20,60,120,240。只影響圖表，不改變回測結果。",
        )

    with st.expander("資料與換倉進階", expanded=False):
        folder = st.text_input(
            "CSV 資料夾路徑",
            value=DEFAULT_DATA_FOLDER,
            help="雲端通常自動指向 txf_backtester/data；只有資料位置改變時才需要調整。",
        )
        session_label = st.selectbox(
            "交易時段", list(SESSION_LABELS.keys()), index=0,
            help="日 K 研究通常使用一般盤。",
        )
        method_label = st.selectbox(
            "連續契約規則", list(METHOD_LABELS.keys()), index=0,
            help="穩定換倉會避免主力契約短暫切換造成跳動。",
        )
        n_confirm = st.number_input(
            "換倉確認天數", 1, 10, value=3,
            help="主力契約連續符合條件幾天後才正式換倉。",
        )
        exclude_weekly = st.checkbox(
            "排除週契約", value=True,
            help="排除到期月份含 W 的週契約。",
        )

    if "folder" not in locals():
        folder = DEFAULT_DATA_FOLDER
    if "session_label" not in locals():
        session_label = "一般盤"
        method_label = "穩定換倉（預設）"
        n_confirm = 3
        exclude_weekly = True

    if symbol == "MTX" and has_prepared_mtx(folder):
        session_label, method_label = "一般盤", "穩定換倉（預設）"
        n_confirm, exclude_weekly = 3, True
        st.markdown('<div class="sidebar-status">MTX 準備資料已就緒</div>', unsafe_allow_html=True)

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
    st.session_state.setdefault("w_use_tax", True)

    with st.expander("其他策略來源", expanded=False):
        st.markdown("**單次策略參數檔**")
        up = st.file_uploader(
            "載入單次策略 JSON",
            type=["json"],
            help="只用於單次回測；載入後仍需按主畫面的開始回測。",
        )
        if up is not None and st.session_state.get("loaded_file") != up.name + str(up.size):
            try:
                apply_loaded_params(load_params_json(up))
                st.session_state["loaded_file"] = up.name + str(up.size)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"參數檔讀取失敗：{e}")

        st.markdown("**手動上傳批次 JSON**")
        batch_up = st.file_uploader(
            "上傳批次策略 JSON",
            type=["json"],
            key="batch_strategy_json_uploader",
            help="無法使用雲端投放箱時，才需要手動上傳。最多 20 組策略。",
        )
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
            manual_mode_label = st.selectbox(
                "手動檔回測範圍",
                list(BATCH_MODE_MAP.keys()),
                index=0,
                key="batch_manual_mode_label",
            )
            if st.button("執行手動批次回測", use_container_width=True, type="primary"):
                queue_batch_mode(BATCH_MODE_MAP[manual_mode_label])

        st.markdown("**手動雲端連結**")
        cloud_url = st.text_input(
            "策略 JSON 連結",
            value=st.session_state.get("batch_cloud_url", DEFAULT_CLOUD_BATCH_JSON_URL),
            key="batch_cloud_url",
            help="僅在投放箱無法使用時作為備援。連線失敗會改讀內建故障測試檔，且不列入正式研究。",
        )
        if st.button("從連結載入並執行", use_container_width=True):
            try:
                raw, loaded_from, _ = load_cloud_or_bundled_batch_json(cloud_url, show_message=True)
                set_batch_json_and_queue(raw, loaded_from, cloud_mode)
            except Exception as e:  # noqa: BLE001
                st.error(f"批次策略讀取失敗：{e}")



    st.button(
        "恢復預設設定",
        on_click=reset_defaults,
        use_container_width=True,
        help="重設介面、圖表、成本與單次策略設定。",
    )

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
        with st.container(border=True):
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
        "cost": cost, "initial_capital": batch_initial_capital, "symbol": symbol,
        "d_start": d_start, "d_end": d_end, "n_bars": len(data),
        "cont": cont, "roll_log": roll_log,
        "strat": copy.deepcopy(strat), "hash": settings_hash(),
        "ui_mode": usage_profile,
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
        batch_obj = json.loads(text)
        batch_initial_capital = (float(batch_obj.get("initial_capital"))
                                 if isinstance(batch_obj, dict) and batch_obj.get("initial_capital") is not None
                                 else float(initial_capital))
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
                             quantity=cost.quantity, initial_capital=batch_initial_capital,
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
st.markdown(
    f'''<div class="app-hero">
      <div class="eyebrow">MTX STRATEGY LAB <span class="version-pill">{APP_VERSION}</span></div>
      <div class="title">台指期策略回測工作台</div>
      <div class="desc">單次策略調整、批次研究與前後期行情對照集中在同一介面；設定變更後，只有按下執行才會重新計算。</div>
    </div>''',
    unsafe_allow_html=True,
)

bc1, bc2, bc3 = st.columns([1.2, 1.2, 2.6])
if bc1.button(
    "調整單次策略",
    type="primary",
    use_container_width=True,
    help="開啟進場、出場與條件參數面板。",
):
    strategy_dialog()
main_run = bc2.button(
    "執行單次回測",
    type="primary",
    use_container_width=True,
    help="以目前的策略、資料與成本設定執行。",
)
with bc3:
    st.markdown(
        '<div class="action-note">批次策略由左側「雲端批次策略」執行；單次回測用來檢視目前面板中的策略。</div>',
        unsafe_allow_html=True,
    )

if mobile_mode:
    st.markdown("""
    <div class="mobile-quick-card">
      <b>手機快速操作</b><br>
      直接執行左側已選取的雲端策略，預設跑前後期行情對照。
    </div>
    """, unsafe_allow_html=True)
    mq1, mq2 = st.columns(2)
    if mq1.button(
        "開始雲端批次回測",
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
            padding:0 .65rem;border:1px solid #efcdb6;border-radius:12px;background:#fff;
            color:#44515c;font-weight:600;text-align:center;overflow:hidden;white-space:nowrap;
            text-overflow:ellipsis;" title="{loaded_strategy_name}">
            {loaded_strategy_name}</div>""",
            unsafe_allow_html=True,
        )
    with st.expander("手機上傳策略 JSON", expanded=False):
        mobile_up = st.file_uploader("上傳策略 JSON 後直接跑前後期行情對照", type=["json"], key="mobile_batch_json_uploader")
        if mobile_up is not None and st.button("執行上傳策略", use_container_width=True, key="mobile_uploaded_run_btn"):
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

# 目前單次策略摘要（預設收合，避免主畫面資訊過量）
with st.expander("目前單次策略摘要", expanded=False):
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
    st.info("尚未執行單次回測。桌機可按「執行單次回測」；手機建議使用上方的雲端批次操作。")
    st.stop()

if bt["hash"] != settings_hash():
    st.warning("設定已變更，以下為【上次回測】的結果；按「執行單次回測」以套用新設定。")

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
                    "風險上限跳過進場次數", "ATR缺值跳過進場次數",
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
