# txf_backtester v0.3 變更紀錄

定位：回測正確性強化版。未新增策略條件 UI，未重寫專案架構。

## 修改重點

1. `backtester.py`
   - 交易明細新增：
     - `signal_date`
     - `signal_bar_index`
     - `entry_execution_date`
     - `entry_bar_index`
     - `exit_bar_index`
     - `entry_reason`
   - 保留舊欄位 `entry_date`，並讓它等於實際進場執行日，避免 Streamlit 舊畫圖與匯出流程失效。
   - 進場 pending 訊號改為保存訊號日期、訊號列位置與進場條件說明。

2. `condition_blocks.py`
   - 新增 `evaluate_block_with_reasons()`。
   - 原本 `evaluate_block()` 行為不變，只是內部共用新函式。
   - 可把每根 K 棒成立的條件積木轉成交易明細文字。

3. `strategies.py`
   - 策略產生訊號時，同步產生：
     - `long_entry_reasons`
     - `short_entry_reasons`
   - 不改變原有進場邏輯。

4. 新增 `correctness.py`
   - 回測完成後檢查交易明細是否符合規則。
   - 檢查項目包含：
     - 下一根開盤進場
     - 進場價
     - 出場日與出場原因
     - 出場價
     - 損益點數
     - 損益金額
     - 進場條件是否有留下說明
     - 移動停損是否避免偷看當根高低點

5. 新增 `self_check_correctness.py`
   - 可執行的合成資料測試。
   - 執行：

```bash
python self_check_correctness.py
```

## 已完成測試

### 合成測試

`python self_check_correctness.py`

通過 8 個案例：

1. 下一根開盤進場
2. 固定停損
3. 固定停利
4. 同根 K 棒停損/停利同時觸發時，固定停損優先
5. 跳空停損使用開盤價
6. 移動停損不偷看當根高點/低點
7. MACD 反向出場
8. 吊燈出場

### 真實資料測試

使用 v0.2 patch2 產出的 MTX 真實連續契約資料：

- 期間：2015-01-05 ～ 2025-12-31
- 連續契約筆數：2,682
- 策略：MACD + Bollinger + Chandelier 預設參數
- 交易次數：136
- 正確性檢查項目：1,645
- 失敗項目：0
- 結論：PASS

輸出檔案位於：

```text
output_v03/
```

包含：

- `trades_MTX_default_v03.csv`
- `equity_MTX_default_v03.csv`
- `metrics_MTX_default_v03.csv`
- `correctness_checks_MTX_default_v03.csv`
- `BACKTEST_CORRECTNESS_REPORT_v03.md`
- `strategy_default_MTX_v03.json`

## 注意事項

- v0.3 尚未新增策略條件 UI。
- v0.3 主要目標是讓交易明細更可追蹤、可驗證。
- Streamlit UI 的交易明細會自動顯示新增欄位；未重做版面。
