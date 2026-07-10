# TXF Backtester v0.5.6 更新說明

## 修正目的

v0.5.5 部署到 Streamlit Community Cloud 後，雖然已切回 Python 3.12，但仍因套件版本浮動安裝到過新的版本，導致 Streamlit 執行環境發生 `Segmentation fault`。

v0.5.6 主要不是策略邏輯改版，而是雲端部署穩定性修正。

## 本版修正

1. 將 `requirements.txt` 改為穩定版本鎖定。
2. 同步更新根目錄與 `txf_backtester/requirements.txt`，避免 Streamlit Cloud 偵測到兩份 requirements 時使用到不同版本。
3. 保留 v0.5.5 的 Google Drive 自動上傳功能。
4. 保留雲端資料路徑自動偵測。

## GitHub 更新方式

請在 GitHub repo 根目錄上傳並覆蓋：

- `txf_backtester/`
- `requirements.txt`
- `README_UPDATE_v056.md`

上傳後按 `Commit changes`，Streamlit Community Cloud 會自動重新部署。

## Streamlit Cloud 設定提醒

Python version 請維持：

```text
Python 3.12
```

部署成功後，再繼續設定 Google Drive Service Account 與 Streamlit Secrets。
