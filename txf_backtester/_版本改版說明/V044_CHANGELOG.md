# V044_CHANGELOG.md

## v0.4.4：策略投放箱路徑與批次結果修正版

本版針對 v0.4.3 測試回報修正三項問題：

1. **策略投放箱路徑偵測修正**
   - 原本固定使用 `G:\我的雲端硬碟\MTX Test Record`。
   - 新版會自動偵測：
     - `C:\Users\<使用者>\我的雲端硬碟\MTX Test Record`
     - `C:\Users\PG\我的雲端硬碟\MTX Test Record`
     - `G:\我的雲端硬碟\MTX Test Record`
   - 也支援環境變數 `MTX_TEST_RECORD_DIR` 指定路徑。

2. **手動批次回測按鈕顯示強化**
   - 手動載入批次策略 JSON 後，`▶ 批次回測（最多10組）` 會改為主要按鈕樣式。

3. **批次結果紅字錯誤修正**
   - 修正 `_safe_filename_part` 定義位置造成的 `NameError`。
   - 批次 ZIP 與 Obsidian 批次紀錄可正常產生。

4. **批次比較表新增欄位**
   - 新增 `年化報酬率(%)`。

## 驗證

- `python -m py_compile *.py`：通過
- `python self_check_correctness.py`：PASS 16 cases
- `python run_batch_backtest.py --batch sample_strategy_batch.json --record-dir <測試資料夾>`：通過
