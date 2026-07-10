# v0.5.5｜Streamlit Cloud 自動上傳 Google Drive 設定

## 目標

讓 Streamlit 網站跑完回測後，自動把結果存到：

`MTX Test Record / _批次回測結果`

上傳後會建立一個新的結果資料夾，內含：

- 完整 ZIP 備份
- `00_前後期行情對照總覽.md`
- `market_phase_comparison.csv`
- 各策略的 `trades.csv`
- 各策略的 `metrics.csv`
- 各策略的 `strategy_config.json`

後續 AI 就能直接讀 Google Drive 結果，不必再下載 ZIP 或複製總覽文字。

---

## 需要做的設定

### 1. 建立 Google Cloud Service Account

在 Google Cloud Console 建立一個 service account，並產生 JSON key。

### 2. 分享 Google Drive 資料夾

把你的 Drive 資料夾：

`MTX Test Record / _批次回測結果`

分享給 service account 的 email，權限給「編輯者」。

service account email 會長得像：

`xxxx@xxxx.iam.gserviceaccount.com`

### 3. 在 Streamlit Community Cloud 設定 Secrets

進入 Streamlit app：

`Settings → Secrets`

貼上以下格式：

```toml
GDRIVE_RESULTS_PARENT_FOLDER_ID = "1KhjGNzHqPTXzIcDEM_fy0clOCZoy25Fa"

[gcp_service_account]
type = "service_account"
project_id = "你的 project_id"
private_key_id = "你的 private_key_id"
private_key = "-----BEGIN PRIVATE KEY-----\n你的金鑰內容\n-----END PRIVATE KEY-----\n"
client_email = "你的-service-account@你的-project.iam.gserviceaccount.com"
client_id = "你的 client_id"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "你的 client_x509_cert_url"
universe_domain = "googleapis.com"
```

儲存後，重新啟動 Streamlit app。

---

## 驗證方式

1. 打開 Streamlit 網站。
2. 使用「雲端作業模式」。
3. 跑一次前後期行情對照。
4. 畫面若顯示「已自動上傳...到 Google Drive」，代表成功。
5. 回到 Google Drive 的 `_批次回測結果` 檢查是否出現新的結果資料夾。

---

## 如果失敗

常見原因：

1. Streamlit Secrets 沒貼完整。
2. private_key 裡的 `\n` 換行格式錯誤。
3. Google Drive 目標資料夾沒有分享給 service account email。
4. `GDRIVE_RESULTS_PARENT_FOLDER_ID` 填錯。
5. Google Drive API 尚未在 Google Cloud 專案中啟用。
