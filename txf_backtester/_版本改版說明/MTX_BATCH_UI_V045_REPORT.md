# MTX_BATCH_UI_V045_REPORT.md

## 版本

v0.4.5：批次回測操作流程修正版

## 本次目標

修正 v0.4.4 批次回測左側操作不直覺的問題。

## 使用方式

### 策略投放箱模式

只要 `_策略投放箱` 裡有批次策略 JSON，左側會顯示目前選擇的檔案，並提供：

`🚀 讀取投放箱並批次回測（最多10組）`

這個按鈕會一次完成讀取與批次回測。

### 手動上傳模式

手動上傳 JSON 後，會顯示已載入檔名，並啟用：

`▶ 手動批次回測（最多10組）`

## 沒有修改

本版沒有修改：

- backtester.py
- correctness.py
- self_check_correctness.py
- 交易邏輯
- 出場優先序
- 損益公式
- 斷頭強制平倉規則

## 驗證結果

```text
python -m py_compile *.py
通過
```

```text
python self_check_correctness.py
PASS 16 cases
```

```text
python run_batch_backtest.py --batch sample_strategy_batch.json --record-dir /mnt/data/v045_smoke
PASS：批次回測完成
```
