# -*- coding: utf-8 -*-
"""
metrics.py - 回測績效統計。

v0.3.2：新增顯示層指標（不影響回測核心）：
- 總報酬率(%)、年化報酬率(%)、最大回撤(%)：以「初始資金」為分母
  （initial_capital，預設 = 一口原始保證金 margin_reference * 口數）
- 期望值(元/筆)：每筆交易平均損益
- 獲利因子：總獲利 ÷ 總虧損，越高越好
舊指標鍵名全部保留，相容既有匯出與報告。
"""
import numpy as np
import pandas as pd


def max_consecutive_losses(pnl: pd.Series) -> int:
    """最大連續虧損次數。"""
    worst = cur = 0
    for x in pnl:
        cur = cur + 1 if x < 0 else 0
        worst = max(worst, cur)
    return worst


def compute_metrics(trades: pd.DataFrame, equity: pd.DataFrame,
                    margin_reference: float = None, quantity: int = 1,
                    initial_capital: float = None,
                    market_data: pd.DataFrame = None) -> dict:
    """回傳 {指標名: 值}，全部為 python 原生型別，方便顯示與匯出。"""
    m = {}
    risk_cap_skips = 0
    missing_atr_skips = 0
    dynamic_size_skips = 0
    if equity is not None and not equity.empty:
        if "risk_cap_skipped_entries" in equity.columns:
            risk_cap_skips = int(pd.to_numeric(equity["risk_cap_skipped_entries"], errors="coerce").fillna(0).iloc[-1])
        if "missing_atr_skipped_entries" in equity.columns:
            missing_atr_skips = int(pd.to_numeric(equity["missing_atr_skipped_entries"], errors="coerce").fillna(0).iloc[-1])
        if "dynamic_size_skipped_entries" in equity.columns:
            dynamic_size_skips = int(pd.to_numeric(equity["dynamic_size_skipped_entries"], errors="coerce").fillna(0).iloc[-1])
    if trades is None or trades.empty:
        return {
            "交易次數": 0,
            "風險上限跳過進場次數": risk_cap_skips,
            "ATR缺值跳過進場次數": missing_atr_skips,
            "動態部位無可用口數跳過次數": dynamic_size_skips,
            "是否曾發生斷頭": "否",
            "斷頭次數": 0,
            "第一次斷頭日期": "無",
            "歷史最低所需安全資金": "無交易",
            "訊息": "此參數組合沒有產生任何交易",
        }

    pnl = trades["pnl_amount"].astype(float)
    pts = trades["pnl_points"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]

    # 初始資金：優先用傳入值，否則用保證金參考值*口數
    base = initial_capital
    if base is None and margin_reference:
        base = margin_reference * quantity

    m["總損益(元)"] = round(pnl.sum(), 0)
    m["總損益(點)"] = round(pts.sum(), 1)
    if base:
        m["總報酬率(%)"] = round(pnl.sum() / base * 100, 2)
        # 年化報酬率：以回測期間日曆天數換算 CAGR
        if equity is not None and not equity.empty:
            d0 = pd.to_datetime(equity["datetime"].iloc[0])
            d1 = pd.to_datetime(equity["datetime"].iloc[-1])
            days = max((d1 - d0).days, 1)
            ratio = (base + pnl.sum()) / base
            if ratio > 0:
                m["年化報酬率(%)"] = round((ratio ** (365.25 / days) - 1) * 100, 2)
            else:
                m["年化報酬率(%)"] = None  # 資金已虧損殆盡，無法年化
    m["交易次數"] = int(len(trades))
    m["風險上限跳過進場次數"] = risk_cap_skips
    m["ATR缺值跳過進場次數"] = missing_atr_skips
    m["動態部位無可用口數跳過次數"] = dynamic_size_skips
    m["獲利次數"] = int(len(wins))
    m["虧損次數"] = int(len(losses))
    m["勝率(%)"] = round(len(wins) / len(trades) * 100, 2)
    m["平均損益(元)"] = round(pnl.mean(), 1)
    m["期望值(元/筆)"] = round(pnl.mean(), 1)
    m["平均獲利(元)"] = round(wins.mean(), 1) if len(wins) else 0.0
    m["平均虧損(元)"] = round(losses.mean(), 1) if len(losses) else 0.0
    gross_loss = abs(losses.sum())
    m["獲利因子"] = (round(wins.sum() / gross_loss, 2)
                    if gross_loss > 0 else float("inf"))
    m["最大獲利(元)"] = round(pnl.max(), 0)
    m["最大虧損(元)"] = round(pnl.min(), 0)
    m["最大連續虧損(次)"] = max_consecutive_losses(pnl)
    m["平均持倉K棒數"] = round(trades["holding_bars"].mean(), 1)
    # v0.6.7：動態部位與資金使用統計。
    if "quantity" in trades.columns:
        qty = pd.to_numeric(trades["quantity"], errors="coerce").dropna()
        if len(qty):
            m["平均小台等值口數"] = round(float(qty.mean()), 2)
            m["最大小台等值口數"] = round(float(qty.max()), 2)
    if "position_regime" in trades.columns:
        regimes = trades["position_regime"].fillna("unknown").astype(str)
        m["核心部位交易數"] = int((regimes == "core").sum())
        m["強趨勢加碼交易數"] = int((regimes == "core+addon").sum())
        m["防禦部位交易數"] = int((regimes == "defensive").sum())
    if "planned_stop_risk_amount" in trades.columns:
        planned = pd.to_numeric(trades["planned_stop_risk_amount"], errors="coerce").dropna()
        if len(planned):
            m["平均預定停損風險(元)"] = round(float(planned.mean()), 0)
            m["最大預定停損風險(元)"] = round(float(planned.max()), 0)
    if "stress_risk_amount" in trades.columns:
        stress = pd.to_numeric(trades["stress_risk_amount"], errors="coerce").dropna()
        if len(stress):
            m["最大跳空壓力風險(元)"] = round(float(stress.max()), 0)
    if "position_margin_amount" in trades.columns:
        margin_used = pd.to_numeric(trades["position_margin_amount"], errors="coerce").dropna()
        if len(margin_used):
            m["最大進場原始保證金(元)"] = round(float(margin_used.max()), 0)
    # v0.8.0：分清實際帳戶報酬、策略交易效率、資金安全效率。
    if "quantity" in trades.columns:
        qty = pd.to_numeric(trades["quantity"], errors="coerce").replace(0, np.nan)
        per_equiv = pnl / qty
        if per_equiv.notna().any():
            m["每口小台等值損益合計(元)"] = round(float(per_equiv.sum()), 0)
            m["每口小台等值平均損益(元/筆)"] = round(float(per_equiv.mean()), 1)
    if "effective_leverage" in trades.columns:
        lev = pd.to_numeric(trades["effective_leverage"], errors="coerce").dropna()
        if len(lev):
            m["平均有效槓桿(倍)"] = round(float(lev.mean()), 2)
            m["最大有效槓桿(倍)"] = round(float(lev.max()), 2)
    if "margin_utilization_pct" in trades.columns:
        util = pd.to_numeric(trades["margin_utilization_pct"], errors="coerce").dropna()
        if len(util):
            m["平均保證金占用率(%)"] = round(float(util.mean()), 2)
            m["最大保證金占用率(%)"] = round(float(util.max()), 2)
    if "safe_capital_balance" in trades.columns:
        bal = pd.to_numeric(trades["safe_capital_balance"], errors="coerce").dropna()
        if len(bal):
            m["最低安全資金餘額(元)"] = round(float(bal.min()), 0)
    if "position_action" in trades.columns:
        acts = trades["position_action"].fillna("maintain").astype(str)
        m["動態加口次數"] = int((acts == "increase").sum())
        m["動態減口次數"] = int((acts == "decrease").sum())
        m["動態維持口數次數"] = int((acts == "maintain").sum())

    # v0.6.3：獲利保留與浮盈轉虧。
    # 加權保留率 = 獲利交易實現點數合計 / 同批獲利交易最大順向浮盈點數合計。
    if "max_favorable_points" in trades.columns:
        mfe = pd.to_numeric(trades["max_favorable_points"], errors="coerce").fillna(0.0)
        pnl_pts = pd.to_numeric(trades["pnl_points"], errors="coerce").fillna(0.0)
        win_mask = pnl_pts > 0
        valid_wins = win_mask & (mfe > 0)
        if valid_wins.any():
            weighted_retention = pnl_pts[valid_wins].sum() / mfe[valid_wins].sum() * 100
            trade_retention = (pnl_pts[valid_wins] / mfe[valid_wins] * 100).clip(upper=100)
            m["獲利交易加權保留率(%)"] = round(float(weighted_retention), 2)
            m["獲利交易中位保留率(%)"] = round(float(trade_retention.median()), 2)
        had_favorable_move = mfe > 0
        turned_loss = had_favorable_move & (pnl_pts <= 0)
        m["曾有浮盈交易筆數"] = int(had_favorable_move.sum())
        m["浮盈轉虧筆數"] = int(turned_loss.sum())
        if had_favorable_move.any():
            m["浮盈轉虧率(%)"] = round(
                float(turned_loss.sum() / had_favorable_move.sum() * 100), 2
            )

    # v0.4.0：斷頭強制平倉統計。
    if "exit_reason" in trades.columns:
        mc = trades[trades["exit_reason"] == "margin_call"]
        m["是否曾發生斷頭"] = "是" if len(mc) else "否"
        m["斷頭次數"] = int(len(mc))
        if len(mc) and "exit_date" in mc.columns:
            first_dt = pd.to_datetime(mc["exit_date"]).min()
            m["第一次斷頭日期"] = first_dt.strftime("%Y-%m-%d")
        else:
            m["第一次斷頭日期"] = "無"
    if "required_safety_capital" in trades.columns:
        # 舊欄位保留相容；它是「單筆保證金＋該筆最大反向浮動」，不是最終建議投入額。
        m["舊式單筆安全資金參考"] = float(round(trades["required_safety_capital"].astype(float).max(), 0))
    if "exit_reason" in trades.columns:
        eod = trades[trades["exit_reason"] == "end_of_data"]
        m["期末強制平倉交易數"] = int(len(eod))
        m["期末強制平倉損益(元)"] = round(float(eod["pnl_amount"].astype(float).sum()), 0) if len(eod) else 0.0
        m["扣除期末強制平倉後損益(元)"] = round(float(pnl.sum() - (eod["pnl_amount"].astype(float).sum() if len(eod) else 0.0)), 0)

    # 最大回撤（權益曲線：已實現+未實現）
    if equity is not None and not equity.empty:
        eq = equity["equity"].astype(float)
        dd = eq - eq.cummax()
        m["最大回撤(元)"] = round(dd.min(), 0)
        if base:
            m["最大回撤(%)"] = round(dd.min() / base * 100, 2)
            capital_curve = base + eq
            m["最低帳戶權益(元)"] = round(float(capital_curve.min()), 0)
            m["最低帳戶權益率(%)"] = round(float(capital_curve.min()) / float(base) * 100, 2)
            # v0.7.0：真正依策略每日權益與當日維持保證金反推最低起始資金。
            # 這不再假設持倉承受大盤固定跌幅，也不把原始保證金誤稱為安全資金。
            if "maintenance_margin_amount" in equity.columns:
                maint = pd.to_numeric(equity["maintenance_margin_amount"], errors="coerce").fillna(0.0)
                min_operating = (maint - eq).max()
                m["歷史最低運作資金(元)"] = round(max(float(min_operating), 0.0), 0)
                m["設定資金池(元)"] = round(float(base), 0)
                m["資金池高於歷史最低運作資金(元)"] = round(float(base) - max(float(min_operating), 0.0), 0)
            rolling_peak = capital_curve.cummax()
            standard_dd_pct = ((capital_curve / rolling_peak) - 1.0) * 100
            m["策略標準最大回撤率(%)"] = round(float(standard_dd_pct.min()), 2)

        # v0.6.3：同期市場基準。以 MTX 連續契約收盤價計算期間漲跌與最大回撤，
        # 再用「策略標準最大回撤率 / 市場最大回撤率」衡量回撤是否超出市場波動。
        if market_data is not None and not market_data.empty and "close" in market_data.columns:
            close = pd.to_numeric(market_data["close"], errors="coerce").dropna()
            if len(close) >= 2 and float(close.iloc[0]) != 0:
                market_return = (float(close.iloc[-1]) / float(close.iloc[0]) - 1.0) * 100
                market_dd_pct = ((close / close.cummax()) - 1.0) * 100
                market_max_dd = float(market_dd_pct.min())
                m["市場期間漲跌幅(%)"] = round(market_return, 2)
                m["市場最大回撤率(%)"] = round(market_max_dd, 2)
                strategy_dd = m.get("策略標準最大回撤率(%)")
                if strategy_dd is not None and abs(market_max_dd) > 1e-12:
                    m["相對市場回撤倍數"] = round(abs(float(strategy_dd)) / abs(market_max_dd), 2)
                if strategy_dd is not None and abs(market_return) > 1e-12:
                    m["回撤相對市場漲跌幅倍數"] = round(abs(float(strategy_dd)) / abs(market_return), 2)

        # v0.4.6：報酬/最大回撤比（越高越好；主要排序指標）
        max_dd = abs(float(dd.min()))
        if max_dd > 0:
            m["報酬回撤比"] = round(pnl.sum() / max_dd, 2)
        else:
            m["報酬回撤比"] = float("inf") if pnl.sum() > 0 else 0.0
        # v0.4.7：資金持續未創新高交易天數（權益創高到回到新高的最長間隔）
        is_peak = eq >= eq.cummax()
        longest = cur = 0
        for flag in is_peak:
            cur = 0 if flag else cur + 1
            longest = max(longest, cur)
        m["資金持續未創新高交易天數"] = int(longest)
    return m


def yearly_stats(trades: pd.DataFrame, equity: pd.DataFrame) -> pd.DataFrame:
    """v0.4.6：年度分解統計。

    以「出場日」歸屬年度計算每年損益/交易次數/勝率；
    以權益曲線計算「年度內最大回撤」（每年年初重設高點）。
    回傳欄位：年度, 損益(元), 交易次數, 勝率(%), 年度內最大回撤(元)
    """
    if trades is None or trades.empty:
        return pd.DataFrame(columns=["年度", "損益(元)", "交易次數",
                                     "勝率(%)", "年度內最大回撤(元)"])
    t = trades.copy()
    t["_year"] = pd.to_datetime(t["exit_date"]).dt.year
    rows = []
    dd_by_year = {}
    if equity is not None and not equity.empty:
        eq2 = equity.copy()
        eq2["_year"] = pd.to_datetime(eq2["datetime"]).dt.year
        for y, g in eq2.groupby("_year"):
            e = g["equity"].astype(float)
            dd_by_year[int(y)] = round(float((e - e.cummax()).min()), 0)
    for y, g in t.groupby("_year"):
        pnl_y = g["pnl_amount"].astype(float)
        wins = int((pnl_y > 0).sum())
        rows.append({
            "年度": int(y),
            "損益(元)": round(pnl_y.sum(), 0),
            "交易次數": int(len(g)),
            "勝率(%)": round(wins / len(g) * 100, 1),
            "年度內最大回撤(元)": dd_by_year.get(int(y), ""),
        })
    return pd.DataFrame(rows).sort_values("年度").reset_index(drop=True)


def metrics_to_df(m: dict) -> pd.DataFrame:
    """轉成兩欄表格，方便顯示與匯出 CSV。"""
    return pd.DataFrame({"指標": list(m.keys()),
                         "數值": [str(v) for v in m.values()]})
