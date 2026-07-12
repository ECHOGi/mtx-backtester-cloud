# V084_FINAL_VERIFICATION_REPORT

## 驗證結果

### 回測核心與歷代回歸測試
- `self_check_correctness.py`：PASS 30
- `self_check_v081.py`：PASS 4
- `self_check_v082.py`：PASS 4
- `self_check_v083.py`：PASS 7
- `self_check_v084.py`：PASS 5
- Python `compileall`：PASS

### v0.8.4專項案例
1. 已實現權益回撤達完整門檻時，風險倍率正確降至floor。
2. 回撤啟動門檻與完整門檻之間，風險倍率正確線性內插。
3. 動態風險模式在口數上限為0時仍可依安全限制建立部位，並正確套用回撤煞車。
4. 未來情境主排名輸出策略／正二期末資產、年化、回撤及相對差值欄位。
5. UI可設定每種未來狀態最多20條路徑，並顯示策略與正二並列數值。

### batch_026結構驗證
- JSON解析：PASS
- 策略數：5
- CC03、GE01、CC04：安全約束動態複利
- NX01、NX02：動態風險複利＋已實現權益回撤煞車
- 五組均設定`position_max_micro_units = 0`，代表無人為口數上限
- 五組均啟用帳戶保證金與壓力風險檢查
- 固定口數策略：0組

### 實際資料引擎煙霧測試
- 使用內建MTX 2015-01-05～2026-06-30資料。
- 五策略完整歷史日K回測：完成，無程式例外。
- 三截止日 × 六種未來狀態 × 每種1條共同路徑 × 五策略，共90個策略路徑：完成。
- 主排名欄位、正二配對欄位、斷頭與未平倉資訊：均有輸出。
- 上述1路徑測試只驗證引擎與輸出完整性，不作正式策略績效判定；正式研究請使用每種狀態20條路徑。

### Streamlit執行環境
- 本地驗證環境未安裝Streamlit套件，因此本次未執行瀏覽器HTTP互動測試。
- 已完成Python編譯、UI原始碼關鍵控制項檢查、回測引擎與未來情境實際執行測試。
- 正式部署後仍應確認首頁版本顯示v0.8.4、路徑選單包含20、主表顯示策略與00631L並列欄位。

## 部署結構
```text
txf_backtester/
├─ app.py
├─ backtester.py
├─ future_scenarios.py
├─ metrics.py
├─ strategies.py
├─ self_check_v084.py
├─ README_v084_改版說明.md
├─ V084_FINAL_VERIFICATION_REPORT.md
├─ requirements.txt
└─ data/prepared/MTX_stable_rollover_sessions.csv
```

## 正式執行設定
- 研究模式：多截止日＋未來情境＋正二比較
- 策略檔：batch_026
- 週期與部位模式：依策略JSON
- 每種未來狀況路徑：20
- 正二基準：00631L
- 主排名：共同路徑期末總權益對00631L期末市值

## 驗證結論
v0.8.4核心引擎與批次結構通過驗證，可進行雲端部署及正式20路徑回測。尚未自然出場視為模擬終點持倉狀態，不列為錯誤或排名扣分。
