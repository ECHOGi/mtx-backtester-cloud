# MTX_STRATEGY_DROPBOX_V042_V043_REPORT.md

## 結論

本次完成兩層功能：

1. v0.4.2：策略投放箱版
2. v0.4.3：命令列批次回測入口

## v0.4.2 策略投放箱

平台新增策略投放箱：

```text
G:\我的雲端硬碟\MTX Test Record\_策略投放箱
```

使用方式：

1. 把批次策略 JSON 放進 `_策略投放箱`。
2. 開啟 Streamlit 平台。
3. 在側欄「批次策略回測」中選擇投放箱 JSON。
4. 按「讀取投放箱策略 JSON」。
5. 按「批次回測」。

這樣不需要 Codex，也不需要瀏覽器手動上傳 JSON。

## v0.4.3 命令列批次回測

新增：

```text
run_batch_backtest.py
```

使用方式：

```bash
python run_batch_backtest.py --batch sample_strategy_batch.json
```

或不指定 batch，直接讀取策略投放箱內最新 JSON：

```bash
python run_batch_backtest.py
```

用途是給後續測試 Codex / 本機 Python 自動循環使用。

## 修改檔案

- app.py
- run_batch_backtest.py
- V042_CHANGELOG.md
- V043_CHANGELOG.md
- MTX_STRATEGY_DROPBOX_V042_V043_REPORT.md

## 未修改

- backtester.py
- correctness.py
- self_check_correctness.py
- metrics.py
- strategies.py
- 交易邏輯
- 斷頭強制平倉規則
- 損益公式

## 驗證

```text
python -m py_compile *.py
通過
```

```text
python self_check_correctness.py
PASS 16 cases
```

命令列批次回測 smoke test：

```text
python run_batch_backtest.py --batch sample_strategy_batch.json --record-dir /mnt/data/v043_cli_test
PASS
```
