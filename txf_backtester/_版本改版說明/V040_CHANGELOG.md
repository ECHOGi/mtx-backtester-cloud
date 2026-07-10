# V040_CHANGELOG.md

## v0.4.0：斷頭強制平倉檢查版

本版維持既有策略、UI 結構與 MTX prepared data 流程，只加入一口小台概略模式的安全資金檢查，並修正啟動 bat 重複開頁問題。

## 修改重點

1. 修正 `啟動台指期回測工具.bat` 與 `START_BACKTESTER.cmd`
   - Streamlit 改以 `--server.headless true` 啟動。
   - 由 bat 延遲開啟一次 `http://localhost:8501`，避免 Streamlit 與 bat 各開一次網頁。

2. 新增一口 MTX 安全資金顯示
   - 原始保證金：159,000 元。
   - 安全緩衝金額：依近 250 日高點的 25% 壓力估算。
   - 安全資金：原始保證金 + 安全緩衝金額。
   - 顯示於側欄「交易成本與資金設定」。

3. 新增斷頭強制平倉邏輯
   - 定義：持倉期間反向浮動損失 >= 安全緩衝金額。
   - 出場原因：`margin_call`。
   - 中文顯示：斷頭強制平倉。
   - 此斷頭不是券商維持保證金判斷，而是本專案定義的安全資金緩衝被吃光。

4. 新增統計
   - 是否曾發生斷頭。
   - 斷頭次數。
   - 第一次斷頭日期。
   - 歷史最低所需安全資金。

5. Obsidian 總覽加入斷頭統計
   - `00_回測總覽.md` 新增「斷頭強制平倉檢查」。
   - `000_MTX回測索引.md` 新增斷頭次數欄位。

## 驗證

- `python -m py_compile *.py`：PASS
- `python self_check_correctness.py`：PASS 16 cases
- MTX prepared data smoke test：PASS
  - 交易筆數：142
  - 斷頭次數：0
  - correctness：PASS，1,717 checks，0 failed
