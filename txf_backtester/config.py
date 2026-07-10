# -*- coding: utf-8 -*-
"""
config.py - 全域設定
商品規格、CSV 欄位對應、預設參數都集中在這裡，程式中不寫死商品代碼。
每點價值 / 手續費 / 滑價可在 Streamlit 介面覆寫，這裡只是預設值。
"""

# ---------- 商品規格 ----------
SYMBOLS = {
    "MTX": {
        "name": "小型臺指期貨",
        "point_value": 50,          # 每點價值 (元)
        "fee": 20,                  # 單邊手續費 (元/口)
        "tax_rate": 0.00002,        # 期交稅率 (成交金額*稅率, 單邊)，先預留
        "slippage_points": 1.0,     # 單邊滑價 (點)
        "margin_reference": 159000,  # 一口原始保證金 (元)=預設初始資金，請依期交所公告調整
        "codes": ["MTX", "MXF"],    # CSV「契約」欄可能出現的代碼
        "keywords": ["小型臺指", "小型台指"],  # 若「契約」欄為中文名稱
    },
    "TMF": {
        "name": "微型臺指期貨",
        "point_value": 10,
        "fee": 12,
        "tax_rate": 0.00002,
        "slippage_points": 1.0,
        "margin_reference": 32000,   # 約小台1/5，請依期交所公告調整
        "codes": ["TMF"],
        "keywords": ["微型臺指", "微型台指"],
    },
    "TX": {
        "name": "臺股期貨(大台)",
        "point_value": 200,
        "fee": 50,
        "tax_rate": 0.00002,
        "slippage_points": 1.0,
        "margin_reference": 636000,  # 約小台4倍，請依期交所公告調整
        "codes": ["TX", "TXF"],
        "keywords": ["臺股期貨", "台股期貨"],
    },
}

DEFAULT_SYMBOL = "MTX"  # 第一版主要商品：小型台指期

# ---------- TAIFEX 每日行情 CSV 欄位對應 ----------
# key: CSV 中文欄位 (讀入時會先去除前後空白)，value: 標準欄位名
COLUMN_MAP = {
    "交易日期": "date",
    "契約": "contract",
    "到期月份(週別)": "contract_month",
    "到期月份（週別）": "contract_month",
    "開盤價": "open",
    "最高價": "high",
    "最低價": "low",
    "收盤價": "close",
    "成交量": "volume",
    "結算價": "settlement",
    "未沖銷契約數": "open_interest",
    "未沖銷契約量": "open_interest",
    "交易時段": "session",
}

# 缺少這些欄位會直接報錯（session / open_interest 允許缺漏，見 data_loader）
HARD_REQUIRED_COLUMNS = ["date", "contract", "contract_month",
                         "open", "high", "low", "close", "volume"]

# 交易時段值對應
SESSION_MAP = {
    "一般": "regular",
    "盤後": "after_hours",
}

# ---------- 其他預設 ----------
DEFAULT_MA_PERIODS = [5, 10, 20, 60, 120, 240]  # 常用均線週期
DATA_DIR = "data"      # 預設 CSV 資料夾
OUTPUT_DIR = "output"  # 匯出結果資料夾
DEFAULT_TIMEFRAME = "1D"  # 目前資料為日K；引擎不限日K，任何 OHLCV 皆可回測
