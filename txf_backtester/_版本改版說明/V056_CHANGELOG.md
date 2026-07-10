# V056_CHANGELOG

## 版本
v0.5.6

## 主題
Streamlit Community Cloud 部署穩定性修正。

## 變更
- 將套件版本從浮動安裝改為穩定版本鎖定。
- 同步根目錄 `requirements.txt` 與 `txf_backtester/requirements.txt`。
- 保留 v0.5.5 Google Drive 自動上傳功能。
- 保留雲端資料路徑自動偵測。

## 原因
v0.5.5 在 Streamlit Cloud Python 3.12 環境仍出現 `Segmentation fault`，log 顯示套件已安裝成功，但執行階段崩潰。判斷應先固定 pandas / numpy / pyarrow / streamlit / google api 相關套件版本，避免雲端自動抓最新版導致底層相容性問題。
