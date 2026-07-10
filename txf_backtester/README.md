# 台指期回測工具 v0.6.3

個人使用的台灣指數期貨（MTX 小台為主、預留 TMF/TX）回測工具。
資料來源為臺灣期貨交易所「每日行情」CSV，介面為 Streamlit。

## 1. 專案目的與商品定位

- 匯入期交所每日交易 CSV（多年、多檔自動合併）
- 清理資料、建立近月連續契約
- 用條件積木組合策略並回測（內建 MACD + Bollinger + Chandelier 示範策略）
- 看圖調整參數、檢視績效、匯出結果
- 架構模組化，方便交給 Codex / Claude / Gemini 繼續擴充

**商品定位：MTX 小型臺指期為主要商品（介面與 check_data 預設），
TMF 微型臺指期為次要商品（2024/07 上市後才有資料），TX 大台僅預留、
不是主要商品。** 商品規格全部在 `config.py` 的 `SYMBOLS`，不寫死在程式中。

## 2. 安裝方式

```bash
pip install -r requirements.txt
```

## 2.1 資料檢查（建議先跑）

```bash
python check_data.py C:\Users\PG\Desktop\回測數據 --symbols MTX TMF
```

輸出：各檔讀取結果與缺漏欄位警告（壞檔跳過不崩潰）、各年度筆數／
各商品筆數／一般盤盤後盤筆數、清理後筆數、連續契約筆數與起訖日期、
日期中斷與契約回跳檢查，並匯出 output/clean_continuous_<商品>.csv
與 output/rollover_log_<商品>.csv。

## 3. 如何放置 CSV 資料

把期交所每日行情 CSV（例如 `2015_fut.csv` ~ `2025_fut.csv`）放進任一資料夾，
在介面左側「CSV 資料夾路徑」填入該資料夾路徑即可（預設 `data`，
也可填絕對路徑，例如 `C:\Users\PG\Desktop\回測數據`）。

- 編碼自動偵測（utf-8 / big5 / cp950 皆可）
- 部分年度檔案每列尾端多一個逗號，程式已處理（`index_col=False`）

## 4. 啟動

```bash
streamlit run app.py
```

瀏覽器會開啟 `http://localhost:8501`。
左側側欄調整參數後，回測會自動重新執行。

## 5. 欄位格式說明

CSV 需含以下中文欄位（對應表在 `config.py` 的 `COLUMN_MAP`，可自行增補）：

| 中文欄位 | 標準欄位 |
|---|---|
| 交易日期 | date |
| 契約 | contract |
| 到期月份(週別) | contract_month |
| 開盤價/最高價/最低價/收盤價 | open/high/low/close |
| 成交量 | volume |
| 結算價 | settlement |
| 未沖銷契約數 | open_interest |
| 交易時段 | session（缺此欄位視為一般盤） |

清理規則：排除 OHLC 為 `-` 或缺漏的列、排除價差契約（到期月份含 `/`）、
可選擇排除週契約（含 `W`）、可選一般盤／盤後盤／全部。

## 6. 連續契約（v2）

`continuous_contract.py` 支援三種模式，回傳 `(連續契約df, 換倉紀錄df)`：

- `stable_rollover`（預設）逐日邏輯：
  1. 只保留月契約（到期月份為 6 位數字，如 202501；週契約 202501W1 一律排除）
  2. 初始日取最近月契約（記錄 reason=initial）
  3. 每日比較「下一個月契約」與「目前契約」的 trigger 欄位
     （`trigger="volume"` 預設，可改 `open_interest`）；
     下一契約連續 `n_confirm` 天（預設 3）較大時才換倉
     （記錄 reason=volume>current x3d）
  4. **expired 強制換倉**：目前契約到期後從資料中消失時，
     當日強制換到「往後」最近的月份（記錄 reason=expired）。
     實務上 MTX 近月成交量通常維持最大直到到期日，
     所以多數換倉由 expired 觸發＝到期日換月，這是資料特性、非錯誤；
     想提前換倉可把 `n_confirm` 調成 1~2 或改用 `trigger="open_interest"`。
  5. 契約月份只能往後、不能回頭，保證連續契約不會每天跳來跳去
- `volume_max_daily`：每日成交量最大（簡單模式）
- `oi_max_daily`：每日未沖銷契約數最大

換倉紀錄（rollover_log.csv）欄位：rollover_date, old_contract, new_contract,
reason, old_volume, new_volume, old_open_interest, new_open_interest。
實測 2015~2025 MTX：契約平均持有約 20 根日K、月份單調往後、無來回跳動；
因近月成交量通常維持最大直到到期，換倉多以 expired（到期強制）觸發。

價格調整：預設 `price_mode="unadjusted"`；`adjusted`（換倉價差回溯調整）
已保留設計位置 `_apply_adjustment()`，第一版未實作。

## 7. 策略條件模組與策略 JSON（v2）

### condition_blocks.py：條件積木

每個條件是一個函式（輸入 OHLCV DataFrame，輸出 bool Series），
用 `@register("條件名")` 註冊，`evaluate_block()` 以 AND / OR 組合。
內建 24 個條件：價格 vs 均線/布林（含突破）、均線交叉、多空頭排列、
MACD（histogram 轉向、DIF 交叉）、KD（交叉、K 值高低）、RSI、成交量 vs 量均。
新增條件只改這個檔案，backtester.py 不用動。

### 策略 JSON 結構（strategies.py）

```json
{
  "name": "MACD_BB_Chandelier_v1",
  "symbol": "MTX", "timeframe": "1D", "direction": "both",
  "entry_long":  {"logic": "AND", "conditions": [
      {"type": "macd_hist_cross_up"},
      {"type": "close_above_bollinger_mid"},
      {"type": "close_above_ma", "ma_type": "SMA", "period": 20}]},
  "entry_short": {"logic": "AND", "conditions": ["...對稱..."]},
  "exit": {"use_chandelier": true, "use_macd_reverse": true,
           "use_fixed_stop": true, "stop_points": 100}
}
```

用法：`run_strategy_config(df, cfg)` 產生訊號 → `run_backtest()`。
存讀：`save_strategy_json()` / `load_strategy_json()`；介面「策略 JSON」
可下載、側欄「載入參數 JSON」可載回（同時相容舊版扁平參數檔）。

### 示範策略

**MACD + Bollinger + Chandelier**（已改由條件模組組合，非寫死）：
多方進場＝MACD histogram 由負轉正 AND 收盤>布林中線 AND（可開關）收盤>過濾均線；
空方完全對稱。支援只做多／只做空／多空雙向，單一持倉、不加碼、不反手。

出場條件（皆可參數開關，優先順序如下）：

1. 固定停損（盤中觸價）
2. 固定停利（盤中觸價）
3. 移動停損（追蹤進場後極值，盤中觸價）
4. 吊燈出場 Chandelier（收盤確認）
5. MACD 反向（收盤確認）

## 8. 回測假設（backtester.py）

- **無 look-ahead bias**：第 i 根收盤訊號成立 → 第 i+1 根「開盤價」進場
- 觸價出場以觸價價位成交；開盤跳空超過停損/停利價則以開盤價成交
- 收盤確認類出場（吊燈/MACD反向）以當根收盤價成交
- 移動停損追蹤的極值只用前一根（含）以前的資料
- 成本：單邊手續費、單邊滑價（進出場各往不利方向調整）、期交稅預留（可開關）
- 權益曲線 = 已實現損益 + 未實現損益（以收盤價評價）
- timeframe 不寫死：任何符合 `datetime/open/high/low/close/volume` 的資料
  （5分K、60分K、週K...）都可直接丟進 `strategies` + `backtester` 回測

## 9. 匯出

介面下方「匯出」區可下載：交易明細 CSV、績效統計 CSV、
清理後連續契約 CSV（clean_continuous.csv）、策略參數 JSON。
參數 JSON 可由側欄「載入參數 JSON」讀回。

## 10. 專案架構

```
app.py                  Streamlit 介面（版面參考 kcTrader）
check_data.py           全資料載入檢查 script
condition_blocks.py     策略條件積木（24 個條件 + AND/OR 組合器）
config.py               商品規格(MTX/TMF/TX)、欄位對應、預設值
data_loader.py          CSV 讀取(自動編碼)與清理
continuous_contract.py  近月連續契約
indicators.py           SMA/EMA/WMA/MACD/KD/RSI/BB/ATR/量均/吊燈/交叉/排列
strategies.py           策略 JSON 結構 + StrategyParams + 策略註冊表
backtester.py           單一持倉回測引擎 + CostModel
metrics.py              績效統計
utils.py                JSON/CSV 存讀
```

## 11. 如何新增條件積木（condition_blocks.py）

在 `condition_blocks.py` 加一個函式並註冊即可，其他檔案不用改：

```python
@register("close_above_prev_high")
def close_above_prev_high(df, lookback=5, **_):
    """收盤突破前 N 日高點"""
    return df["close"] > df["high"].shift(1).rolling(int(lookback)).max()
```

規則：輸入標準 OHLCV DataFrame、參數給預設值、結尾加 `**_` 吃掉多餘參數、
回傳 bool Series。之後就能在策略 JSON 用
`{"type": "close_above_prev_high", "lookback": 5}`。

## 12. 如何新增策略

方法一（推薦）：寫一份策略 JSON，程式回測：

```python
from strategies import run_strategy_config, params_from_config
from backtester import CostModel, run_backtest
cfg = {...}                                # 見第 7 節 JSON 結構
sig = run_strategy_config(cont_df, cfg)    # cont_df = 連續契約 OHLCV
trades, equity = run_backtest(sig, CostModel(point_value=50),
                              params_from_config(cfg))
```

方法二（要出現在介面下拉選單）：在 `strategies.py` 的 `STRATEGIES`
加一行：

```python
STRATEGIES["我的策略"] = lambda df, p: run_strategy_config(df, my_cfg, p)
```

出場邏輯（停損/停利/移動停損/吊燈/MACD反向）由 backtester 依 exit
參數開關執行，新策略不需要重寫 backtester.py。

## 13. 已知限制（v0.2）

- 連續契約為未調整（unadjusted），換倉日有價格跳空，
  跨換倉日持倉的損益會含換月價差；`adjusted` 已留設計位置未實作
- stable_rollover 在 MTX 上多以到期日強制換月（見第 6 節說明）
- 出場觸價使用日K高低點近似，同一根K棒內先碰停損或停利無法分辨，
  依保守順序：停損 > 停利 > 移動停損
- 吊燈與 MACD 反向為收盤確認、收盤價出場（假設收盤前可下單）
- 期交稅預設關閉（可在介面開啟）
- 單一持倉、不加碼、不反手；資料為日K（引擎支援分鐘K但尚無分鐘資料）
- UI 的條件組合固定為示範策略模板；自訂條件組合請用策略 JSON + 程式回測
- 2015/2016 檔案無「交易時段」欄位，一律視為一般盤

## 14. 未來擴充方向

- 換倉模型：到期日換倉、價差調整連續契約（改 `build_continuous`）
- 分鐘 K 回測：直接把分鐘 OHLCV 餵給 strategies/backtester
- 新策略：在 `strategies.py` 寫同介面函式，加入 `STRATEGIES` dict 即可出現在介面
- 均線交叉／多頭排列進場：`indicators.py` 已有 `cross_over/bullish_alignment` 等現成函式
- 參數最佳化：對 `run_backtest` 迴圈掃參數即可（引擎為純函式，無全域狀態）
- 接 XQ 模擬交易：匯出訊號 CSV 供 XQ 對照

## 給 AI 接手者的說明

- 資料流：`load_folder → clean_data → build_continuous → 策略函式 → run_backtest → compute_metrics`
- 各模組為純函式、無全域狀態，`app.py` 只負責 UI 與組裝
- 策略介面約定寫在 `strategies.py` 開頭 docstring
- 期交所 CSV 的陷阱（編碼混合、尾端多逗號、欄位左移）已在 `data_loader._read_csv` 處理並註解

---

## v0.3 回測正確性強化

v0.3 不是策略 UI 版本，重點是交易明細可追蹤與可驗證。

新增檔案：

- `correctness.py`：回測後檢查交易明細是否符合進出場、出場原因與損益規則。
- `self_check_correctness.py`：合成資料正確性測試。
- `V03_CHANGELOG.md`：v0.3 修改紀錄。

可執行：

```bash
python self_check_correctness.py
```

真實資料 v0.3 回測輸出放在：

```text
output_v03/
```

交易明細新增欄位包含 `signal_date`、`entry_execution_date`、`entry_reason`，用來檢查是否為下一根開盤進場，以及每筆交易由哪些條件積木觸發。

---

## v0.3.1 使用便利性小修

v0.3.1 主要是介面與使用便利性調整，不改回測核心邏輯。

重點：

- 介面文字中文化。
- 左側欄位重排。
- 新增「圖表顯示」控制，可分別勾選 K 線、布林通道、吊燈線、均線、成交量、MACD、KD、權益曲線等。
- 圖表顯示勾選只影響畫面，不影響回測結果。
- 新增 `啟動台指期回測工具.bat`，可雙擊啟動本機 Streamlit 工具。
- 新增 `使用說明.md`。

資料請放在：

```text
txf_backtester/data/
```

使用期間請不要關閉啟動後出現的 CMD / PowerShell 視窗；用完後關閉該視窗即可停止平台。
