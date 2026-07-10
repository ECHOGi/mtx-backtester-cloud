# V041_CHANGELOG — 策略批次回測版

## 版本定位
v0.4.1 聚焦新增「策略批次回測」功能。

本版不修改交易邏輯、不修改 backtester、不修改 correctness、不修改斷頭強制平倉規則。

## 新增功能

### 1. 批次策略 JSON 載入
在側欄「參數檔／進階設定」新增：

- 載入批次策略 JSON（最多10組）
- ▶ 批次回測（最多10組）

支援格式：

```json
[
  {"name": "策略01", "entry_long": [], "entry_short": [], "exit": {}},
  {"name": "策略02", "entry_long": [], "entry_short": [], "exit": {}}
]
```

或：

```json
{
  "batch_name": "第一批策略測試",
  "strategies": [
    {"name": "策略01", "config": {}},
    {"name": "策略02", "config": {}}
  ]
}
```

### 2. 批次比較表
批次回測後在畫面顯示比較表，欄位包含：

- 策略編號
- 策略名稱
- 總損益(元)
- 總報酬率(%)
- 最大回撤(元)
- 交易次數
- 勝率(%)
- 是否曾發生斷頭
- 斷頭次數
- 第一次斷頭日期

### 3. Obsidian 批次回測資料夾
批次回測後會自動在：

`G:\我的雲端硬碟\MTX Test Record`

建立批次回測資料夾，內容包含：

- `00_批次回測總覽.md`
- `batch_comparison.csv`
- 每一組策略各自的資料夾
  - `00_策略回測摘要.md`
  - `trades.csv`
  - `metrics.csv`
  - `equity_curve.csv`
  - `strategy_config.json`
- 批次回測 ZIP 備份

### 4. 範例批次 JSON
新增：

- `sample_strategy_batch.json`

可用來測試批次回測功能。

## 驗證

- `python -m py_compile *.py`：通過
- `python self_check_correctness.py`：PASS 16 cases
- 2 組範例策略 smoke test：可正常跑完

