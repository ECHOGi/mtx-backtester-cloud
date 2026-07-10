# MTX_STRATEGY_DROPBOX_V044_FIX_REPORT.md

## 修正摘要

本版為 v0.4.3 的修正版，解決使用者換電腦後 Google Drive 同步路徑改變造成的策略投放箱讀取失敗問題。

## 1. 策略投放箱路徑

目前支援自動偵測：

```text
C:\Users\<使用者>\我的雲端硬碟\MTX Test Record\_策略投放箱
C:\Users\PG\我的雲端硬碟\MTX Test Record\_策略投放箱
G:\我的雲端硬碟\MTX Test Record\_策略投放箱
```

也可用環境變數覆蓋：

```text
MTX_TEST_RECORD_DIR
```

## 2. 手動批次回測按鈕

手動載入批次策略 JSON 後，批次回測按鈕改成主要按鈕樣式，比原本明顯。

## 3. 批次結果錯誤

修正批次 ZIP 產生時發生的：

```text
NameError: name '_safe_filename_part' is not defined
```

原因是 Streamlit 執行順序下，批次結果區塊呼叫該函式時，函式定義位置太後面。
本版已將檔名清理函式移到前段共用區。

## 4. 批次比較表新增欄位

新增：

```text
年化報酬率(%)
```

## 驗證結果

```text
python -m py_compile *.py
通過
```

```text
python self_check_correctness.py
PASS 16 cases
```

命令列批次回測 smoke test 通過，且輸出的 `batch_comparison.csv` 已包含 `年化報酬率(%)` 欄位。
