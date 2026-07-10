# MTX prepared 資料 smoke test

## 測試目的

確認 `data/prepared/MTX_stable_rollover_daily.csv` 可以被現有策略與回測引擎直接使用。

## 測試設定

- 資料：`data/prepared/MTX_stable_rollover_daily.csv`
- 日 K 筆數：2,798
- 策略：既有 `StrategyParams()` 預設策略
- 回測成本：既有 `CostModel()` 預設值

## 結果

| 項目 | 結果 |
|---|---:|
| 回測資料列數 | 2,798 |
| 交易筆數 | 142 |
| correctness 總檢查項目 | 1,717 |
| correctness 失敗項目 | 0 |
| 結論 | PASS |

## 出場原因分布

| 出場原因 | 筆數 |
|---|---:|
| fixed_stop | 86 |
| macd_reverse | 43 |
| chandelier | 13 |

## 判斷

MTX prepared stable_rollover 檔案可以直接進入既有策略與 backtester 流程。下一步可改 Streamlit 資料載入流程，讓平台優先讀取 prepared 檔。
