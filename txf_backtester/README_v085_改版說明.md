# V085_CHANGELOG

## 版本
- 平台：v0.8.5
- 名稱：SAR＋BIAS＋日K缺口＋寶塔線研究版
- 基礎：v0.8.4全部功能保留

## 新增一：Parabolic SAR
### 條件積木
- `sar_flip_bullish`：SAR當根翻多
- `sar_flip_bearish`：SAR當根翻空
- `sar_bullish`／`sar_bearish`：目前趨勢狀態

參數：
```json
{"type":"sar_flip_bullish","af_start":0.02,"af_step":0.02,"af_max":0.2}
```

### 自適應移動出場
```json
"exit": {
  "use_sar_exit": true,
  "sar_af_start": 0.02,
  "sar_af_step": 0.02,
  "sar_af_max": 0.2
}
```
SAR出場採盤中觸價；若開盤已跳過SAR線，以開盤價成交。停損線使用當根開盤前已可由過去資料計算的值，不把當根完成後才知道的極值偷放入停損線。

## 新增二：乖離率 BIAS
公式：`(收盤價 / N日均線 - 1) × 100`

條件：
- `bias_above`
- `bias_below`
- `bias_cross_up`
- `bias_cross_down`

範例：
```json
{"type":"bias_above","period":60,"ma_type":"SMA","value":8.0}
```
可作強動能追價過濾，也可放在exclude作過熱排除。

## 新增三：日K缺口
### 開盤跳空幅度
- `open_gap_pct_above`
- `open_gap_pct_below`

### 完整缺口建立
- `full_gap_up`：當根low高於前一根high
- `full_gap_down`：當根high低於前一根low

### N日未回補
- `gap_up_unfilled`
- `gap_down_unfilled`

範例：
```json
{"type":"gap_up_unfilled","min_age":5,"lookback":60,"min_gap_pct":0.3}
```
表示最近60根內曾建立至少0.3%的向上完整缺口，已存在至少5根且仍未回補。

## 新增四：平台研究版寶塔線
本平台採固定、可重現的「三根轉向確認」定義：
- 紅色狀態中，收盤跌破前N根最低收盤才翻黑。
- 黑色狀態中，收盤突破前N根最高收盤才翻紅。
- N預設3，可用`confirm_bars`修改。

條件：
- `tower_flip_red`
- `tower_flip_black`
- `tower_red`
- `tower_black`

寶塔線優先度較低，主要供與均線交叉、價格通道做去重與對照研究。

## 相容性
- v0.8.4策略JSON不需修改，可直接回測。
- 新指標均只使用當根及以前資料。
- SAR出場已同步加入`correctness.py`逐筆重算。
- 尚未修改NX01、NX02策略；下一批才比較新指標是否能改善其訊號或出場。
