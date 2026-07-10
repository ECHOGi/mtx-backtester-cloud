# V050 改版說明｜獲利分段吊燈出場支援

版本：v0.5.0  
日期：2026-07-10

## 改版目的

為支援 batch_008 的策略 11～15，新增「獲利分段吊燈」出場機制：

- 獲利未達第一門檻：使用較緊吊燈倍數
- 獲利進入中段：放寬吊燈倍數
- 獲利超過高門檻：再放寬吊燈倍數，避免牛市大波段過早出場

## 新增策略 JSON 欄位

在 `exit` 區塊可使用：

```json
{
  "use_chandelier": false,
  "use_profit_tier_chandelier": true,
  "profit_tier_chandelier_period": 22,
  "profit_tier_amounts": [7000, 10000],
  "profit_tier_mults": [2.5, 3.0, 3.5]
}
```

說明：

- `profit_tier_amounts` 使用金額門檻（元）
- `profit_tier_mults` 數量必須比門檻多 1 段
- 例：`[7000, 10000]` + `[2.5, 3.0, 3.5]`
  - 浮盈 < 7000：吊燈 2.5
  - 7000 <= 浮盈 < 10000：吊燈 3.0
  - 浮盈 >= 10000：吊燈 3.5

## 修改檔案

- `strategies.py`
  - `StrategyParams` 新增獲利分段吊燈欄位
  - 預先計算各分段吊燈線

- `backtester.py`
  - 新增依浮盈金額選擇吊燈倍數的出場判斷
  - 交易明細 `exit_reason` 會顯示例如：
    - `profit_tier_chandelier_2.5`
    - `profit_tier_chandelier_3.0`
    - `profit_tier_chandelier_3.5`

## 相容性

- 舊策略不受影響
- 未啟用 `use_profit_tier_chandelier` 時，仍使用原本固定吊燈邏輯
- batch_008 的策略 11～15 需使用 v0.5.0 以上

## 驗證

已完成：

- `strategies.py` / `backtester.py` 語法檢查
- batch_008 部分策略 smoke test
- 確認獲利分段吊燈可產生交易結果與 exit_reason
