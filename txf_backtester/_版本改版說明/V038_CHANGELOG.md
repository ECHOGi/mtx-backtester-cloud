# V038_CHANGELOG

## 版本定位
v0.3.8：AI 分析包自動保存到 MTX Test Record。

## 本次目標
將回測結果的「AI 分析包 ZIP」保留原本下載按鈕，同時在本機執行時自動另存一份到：

```text
G:\我的雲端硬碟\MTX Test Record
```

這個資料夾可作為後續檢討回測結果、Obsidian 專案連結與長期研究紀錄使用。

## 修改檔案
- `app.py`

## 新增邏輯
1. 新增 `DEFAULT_RECORD_DIR` 常數，預設路徑為 `G:\我的雲端硬碟\MTX Test Record`。
2. 新增 AI 分析包檔名產生邏輯，格式大致為：
   `MTX_回測分析包_起日_迄日_YYYYMMDD_HHMMSS.zip`
3. 新增自動保存函式，會在回測結果產生後，把 AI 分析包 ZIP 寫入指定資料夾。
4. 使用 `st.session_state["ai_pack_saved_hash"]` 控制：同一組回測結果只自動保存一次，避免畫面重整時重複產生檔案。
5. 保留 Streamlit 原本的「AI 分析包 ZIP」下載按鈕。

## 注意事項
Streamlit 的 `download_button` 是由瀏覽器控制下載位置，程式本身不能強制指定瀏覽器下載資料夾。因此本版採用「下載按鈕保留 + 本機自動另存一份」的方式達成需求。

若 `G:` 雲端硬碟尚未掛載、資料夾不存在或沒有寫入權限，平台會顯示警告；下載按鈕仍可使用。

## 驗證
- `python -m py_compile *.py`：PASS
- `python self_check_correctness.py`：PASS 13 cases

## 未修改
- 未改 `backtester.py`
- 未改 `strategies.py`
- 未改 `correctness.py`
- 未改 `self_check_correctness.py`
- 未改既有進出場邏輯
- 未改既有損益公式
- 未改 MTX prepared data 載入邏輯
