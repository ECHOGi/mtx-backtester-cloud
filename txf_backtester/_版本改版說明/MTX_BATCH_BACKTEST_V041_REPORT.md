# MTX_BATCH_BACKTEST_V041_REPORT

## 本版目標

v0.4.1 只新增「策略批次回測」。

一次最多可載入 10 組策略 JSON，平台逐組回測，產生批次比較表與 Obsidian 批次紀錄。

## 本版沒有修改

- 沒有修改 backtester.py
- 沒有修改 correctness.py
- 沒有修改 self_check_correctness.py
- 沒有修改交易進出場優先序
- 沒有修改斷頭強制平倉規則
- 沒有修改損益公式

## 修改檔案

- `app.py`

## 新增檔案

- `sample_strategy_batch.json`
- `V041_CHANGELOG.md`
- `MTX_BATCH_BACKTEST_V041_REPORT.md`

## 批次 JSON 限制

一次最多 10 組策略。

超過 10 組時，平台會拒絕執行並提示錯誤。

## 批次回測輸出

畫面會顯示批次比較表。

同時自動保存到：

`G:\我的雲端硬碟\MTX Test Record`

批次資料夾內包含：

- `00_批次回測總覽.md`
- `batch_comparison.csv`
- 每一組策略的獨立資料夾與結果檔
- 批次 ZIP 備份

## 驗證結果

```text
python -m py_compile *.py
通過
```

```text
python self_check_correctness.py
PASS 16 cases
```

範例 2 組策略 smoke test：

| 策略 | 交易次數 | 總損益(元) | 斷頭次數 |
|---|---:|---:|---:|
| default | 142 | 282250 | 0 |
| stop150_tp300 | 142 | 136150 | 0 |

