# v0.3.1 使用便利性小修

本版只做介面與使用便利性調整，未修改回測核心邏輯。

## 修改內容

1. 介面中文化
   - 策略名稱改為中文顯示：`MACD＋布林線＋吊燈出場`。
   - 圖例與交易紀錄部分文字改為中文。
   - 出場原因顯示為中文，例如「固定停損」、「MACD 反向出場」。

2. 操作區塊重排
   - 左側欄位改為：
     1. 策略設定
     2. 策略參數
     3. 出場條件
     4. 圖表顯示
     5. 資料設定
     6. 成本設定
     7. 參數檔

3. 圖表顯示控制
   - 新增獨立的「圖表顯示」勾選區。
   - 可分別控制：K 線、進出場標記、布林通道、吊燈線、均線、成交量、MACD、KD、權益曲線。
   - 這些勾選只影響畫面，不影響回測結果。

4. 回測摘要資訊卡片
   - 將「商品、策略、期間、K 棒數量」改為主畫面上方資訊卡片。
   - 避免被 Streamlit 上方 Deploy / 部署列遮住。

5. 新增雙擊啟動檔
   - 新增 `啟動台指期回測工具.bat`。
   - 雙擊後會啟動 Streamlit 本機網頁工具。

6. 新增使用說明
   - 新增 `使用說明.md`。
   - 說明資料要放在 `data` 資料夾。
   - 說明使用期間不要關閉 CMD / PowerShell 視窗。

7. Streamlit 工具列設定
   - 新增 `.streamlit/config.toml`。
   - 嘗試將 Streamlit 工具列改為 viewer 模式，減少本機開發工具列干擾。

## 未修改內容

- 未修改 `backtester.py` 回測核心。
- 未修改 `correctness.py` 正確性檢查邏輯。
- 未修改 `data_loader.py` 資料清理核心。
- 未新增策略條件 UI。
- 未新增多策略管理器。
- 未改變 v0.3 的回測結果計算方式。

## 驗證

- `python -m py_compile *.py`：通過。
- `python self_check_correctness.py`：PASS 8 cases。

## v0.3.1 patch1

- Replaced the launcher BAT content with ASCII-only commands to avoid Windows CMD encoding problems on Chinese-language systems.
- Added `START_BACKTESTER.cmd` as an English filename backup launcher.
- The launcher now checks `app.py`, `requirements.txt`, Python launcher `py`, and Streamlit before starting.
- The launcher now pauses on errors so the error message will not disappear instantly.
