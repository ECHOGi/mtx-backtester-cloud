# v0.5.4 雲端作業模式部署說明

本版目標是讓平台可以放到能執行 Python 的私人雲端空間，並可用手機操作。

## 已完成

- 預設「雲端作業模式」：結果不再強制寫入本機 Google Drive 路徑。
- 結果區提供：下載 ZIP、複製總覽 Markdown。
- 新增「手機精簡介面」：主畫面提供雲端前後期對照、內建 batch_009、手機上傳 JSON 快捷操作。
- 保留「本機桌機模式」：桌機仍可使用原本完整介面與本機投放箱。
- 附 Dockerfile / Procfile，方便之後部署到能跑 Python 的主機。

## 注意

這仍然是 Streamlit/Python 平台，不是純 HTML 靜態網站。部署空間必須能執行 Python、安裝 requirements.txt，且需要伺服器端能讀取 data/prepared 內的 MTX prepared 資料。

## 建議手機使用流程

1. 側欄選「雲端作業模式」。
2. 側欄選「手機精簡介面」。
3. 主畫面按「雲端前後期對照」或「內建 batch_009」。
4. 結果產生後，下載 ZIP 或複製總覽 Markdown 給 AI 分析。
