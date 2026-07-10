# MTX Test Record 自動保存整合報告

## 本次需求
使用者希望將回測結果下載的 ZIP 檔，預設整理到：

```text
G:\我的雲端硬碟\MTX Test Record
```

使用者也已在 Obsidian 建立一個指向此資料夾的專案，後續可在該資料夾集中檢討回測結果。

## 實作方式
瀏覽器下載位置無法由 Streamlit `download_button` 強制指定，因此本次採用下列方式：

1. 保留原本「AI 分析包 ZIP」下載按鈕。
2. 回測結果產生後，本機自動另存一份同內容 ZIP 到 `G:\我的雲端硬碟\MTX Test Record`。
3. 若該資料夾不存在，程式會嘗試自動建立。
4. 若自動保存失敗，平台顯示警告，但下載按鈕仍可使用。
5. 同一組回測結果只保存一次，避免 Streamlit 畫面重整造成重複檔案。

## 檔名格式
自動保存與下載按鈕使用相同檔名，格式大致如下：

```text
MTX_回測分析包_2015-01-05_2026-06-30_20260709_153000.zip
```

實際時間戳依使用者執行回測當下產生。

## 修改檔案
- `app.py`

## 重要新增項目
- `DEFAULT_RECORD_DIR = r"G:\我的雲端硬碟\MTX Test Record"`
- `_safe_filename_part()`
- `build_ai_pack_filename()`
- `save_ai_pack_to_record_folder()`
- `st.session_state["ai_pack_saved_hash"]` 防止同結果重複保存

## 驗證結果

```text
python -m py_compile *.py
PASS
```

```text
python self_check_correctness.py
PASS 13 cases
```

## 使用者本機測試方式
1. 解壓本版 ZIP。
2. 確認 Google Drive 的 `G:` 磁碟存在。
3. 確認資料夾 `G:\我的雲端硬碟\MTX Test Record` 存在；若不存在，程式會嘗試建立。
4. 啟動平台。
5. 按「開始回測」。
6. 到 `G:\我的雲端硬碟\MTX Test Record` 檢查是否產生 `MTX_回測分析包_...zip`。
7. Obsidian 專案即可引用該資料夾內的回測紀錄。

## 後續建議
下一步可考慮在 AI 分析包內新增固定檔名的「回測檢討模板.md」，讓每次回測結果都能直接在 Obsidian 裡用相同格式檢討。
