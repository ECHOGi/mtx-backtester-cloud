# v0.3.6 MTX prepared 資料層

## 本版目標

本版不重寫回測引擎、不改 Streamlit UI、不改策略條件選單。  
目標只有一個：先建立 MTX 回測專用 prepared 資料，避免平台每次載入 2015～2026 全商品原始大表。

## 新增內容

1. 新增 `prepare_mtx_data.py`
   - 從期交所 CSV 只抽取 MTX / MXF。
   - 只輸出一般盤 regular。
   - 不修改原始 CSV。
   - 輸出 prepared 檔案到 `data/prepared/`。

2. 新增 `data/prepared/` MTX 回測資料
   - `MTX_clean_regular_contracts.csv`
   - `MTX_stable_rollover_daily.csv`
   - `MTX_volume_max_daily.csv`
   - `MTX_oi_max_daily.csv`
   - `MTX_*_rollover_log.csv`
   - `MTX_prepare_file_summary.csv`
   - `MTX_prepare_method_summary.csv`
   - `MTX_prepared_default_backtest_summary.csv`

3. 新增 `MTX_PREPARED_DATA_REPORT.md`
   - 紀錄來源檔摘要、MTX 筆數、連續契約輸出檢查。

## 資料範圍

- 2015_fut.csv ～ 2025_fut.csv
- 2026-01.csv ～ 2026-06.csv
- 商品：MTX
- 時段：一般盤 regular

## 檢查結果

- MTX 清洗後契約資料：19,175 筆
- 三種連續契約日 K：各 2,798 筆
- 起訖日期：2015-01-05 ～ 2026-06-30
- 日期中斷 > 15 天：0
- 契約回跳：0

## 驗證

- `python -m py_compile *.py`：通過
- `python self_check_correctness.py`：PASS 13 cases
- 使用 `MTX_stable_rollover_daily.csv` 跑預設策略 smoke test：
  - 日 K：2,798 筆
  - 交易：142 筆
  - correctness 檢查：PASS，1,717 項，0 失敗

## 下一步交接

下一輪再做 Streamlit 整合：

1. 當商品為 MTX 且 `data/prepared/MTX_<method>.csv` 存在時，優先讀取 prepared 檔。
2. prepared 不存在時，不要直接載入全商品原始大表，應提示使用者先執行 `prepare_mtx_data.py`。
3. 暫時不要擴充 TX/TMF，維持 MTX-only，先完成平台機能穩定化。
