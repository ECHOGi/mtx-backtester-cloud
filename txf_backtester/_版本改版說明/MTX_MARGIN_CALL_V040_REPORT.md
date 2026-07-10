# MTX_MARGIN_CALL_V040_REPORT.md

## 本次目標

本次改版目標固定為：加入一口小台安全資金檢查，當安全緩衝金額被吃光時，視同斷頭強制平倉。

## 統一名詞

- 原始保證金：159,000 元。
- 安全緩衝金額：可承受的反向浮動損失金額，依 25% 壓力估算。
- 安全資金：原始保證金 + 安全緩衝金額。

## 斷頭定義

若持倉期間反向浮動損失 >= 安全緩衝金額，則視同安全資金緩衝被吃光。

交易明細中的出場原因：

- 程式值：`margin_call`
- 中文顯示：斷頭強制平倉

## 修改檔案

- `backtester.py`
- `correctness.py`
- `metrics.py`
- `self_check_correctness.py`
- `app.py`
- `啟動台指期回測工具.bat`
- `START_BACKTESTER.cmd`
- `V040_CHANGELOG.md`
- `MTX_MARGIN_CALL_V040_REPORT.md`

## 保留不動

- 未改 strategies.py 條件選單。
- 未改 signal_exit 邏輯。
- 未改 MTX prepared data 載入邏輯。
- 未加入多口數、券商保證金、即時保證金、自動補錢模擬。

## self_check 新增案例

原本 13 cases 保留，新增 3 個斷頭案例：

14. `margin_call_long`：多單安全緩衝金額被吃光，出場原因為 margin_call。
15. `margin_call_short`：空單安全緩衝金額被吃光，出場原因為 margin_call。
16. `margin_call_after_fixed_stop_priority`：固定停損先成立時，不被較遠的 margin_call 搶先。

執行結果：

```text
PASS 16 cases
```

## MTX prepared smoke test

使用 `data/prepared/MTX_stable_rollover_daily.csv` 測試既有預設策略。

- 交易筆數：142
- 斷頭次數：0
- 是否曾發生斷頭：否
- 第一次斷頭日期：無
- 歷史最低所需安全資金：228,650 元
- correctness：PASS
- correctness 檢查項目：1,717
- correctness 失敗：0

本次 smoke test 的安全資金估算：

- 近 250 日高點：48,866 點
- 安全緩衝金額：610,825 元
- 安全資金：769,825 元

## 啟動 bat 修正

原本 bat 會手動開網頁，同時 Streamlit 也可能自動開網頁，造成兩個 `http://localhost:8501/` 頁面。

本版改為：

```bat
start "" /min cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8501"
py -m streamlit run app.py --server.headless true
```

預期只會開啟一個網頁。
