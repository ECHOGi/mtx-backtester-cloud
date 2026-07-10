# MTX prepared data 平台整合測試報告

## 測試目標

確認 v0.3.7 已將 Streamlit 平台資料載入流程接上 MTX prepared data：

`data/prepared/MTX_stable_rollover_daily.csv`

## 本版修改摘要

本版只修改 `app.py`。

新增邏輯：

```python
if symbol == "MTX" and os.path.exists(prepared_mtx_path(folder)):
    return _load_prepared_mtx(folder)
```

代表當商品為 MTX 且 prepared 檔存在時，平台直接讀取 prepared 日K檔，不再讀取全商品 raw CSV。

## prepared 檔案

- 路徑：`data/prepared/MTX_stable_rollover_daily.csv`
- 商品：MTX
- 交易時段：一般盤 regular
- 連續契約：stable_rollover
- 日期範圍：2015-01-05 ～ 2026-06-30
- 日K筆數：2,798

## 測試指令

```bash
python -m py_compile *.py
python self_check_correctness.py
```

## 測試結果

### py_compile

結果：PASS

### self_check_correctness.py

結果：PASS 13 cases

既有 8 個 correctness 案例保留，signal_exit 新增案例也保留。

### MTX prepared smoke test

使用 `data/prepared/MTX_stable_rollover_daily.csv` 搭配既有預設策略與回測引擎。

| 項目 | 結果 |
|---|---:|
| 回測資料列數 | 2,798 |
| 資料起日 | 2015-01-05 |
| 資料迄日 | 2026-06-30 |
| 交易筆數 | 142 |
| correctness 總檢查項目 | 1,717 |
| correctness 失敗項目 | 0 |
| 結論 | PASS |

## 出場原因分布

| 出場原因 | 筆數 |
|---|---:|
| fixed_stop | 86 |
| macd_reverse | 43 |
| chandelier | 13 |

## Streamlit 實機啟動狀態

本 sandbox 環境未安裝 Streamlit，因此無法在此環境完整啟動網頁介面。

嘗試指令：

```bash
python -m streamlit run app.py --server.headless true
```

結果：

```text
No module named streamlit
```

因此本次完成的是：

- Python 編譯檢查
- correctness 自檢
- MTX prepared 檔直接進入策略與 backtester 的 smoke test

實機畫面操作建議於使用者本機或已安裝 requirements.txt 的環境執行確認。

## 結論

v0.3.7 已完成 MTX prepared data 平台整合的程式碼修改。當 `data/prepared/MTX_stable_rollover_daily.csv` 存在時，MTX 回測資料載入會直接走 prepared 快取，不再每次讀取全商品 raw CSV。
