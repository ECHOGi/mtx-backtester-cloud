# V043_CHANGELOG.md

## v0.4.3：命令列批次回測入口

### 本版目的
建立不經過 Streamlit 網頁的批次回測入口，供後續測試 Codex 或本機 Python 自動循環使用。

### 新增
- `run_batch_backtest.py`

### 使用方式
指定批次策略 JSON：

```bash
python run_batch_backtest.py --batch sample_strategy_batch.json
```

未指定 `--batch` 時，會讀取：

```text
G:\我的雲端硬碟\MTX Test Record\_策略投放箱
```

中最新的 `.json` 檔。

### 輸出
輸出到：

```text
G:\我的雲端硬碟\MTX Test Record
```

並建立：

- `00_批次回測總覽.md`
- `batch_comparison.csv`
- 各策略資料夾
- 批次 ZIP 備份

### 未改動
- 未改交易邏輯。
- 未改 Streamlit 既有單次回測功能。
- 未改 correctness.py。

