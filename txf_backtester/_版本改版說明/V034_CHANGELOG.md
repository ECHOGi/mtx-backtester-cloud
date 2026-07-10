# v0.3.4 kcTrader 式條件槽版

self_check_correctness.py 仍 PASS 8 cases。

## 修改內容

1. 進出場組合改為 kcTrader 式三種條件槽（皆為可選）：
   - 滿足：當根全部成立（AND），勾選格
   - 曾經滿足（前提）：所選條件在最近 N 根內至少成立過一次
     （rolling max，只用過去資料、無未來函數；N 可調，預設 10）
   - 排除：任一成立則不觸發
   多單/空單各 2 組（組合間 OR）。
2. 新增布林通道條件（進出場皆可用）：
   突破布林上軌、跌破布林下軌、站回布林下軌、跌回布林上軌下
   （condition_blocks.py 增量註冊 4 個交叉條件）。
3. 新增「條件出場」：多單/空單各一組（滿足/前提/排除），
   符合即收盤平倉，出場原因顯示「條件出場」。
   - backtester.py 僅增量加入 e2 檢查（params.use_signal_exit 預設 False，
     未啟用時完全不影響既有行為；合成資料驗證：啟用時正確於訊號日收盤出場，
     未啟用時行為與舊版相同）。
   - 出場優先序：停損 > 停利 > 移動停損 > 吊燈 > MACD反向 > 條件出場。
4. 策略 JSON 的 ui_combos 擴充（must/ever/ever_n/exclude 與 exit_long/exit_short），
   舊版 list 格式自動遷移。

## 已知note
- correctness.py 的逐筆驗證尚未涵蓋「條件出場」類型（不影響其餘檢查）。
