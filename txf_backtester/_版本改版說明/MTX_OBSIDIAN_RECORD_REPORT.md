# MTX Obsidian 回測紀錄整合報告（v0.3.9）

## 本次目的

使用者已將 Obsidian 專案指向：

```text
G:\我的雲端硬碟\MTX Test Record
```

但 v0.3.8 只會自動保存 ZIP。Obsidian 對 ZIP 只能看到封存檔，無法直接閱讀、連結、搜尋與檢討內容。

本次改版目標是：

> 回測完成後，自動建立 Obsidian 可直接閱讀的回測紀錄資料夾。

## 修改內容

主要修改 `app.py` 的匯出區塊。

原本行為：

```text
自動保存 AI 分析包 ZIP 到 MTX Test Record 根目錄
```

新版行為：

```text
自動建立一個回測紀錄資料夾
將 ZIP 解出 Markdown / CSV / JSON
建立 00_回測總覽.md
更新 000_MTX回測索引.md
仍保留 AI 分析包 ZIP 下載按鈕與 ZIP 備份
```

## 新的資料夾結構

每次回測會產生類似：

```text
G:\我的雲端硬碟\MTX Test Record
  000_MTX回測索引.md
  MTX_回測紀錄_2015-01-05_2026-06-30_20260709_115900\
    00_回測總覽.md
    AI_回測分析摘要.md
    trades.csv
    metrics.csv
    equity_curve.csv
    strategy_config.json
    MTX_回測分析包_2015-01-05_2026-06-30_20260709_115900.zip
```

## Obsidian 主要閱讀檔

### 1. `000_MTX回測索引.md`

放在 `MTX Test Record` 根目錄。用途是總索引，每次回測新增一列：

- 建立時間
- 連到該次 `00_回測總覽.md`
- 總損益
- 交易次數
- 勝率
- 最大回撤
- 狀態

### 2. `00_回測總覽.md`

每次回測資料夾內的主檔，包含：

- 基本資訊
- 核心績效
- 出場原因統計
- 檢討欄位
- 最大單筆獲利
- 最大單筆虧損
- 相關附件連結
- 下次可問 AI 的問題

### 3. `AI_回測分析摘要.md`

保留原本 AI 分析包內的完整摘要，可直接在 Obsidian 閱讀。

## 保留項目

- 原本的「AI 分析包 ZIP」下載按鈕仍保留。
- 每次回測資料夾內仍保存完整 ZIP 備份。
- 不影響瀏覽器手動下載。

## 未修改項目

本版沒有修改任何交易核心：

- 進場規則未改
- 出場規則未改
- signal_exit 未改
- correctness 未改
- 損益公式未改
- MTX prepared data 載入邏輯未改

## 驗證結果

執行：

```bash
python -m py_compile *.py
python self_check_correctness.py
```

結果：

```text
PASS 13 cases
```

## 注意事項

目前 sandbox 環境沒有安裝 Streamlit，也沒有 Windows 的 `G:` 雲端硬碟，因此無法在此環境實際打開網頁測試 `G:\我的雲端硬碟\MTX Test Record` 寫入。

但已完成：

- Python 語法編譯檢查
- correctness 自檢
- 保存邏輯以本機檔案寫入方式設計

使用者在 Windows 本機啟動後，若 `G:` 雲端硬碟已掛載，回測完成後應會自動建立 Obsidian 紀錄資料夾。
