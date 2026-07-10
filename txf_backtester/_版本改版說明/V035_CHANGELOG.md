# v0.3.5 條件目錄重構版

self_check_correctness.py 仍 PASS 8 cases。backtester/correctness/data_loader/
continuous_contract 未動；condition_blocks.py 僅增量註冊 12 個新條件。

## 修改內容

1. 「曾經滿足」「排除」改為勾選式（與「滿足」一致），移除文字輸入式下拉。
2. 條件改為「類型 → 型態」二層勾選：先勾類型（MACD／布林通道／均線／KD／
   RSI／成交量），該類型的型態勾選格才會展開；類型旁顯示已選數量（✓n）。
   類型取消勾選時，其底下型態不會被計入回測（實測驗證）。
3. 型態大幅補齊（共 39 種，多空通用，進場/前提/排除/條件出場皆可選）：
   - MACD 8 種：柱狀圖翻多/翻空/為正/為負、DIF黃金/死亡交叉、DIF零軸上/下
   - 布林通道 10 種：中線上/下、突破/跌破中線、上軌上/下軌下、
     突破上軌、跌回上軌下、站回下軌上、跌破下軌
   - 均線 8 種：站上/跌破均線、黃金/死亡交叉、多頭/空頭排列、翻揚/下彎
   - KD 6 種：黃金/死亡交叉、K在D上/下、低檔超賣/高檔超買
   - RSI 4 種：高於/低於門檻、上穿/下穿門檻
   - 成交量 3 種：量增(勝量均)、量縮(低量均)、單日爆量(勝昨量)
   （新增 12 個條件函式：macd_hist_positive/negative、macd_dif_above/below_zero、
   ma_slope_up/down、kd_k_above_d/below_d、rsi_cross_up/down、volume_above_prev）
4. 面板改為即時互動（dialog fragment）：勾類型立即展開型態格；
   仍只有按「▶ 開始回測」才會執行回測，「取消」不套用。
5. 修正面板被 X 關閉後重開顯示空白的問題（哨兵鍵自動重灌暫存值）。
6. 舊策略 JSON 的條件 key 自動遷移（OLD_KEY_MAP），載入不中斷。

## 交接備註
- correctness.py 逐筆驗證尚未涵蓋「signal_exit 條件出場」，請下一位接手補上。
- 資料夾若加入新月份 CSV（如 2026-01.csv），loader 會自動納入，
  回測起訖日期與 K 棒數會隨之增加，屬正常行為。
