# v0.3.7 變更紀錄：MTX prepared data 平台整合版

## 本版目標

讓 Streamlit 平台在 MTX prepared 資料已存在時，優先直接讀取：

`data/prepared/MTX_stable_rollover_daily.csv`

避免每次回測都重新載入 2015～2026 全商品原始 CSV。

## 修改檔案

- `app.py`

## 新增行為

1. 新增 MTX prepared data 載入輔助函式：
   - `prepared_mtx_path()`
   - `has_prepared_mtx()`
   - `_load_prepared_mtx()`

2. 修改 `cached_continuous()`：
   - 若商品為 `MTX` 且 `data/prepared/MTX_stable_rollover_daily.csv` 存在，直接讀取 prepared 日K資料。
   - 不再讀取全商品原始 CSV。
   - 其他商品或 prepared 檔不存在時，保留原本 raw CSV 載入流程。

3. MTX prepared 模式固定使用：
   - 一般盤 `regular`
   - 穩定換倉 `stable_rollover`
   - 換倉確認 3 天
   - 排除週契約

4. 若 MTX prepared 檔存在，資料設定區會提示平台正在直接讀取 prepared 檔。

## 未修改項目

本版未修改：

- `backtester.py`
- `strategies.py`
- `correctness.py`
- `self_check_correctness.py`
- `data_loader.py`
- `continuous_contract.py`
- 既有進場規則
- 既有出場規則
- 既有損益公式
- signal_exit 邏輯

## 驗證結果

- `python -m py_compile *.py`：通過
- `python self_check_correctness.py`：PASS 13 cases
- 使用 `data/prepared/MTX_stable_rollover_daily.csv` 跑 smoke test：PASS

## 交接事項

下一步可在實機環境啟動 Streamlit，確認畫面流程與按鈕操作體感。
本 sandbox 環境未安裝 Streamlit，因此本版僅完成編譯與 prepared MTX 回測流程 smoke test。
