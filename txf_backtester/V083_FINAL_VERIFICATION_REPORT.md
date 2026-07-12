# V083_FINAL_VERIFICATION_REPORT

## 驗證結果

### 回測核心
- `self_check_correctness.py`：PASS 30
- `self_check_v081.py`：PASS 4
- `self_check_v082.py`：PASS 4
- `self_check_v083.py`：PASS 7
- Python `compileall`：PASS

### v0.8.3專項案例
1. 1拆22前後調整報酬保持連續，沒有假暴跌。
2. 原始價格模式在分割日將股數乘22，總資產保持連續。
3. 正二最大回撤不受假分割跌幅污染。
4. 分割後才開始回測時，不會再次把股數乘22。
5. 大型複利部位使用二分搜尋，可處理超過1,000微台等值曝險。
6. 六種未來狀態均可產生合法OHLC，且相同seed可重現。
7. 左側help與主畫面結果說明存在。

### 實際資料引擎驗證
- 使用內建MTX 2015-01-05～2026-06-30資料。
- 使用batch_025三策略。
- 標準純日K回測完成：PASS。
- 三截止日 × 六種未來狀態 × 三策略，共54個策略路徑：PASS。
- 自動未來延伸長度：292個交易日。
- 核心情境測試執行時間：約23秒（本驗證環境）。

### Streamlit驗證
- 首頁載入：PASS，無例外。
- 標準回測完整互動：PASS。
- 多截止日模式控制項：PASS；預設選擇3個代表截止日，每種狀態1條路徑。
- 單截止日完整情境互動：PASS，已顯示情境排名、超越正二比例、P10與尚未自然出場比例。
- Streamlit HTTP：200 OK。

## 部署結構
```text
txf_backtester/
├─ app.py
├─ benchmark_00631l.py
├─ future_scenarios.py
├─ self_check_v083.py
├─ requirements.txt
└─ data/prepared/MTX_stable_rollover_sessions.csv
```

## 驗證結論
v0.8.3可部署。正式執行batch_025時，建議研究模式選「多截止日＋未來情境＋正二比較」，資料週期與部位模式維持「依策略JSON」。
