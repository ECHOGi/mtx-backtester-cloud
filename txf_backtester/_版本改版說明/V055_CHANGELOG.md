# V0.5.5 更新紀錄｜Google Drive 自動上傳優先版

## 版本定位

本版重點是讓 Streamlit Community Cloud 跑完回測後，可以自動把結果上傳到 Google Drive，避免使用者下載 ZIP 或複製總覽文字後再貼回對話。

## 主要修改

1. 雲端資料路徑自動偵測
   - Streamlit Cloud 預設優先讀取 `txf_backtester/data`。
   - 本機執行則仍可讀取 `data`。
   - 偵測到 prepared MTX 日K資料時，會直接讀取 `prepared/MTX_stable_rollover_daily.csv`。

2. Google Drive 自動上傳
   - 雲端作業模式下，批次回測／前後期行情對照／單次回測分析包完成後，會優先嘗試上傳到 Google Drive。
   - 預設目標資料夾為 `MTX Test Record / _批次回測結果`。
   - 上傳內容包含完整 ZIP 備份與 ZIP 內的 Markdown / CSV / JSON 展開檔案，方便後續 AI 直接讀取。

3. Streamlit Secrets 支援
   - 支援 `[gcp_service_account]` 區塊。
   - 也支援 `GDRIVE_SERVICE_ACCOUNT_JSON` 一整段 JSON 字串。
   - 可用 `GDRIVE_RESULTS_PARENT_FOLDER_ID` 覆蓋預設上傳目標資料夾。

4. 保留本機模式
   - 本機作業模式仍維持原本的 Obsidian / Google Drive 桌面版資料夾寫入方式。

## 注意

本版程式已具備自動上傳功能，但 Streamlit Cloud 必須先設定 Google service account secrets，且必須把 Google Drive 目標資料夾分享給該 service account email，否則平台會顯示「尚未設定」或「權限不足」。
