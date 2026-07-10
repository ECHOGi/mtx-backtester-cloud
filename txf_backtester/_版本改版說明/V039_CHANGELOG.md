# v0.3.9 變更紀錄：Obsidian 回測紀錄版

## 本版目標

讓回測結果不只自動保存 ZIP，也能自動產生 Obsidian 可直接閱讀與整理的回測紀錄資料夾。

原本 v0.3.8 只會在：

```text
G:\我的雲端硬碟\MTX Test Record
```

保存一份 AI 分析包 ZIP。由於 Obsidian 不適合直接閱讀 ZIP，本版改為每次回測完成後自動建立一個資料夾，將 Markdown、CSV、JSON 與 ZIP 備份分開放好。

## 修改檔案

- `app.py`

## 新增行為

每次新的回測結果完成後，會在指定資料夾內建立：

```text
G:\我的雲端硬碟\MTX Test Record
  000_MTX回測索引.md
  MTX_回測紀錄_2015-01-05_2026-06-30_YYYYMMDD_HHMMSS\
    00_回測總覽.md
    AI_回測分析摘要.md
    trades.csv
    metrics.csv
    equity_curve.csv
    strategy_config.json
    MTX_回測分析包_2015-01-05_2026-06-30_YYYYMMDD_HHMMSS.zip
```

## Obsidian 用途

- `000_MTX回測索引.md`：總索引，每次回測新增一筆，可快速連到各次總覽。
- `00_回測總覽.md`：每次回測的主檔，含基本資訊、核心績效、出場原因統計、代表性交易、檢討欄位。
- `AI_回測分析摘要.md`：較完整的策略設定與 AI 分析摘要。
- `trades.csv`、`metrics.csv`、`equity_curve.csv`、`strategy_config.json`：輔助分析資料。
- ZIP：完整備份與轉交其他 AI 使用。

## 未更動項目

本版未修改：

- `backtester.py`
- `strategies.py`
- `correctness.py`
- `self_check_correctness.py`
- `data_loader.py`
- `continuous_contract.py`
- MTX prepared data 載入邏輯
- 進出場規則
- 損益公式
- signal_exit correctness

## 驗證

```bash
python -m py_compile *.py
python self_check_correctness.py
```

結果：

```text
PASS 13 cases
```
