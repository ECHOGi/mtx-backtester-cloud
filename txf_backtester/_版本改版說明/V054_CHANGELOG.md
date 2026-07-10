# V054_CHANGELOG

## 版本
v0.5.4

## 主題
雲端作業模式與手機精簡介面。

## 重點修改

1. 新增「作業環境」選項：
   - 雲端作業模式：預設；不依賴本機 Google Drive 路徑，結果以下載 ZIP／複製總覽文字為主。
   - 本機桌機模式：保留原本自動保存到 Obsidian / Google Drive 同步資料夾的行為。

2. 新增「畫面配置」選項：
   - 桌機完整介面：保留原本桌機操作。
   - 手機精簡介面：主畫面提供大按鈕快速操作。

3. 手機精簡介面新增：
   - 雲端前後期對照。
   - 內建 batch_009 前後期對照。
   - 手機上傳策略 JSON 後直接跑前後期行情對照。

4. 結果區新增：
   - 複製給 AI 的前後期行情對照總覽 Markdown。
   - 複製給 AI 的批次回測總覽 Markdown。

5. 啟動器修正：
   - 優先檢查 Python 3.12，避免誤用過新的 Python 3.15 導致 pandas/numpy 編譯失敗。

6. 部署支援：
   - 新增 Dockerfile。
   - 新增 Procfile。
   - 新增 README_DEPLOY_v054.md。

## 備註
本版仍是 Streamlit/Python 平台；若部署到網站空間，該空間必須支援 Python 程式執行，不是純靜態 HTML 部署。
