# V045_CHANGELOG.md

## v0.4.5：批次回測操作流程修正版

本版只調整批次回測操作流程，不修改交易邏輯、回測公式、出場優先序或 correctness 規則。

### 修改重點

1. 策略投放箱有 JSON 時，左側會直接顯示：
   - 策略投放箱已連線
   - 找到幾個 JSON
   - 目前選擇的 JSON 檔名

2. 新增主要紅色按鈕：

   `🚀 讀取投放箱並批次回測（最多10組）`

   點一次即完成：
   - 讀取投放箱 JSON
   - 啟動批次回測

3. 手動上傳 JSON 區塊獨立顯示：
   - 未上傳時，手動批次回測按鈕停用
   - 上傳後，手動批次回測按鈕變成主要按鈕

### 驗證

- `python -m py_compile *.py`：通過
- `python self_check_correctness.py`：PASS 16 cases
- `python run_batch_backtest.py --batch sample_strategy_batch.json --record-dir <測試資料夾>`：通過
