# -*- coding: utf-8 -*-
"""
run_batch_backtest.py - 命令列批次回測入口（適用 v0.6.4）。

用途：
- 不開 Streamlit 網頁。
- 從策略投放箱或指定 JSON 檔讀取最多 20 組策略（--max-strategies 可調）。
- 直接執行 MTX prepared data 批次回測。
- 將結果寫回 Obsidian/Google Drive 同步資料夾。

v0.4.6 新增：
- 比較表新增：報酬回撤比 / 獲利因子 / 期望值 / 最大連續虧損 / 平均持倉
- 每策略自動輸出年度分解 yearly_stats.csv（含每年損益/次數/勝率/年度內回撤）
- 批次 JSON 支援 sweep 參數掃描模式（單參數多值自動展開，見 parse_strategy_batch）
- 期交稅預設「計入」，--no-tax 可關閉（舊 --use-tax 保留相容、已無作用）
- 上限由 10 調高為 20

範例：
    python run_batch_backtest.py
    python run_batch_backtest.py --batch "G:\\我的雲端硬碟\\MTX Test Record\\_策略投放箱\\strategy_batch_001.json"
    python run_batch_backtest.py --batch 路徑/批次策略.json --record-dir 路徑/回測結果
    python run_batch_backtest.py --start 2015-01-01 --end 2023-12-31   # 樣本內調參
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

from backtester import CostModel, run_backtest
from config import DEFAULT_MA_PERIODS, SYMBOLS
from data_loader import DataError
from metrics import compute_metrics, metrics_to_df, yearly_stats
from strategies import params_from_config, run_strategy_config

def detect_record_dir() -> str:
    env = os.environ.get("MTX_TEST_RECORD_DIR")
    user_home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    candidates = [
        env,
        os.path.join(user_home, "我的雲端硬碟", "MTX Test Record"),
        r"C:\Users\PG\我的雲端硬碟\MTX Test Record",
        r"G:\我的雲端硬碟\MTX Test Record",
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    for c in candidates:
        if c and os.path.isdir(os.path.dirname(c)):
            return c
    return os.path.join(user_home, "我的雲端硬碟", "MTX Test Record")


DEFAULT_RECORD_DIR = detect_record_dir()
STRATEGY_DROPBOX_NAME = "_策略投放箱"
BATCH_MAX_STRATEGIES = 20
PREPARED_MTX_FILE = "MTX_stable_rollover_daily.csv"
MTX_ORIGINAL_MARGIN = 159000.0
MTX_SAFETY_STRESS_RATE = 0.25

EXIT_REASON_LABELS = {
    "fixed_stop": "固定停損", "take_profit": "固定停利",
    "trailing_stop": "移動停損", "chandelier_long": "吊燈出場（多）",
    "chandelier_short": "吊燈出場（空）", "chandelier": "吊燈出場",
    "macd_reverse": "MACD 反向出場", "signal_exit": "條件出場",
    "margin_call": "斷頭強制平倉",
    "end_of_data": "資料結束強制平倉",
}
DIRECTION_LABELS = {"long": "多單", "short": "空單"}
TRADE_COL_ZH = {
    "signal_date": "訊號日", "signal_bar_index": "訊號K棒序",
    "entry_date": "進場日", "entry_execution_date": "進場執行日",
    "entry_bar_index": "進場K棒序",
    "exit_date": "出場日", "exit_bar_index": "出場K棒序",
    "direction": "方向", "entry_price": "進場價", "exit_price": "出場價",
    "quantity": "口數", "pnl_points": "損益點數", "pnl_amount": "損益金額",
    "holding_bars": "持倉K棒數", "exit_reason": "出場原因",
    "entry_reason": "進場條件",
    "max_adverse_points": "最大反向浮動點數",
    "max_adverse_amount": "最大反向浮動金額",
    "max_favorable_points": "最大順向浮動點數",
    "max_favorable_amount": "最大順向浮動金額",
    "required_safety_capital": "當筆最低所需安全資金",
}


def safe_filename_part(text: str) -> str:
    text = str(text).strip().replace("/", "-").replace("\\", "-")
    return re.sub(r'[<>:"|?*]+', "_", text) or "未命名"


def load_prepared_mtx(data_dir: str) -> pd.DataFrame:
    path = Path(data_dir) / "prepared" / PREPARED_MTX_FILE
    if not path.exists():
        raise FileNotFoundError(f"找不到 MTX prepared 檔：{path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "datetime" not in df.columns:
        raise DataError(f"prepared 檔缺少 datetime 欄位：{path}")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataError(f"prepared 檔缺少必要欄位 {missing}：{path}")
    for c in required + ["open_interest"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def calculate_mtx_safety_settings(cont: pd.DataFrame, end_date, point_value: float) -> dict:
    hist = cont[pd.to_datetime(cont["datetime"]).dt.date <= end_date].tail(250)
    if hist.empty:
        hist = cont.tail(250)
    base_high = float(hist["high"].max()) if not hist.empty else 0.0
    buffer_points = base_high * MTX_SAFETY_STRESS_RATE
    buffer_amount = buffer_points * float(point_value)
    return {
        "original_margin": MTX_ORIGINAL_MARGIN,
        "safety_stress_rate": MTX_SAFETY_STRESS_RATE,
        "safety_base_high": base_high,
        "safety_buffer_points": buffer_points,
        "safety_buffer_amount": buffer_amount,
        "safety_capital": MTX_ORIGINAL_MARGIN + buffer_amount,
    }


def _set_nested(cfg: dict, path: str, value):
    """依 'exit.chandelier_mult' 這種點記法設定巢狀欄位。"""
    keys = path.split(".")
    node = cfg
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value


def expand_sweeps(obj: dict) -> list[dict]:
    """v0.4.6：展開 sweep 參數掃描設定。

    JSON 範例：
    {
      "batch_name": "batch_005_吊燈倍數掃描",
      "strategies": [ ...固定對照組（可省略）... ],
      "sweep": {
        "base": { ...完整策略 config... },
        "param": "exit.chandelier_mult",
        "values": [2.0, 2.25, 2.5, 2.75, 3.0],
        "name_prefix": "MA20_60_120_吊燈22x"
      }
    }
    sweep 可以是單一物件或物件陣列（多個掃描共用一批）。
    每個 value 會複製 base、把 param 設成該值，策略名 = name_prefix + value。
    """
    sweeps = obj.get("sweep")
    if sweeps is None:
        return []
    if isinstance(sweeps, dict):
        sweeps = [sweeps]
    items = []
    for si, sw in enumerate(sweeps, start=1):
        base = sw.get("base")
        param = sw.get("param")
        values = sw.get("values")
        if not isinstance(base, dict) or not param or not isinstance(values, list):
            raise ValueError(f"sweep 第 {si} 組格式錯誤：需要 base(物件)/param(字串)/values(陣列)。")
        prefix = str(sw.get("name_prefix") or f"{param}=")
        for v in values:
            cfg = json.loads(json.dumps(base))  # deep copy
            _set_nested(cfg, param, v)
            cfg["name"] = f"{prefix}{v}"
            items.append(cfg)
    return items


def parse_strategy_batch(text: str, max_strategies: int = BATCH_MAX_STRATEGIES
                         ) -> tuple[str, list[tuple[str, dict]]]:
    obj = json.loads(text)
    batch_name = "MTX批次回測"
    if isinstance(obj, list):
        raw_items = obj
    elif isinstance(obj, dict):
        batch_name = str(obj.get("batch_name") or obj.get("name") or batch_name)
        raw_items = obj.get("strategies") or obj.get("items")
        sweep_items = expand_sweeps(obj)
        if raw_items is None:
            raw_items = [] if sweep_items else [obj]
        if not isinstance(raw_items, list):
            raise ValueError("strategies 必須是陣列。")
        raw_items = list(raw_items) + sweep_items
    else:
        raise ValueError("批次策略 JSON 必須是策略陣列，或包含 strategies 的物件。")

    if not isinstance(raw_items, list):
        raise ValueError("strategies 必須是陣列。")
    if len(raw_items) == 0:
        raise ValueError("批次策略 JSON 沒有策略。")
    if len(raw_items) > max_strategies:
        raise ValueError(f"一次最多只能放 {max_strategies} 組策略，目前有 {len(raw_items)} 組。")

    out = []
    for i, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 組策略不是 JSON 物件。")
        if isinstance(item.get("config"), dict):
            cfg = dict(item["config"])
            name = item.get("name") or item.get("label") or cfg.get("name")
        elif isinstance(item.get("strategy_config"), dict):
            cfg = dict(item["strategy_config"])
            name = item.get("name") or item.get("label") or cfg.get("name")
        else:
            cfg = dict(item)
            name = cfg.get("name") or item.get("label")
        name = str(name or f"策略{i:02d}")
        cfg.setdefault("name", name)
        cfg["symbol"] = "MTX"
        out.append((name, cfg))
    return batch_name, out


def newest_json_in_dropbox(record_dir: str) -> Path:
    inbox = Path(record_dir) / STRATEGY_DROPBOX_NAME
    inbox.mkdir(parents=True, exist_ok=True)
    files = sorted(inbox.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"策略投放箱沒有 JSON 檔：{inbox}")
    return files[0]


def zh_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    for c in ["signal_date", "entry_date", "entry_execution_date", "exit_date"]:
        if c in t.columns:
            t[c] = pd.to_datetime(t[c]).dt.strftime("%Y/%m/%d")
    if "direction" in t.columns:
        t["direction"] = t["direction"].map(DIRECTION_LABELS).fillna(t["direction"])
    if "exit_reason" in t.columns:
        t["exit_reason"] = t["exit_reason"].map(EXIT_REASON_LABELS).fillna(t["exit_reason"])
    return t.rename(columns=TRADE_COL_ZH)



def _sort_batch_compare(df: pd.DataFrame) -> pd.DataFrame:
    """依實際判讀優先度排序：報酬回撤比、總損益、回撤、獲利因子。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    rr = pd.to_numeric(out.get("報酬回撤比"), errors="coerce").fillna(float("-inf"))
    pnl = pd.to_numeric(out.get("總損益(元)"), errors="coerce").fillna(float("-inf"))
    dd = pd.to_numeric(out.get("最大回撤(元)"), errors="coerce").abs().fillna(float("inf"))
    pf = pd.to_numeric(out.get("獲利因子"), errors="coerce").fillna(float("-inf"))
    out["__rr"] = rr
    out["__pnl"] = pnl
    out["__dd_abs"] = dd
    out["__pf"] = pf
    out = out.sort_values(
        by=["__rr", "__pnl", "__dd_abs", "__pf"],
        ascending=[False, False, True, False],
        kind="mergesort",
    )
    return out.drop(columns=["__rr", "__pnl", "__dd_abs", "__pf"]).reset_index(drop=True)

def compare_row(idx: int, name: str, metrics: dict) -> dict:
    return {
        "策略編號": idx,
        "策略名稱": name,
        "報酬回撤比": metrics.get("報酬回撤比", ""),
        "總損益(元)": metrics.get("總損益(元)", 0),
        "最大回撤(元)": metrics.get("最大回撤(元)", 0),
        "策略標準最大回撤率(%)": metrics.get("策略標準最大回撤率(%)", ""),
        "市場期間漲跌幅(%)": metrics.get("市場期間漲跌幅(%)", ""),
        "市場最大回撤率(%)": metrics.get("市場最大回撤率(%)", ""),
        "相對市場回撤倍數": metrics.get("相對市場回撤倍數", ""),
        "獲利交易加權保留率(%)": metrics.get("獲利交易加權保留率(%)", ""),
        "曾有浮盈交易筆數": metrics.get("曾有浮盈交易筆數", ""),
        "浮盈轉虧率(%)": metrics.get("浮盈轉虧率(%)", ""),
        "獲利因子": metrics.get("獲利因子", ""),
        "期望值(元/筆)": metrics.get("期望值(元/筆)", ""),
        "交易次數": metrics.get("交易次數", 0),
        "勝率(%)": metrics.get("勝率(%)", 0),
        "最大連續虧損(次)": metrics.get("最大連續虧損(次)", ""),
        "平均持倉K棒數": metrics.get("平均持倉K棒數", ""),
        "資金持續未創新高交易天數": metrics.get("資金持續未創新高交易天數", ""),
        "年化報酬率(%)": metrics.get("年化報酬率(%)", ""),
        "總報酬率(%)": metrics.get("總報酬率(%)", 0),
        "是否曾發生斷頭": metrics.get("是否曾發生斷頭", "否"),
        "斷頭次數": metrics.get("斷頭次數", 0),
        "第一次斷頭日期": metrics.get("第一次斷頭日期", "無"),
    }

def markdown_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "（無資料）"
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---" for _ in cols]) + "|"]
    for _, row in df.iterrows():
        vals = []
        for c in df.columns:
            v = row[c]
            vals.append("" if pd.isna(v) else str(v).replace("|", "／"))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def equity_out(equity: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if equity is None or equity.empty:
        return pd.DataFrame(columns=["日期", "資金", "回撤"])
    eq = equity.copy()
    eq["資金"] = initial_capital + eq["equity"]
    eq["回撤"] = eq["資金"] - eq["資金"].cummax()
    return pd.DataFrame({
        "日期": pd.to_datetime(eq["datetime"]).dt.strftime("%Y/%m/%d"),
        "資金": eq["資金"].round(0),
        "回撤": eq["回撤"].round(0),
    })


def single_strategy_md(batch: dict, result: dict) -> str:
    m = result["metrics"]
    lines = [
        "---",
        "type: MTX批次回測單一策略",
        f"batch: {batch.get('batch_name', '')}",
        f"strategy_index: {result['idx']}",
        f"strategy_name: {result['name']}",
        "tags:",
        "  - MTX",
        "  - 批次回測",
        "---",
        "",
        f"# 策略 {result['idx']:02d}｜{result['name']}",
        "",
        f"- 回測期間：{batch.get('d_start')} ～ {batch.get('d_end')}",
        f"- 總損益：{m.get('總損益(元)', 0)} 元",
        f"- 年化報酬率：{m.get('年化報酬率(%)', '')}%",
        f"- 最大回撤：{m.get('最大回撤(元)', 0)} 元",
        f"- 報酬回撤比：{m.get('報酬回撤比', '')}",
        f"- 獲利因子：{m.get('獲利因子', '')}",
        f"- 期望值：{m.get('期望值(元/筆)', '')} 元/筆",
        f"- 交易次數：{m.get('交易次數', 0)} 筆",
        f"- 勝率：{m.get('勝率(%)', 0)}%",
        f"- 是否曾發生斷頭：{m.get('是否曾發生斷頭', '否')}",
        f"- 斷頭次數：{m.get('斷頭次數', 0)} 次",
        "",
        "## 年度分解",
        "",
        markdown_table(result.get("yearly")),
        "",
        "## 相關檔案",
        "- [交易明細 trades.csv](trades.csv)",
        "- [績效統計 metrics.csv](metrics.csv)",
        "- [資金曲線 equity_curve.csv](equity_curve.csv)",
        "- [年度分解 yearly_stats.csv](yearly_stats.csv)",
        "- [策略設定 strategy_config.json](strategy_config.json)",
    ]
    return "\n".join(lines) + "\n"


def batch_overview_md(batch: dict, folder_name: str) -> str:
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "---",
        "type: MTX批次回測",
        f"created: {now}",
        f"batch_name: {batch.get('batch_name', '')}",
        "tags:",
        "  - MTX",
        "  - 批次回測",
        "  - 策略比較",
        "---",
        "",
        f"# MTX 批次回測｜{batch.get('batch_name', '')}",
        "",
        f"- 建立時間：{now}",
        f"- 回測期間：{batch.get('d_start')} ～ {batch.get('d_end')}",
        f"- 策略數量：{len(batch.get('results', []))} / {BATCH_MAX_STRATEGIES}",
        f"- 批次資料夾：`{folder_name}`",
        "",
        "## 批次比較表",
        "",
        markdown_table(batch["compare"]),
        "",
        "## 策略連結",
    ]
    for r in batch.get("results", []):
        sub = f"{r['idx']:02d}_{safe_filename_part(r['name'])}"
        lines.append(f"- [[{sub}/00_策略回測摘要|策略 {r['idx']:02d}｜{r['name']}]]")
    lines += [
        "",
        "## 檢討欄位",
        "- 本批次最值得保留的策略：待檢討",
        "- 本批次最需要淘汰的策略：待檢討",
        "- 下一批策略調整方向：待檢討",
    ]
    return "\n".join(lines) + "\n"


def write_batch_outputs(batch: dict, record_dir: str) -> Path:
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"MTX_批次回測_{ts}_{safe_filename_part(batch.get('batch_name', 'MTX批次回測'))}"
    folder = Path(record_dir) / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "00_批次回測總覽.md").write_text(batch_overview_md(batch, folder_name), encoding="utf-8")
    batch["compare"].to_csv(folder / "batch_comparison.csv", index=False, encoding="utf-8-sig")
    for r in batch.get("results", []):
        sub = folder / f"{r['idx']:02d}_{safe_filename_part(r['name'])}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "00_策略回測摘要.md").write_text(single_strategy_md(batch, r), encoding="utf-8")
        r["trades_zh"].to_csv(sub / "trades.csv", index=False, encoding="utf-8-sig")
        metrics_to_df(r["metrics"]).to_csv(sub / "metrics.csv", index=False, encoding="utf-8-sig")
        if r.get("yearly") is not None:
            r["yearly"].to_csv(sub / "yearly_stats.csv", index=False, encoding="utf-8-sig")
        equity_out(r["equity"], batch["initial_capital"]).to_csv(sub / "equity_curve.csv", index=False, encoding="utf-8-sig")
        (sub / "strategy_config.json").write_text(json.dumps(r["cfg"], ensure_ascii=False, indent=2), encoding="utf-8")

    zip_name = f"{folder_name}.zip"
    zip_path = folder / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in folder.rglob("*"):
            if path == zip_path or path.is_dir():
                continue
            z.write(path, path.relative_to(folder))
    return folder


def run_batch(args) -> Path:
    project_dir = Path(__file__).resolve().parent
    batch_path = Path(args.batch) if args.batch else newest_json_in_dropbox(args.record_dir)
    text = batch_path.read_text(encoding="utf-8-sig")
    batch_name, items = parse_strategy_batch(text, int(args.max_strategies))

    cont = load_prepared_mtx(args.data_dir)
    dmin = cont["datetime"].min().date()
    dmax = cont["datetime"].max().date()
    d_start = pd.to_datetime(args.start).date() if args.start else dmin
    d_end = pd.to_datetime(args.end).date() if args.end else dmax
    mask = (cont["datetime"].dt.date >= d_start) & (cont["datetime"].dt.date <= d_end)
    data = cont[mask].reset_index(drop=True)
    if len(data) < 30:
        raise ValueError("回測區間資料不足 30 根 K 棒，請放寬區間。")

    point_value = float(args.point_value)
    fee = float(args.fee)
    slippage = float(args.slippage)
    initial_capital = float(args.initial_capital)
    # v0.4.6：期交稅預設計入；--no-tax 關閉（--use-tax 保留相容、已無作用）
    tax_rate = 0.0 if args.no_tax else SYMBOLS["MTX"].get("tax_rate", 0.0)
    safety = calculate_mtx_safety_settings(cont, d_end, point_value)
    cost = CostModel(
        point_value=point_value,
        fee=fee,
        slippage_points=slippage,
        tax_rate=tax_rate,
        use_margin_call_check=True,
        safety_buffer_amount=float(safety["safety_buffer_amount"]),
        original_margin_amount=float(safety["original_margin"]),
    )

    results = []
    rows = []
    for idx, (name, cfg) in enumerate(items, start=1):
        params = params_from_config(cfg)
        if not getattr(params, "ma_periods", None):
            params.ma_periods = tuple(DEFAULT_MA_PERIODS)
        sig = run_strategy_config(data, cfg, params)
        trades, equity = run_backtest(sig, cost, params)
        metrics = compute_metrics(
            trades, equity,
            margin_reference=SYMBOLS["MTX"]["margin_reference"],
            quantity=cost.quantity,
            initial_capital=initial_capital,
            market_data=data,
        )
        row = compare_row(idx, name, metrics)
        rows.append(row)
        results.append({
            "idx": idx,
            "name": name,
            "cfg": cfg,
            "trades": trades,
            "trades_zh": zh_trades(trades),
            "equity": equity,
            "metrics": metrics,
            "yearly": yearly_stats(trades, equity),
            "row": row,
        })

    batch = {
        "batch_name": batch_name,
        "source_json": str(batch_path),
        "results": results,
        "compare": pd.DataFrame(rows),
        "initial_capital": initial_capital,
        "symbol": "MTX",
        "d_start": d_start,
        "d_end": d_end,
        "n_bars": len(data),
        "safety_info": safety,
    }
    return write_batch_outputs(batch, args.record_dir)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MTX 命令列批次回測入口 v0.4.6")
    parser.add_argument("--batch", default="", help="批次策略 JSON 路徑；未指定時讀取投放箱最新 JSON。")
    parser.add_argument("--data-dir", default="data", help="資料資料夾，預設 data。")
    parser.add_argument("--record-dir", default=DEFAULT_RECORD_DIR, help="Obsidian/Google Drive 回測紀錄資料夾。")
    parser.add_argument("--start", default="", help="回測起日 YYYY-MM-DD；空白則用 prepared 起日。")
    parser.add_argument("--end", default="", help="回測迄日 YYYY-MM-DD；空白則用 prepared 迄日。")
    parser.add_argument("--point-value", type=float, default=50.0, help="MTX 每點價值。")
    parser.add_argument("--fee", type=float, default=20.0, help="單邊手續費。")
    parser.add_argument("--slippage", type=float, default=1.0, help="單邊滑價點數。")
    parser.add_argument("--initial-capital", type=float, default=159000.0, help="初始資金。")
    parser.add_argument("--use-tax", action="store_true",
                        help="（相容舊版，已無作用）v0.4.6 起期交稅預設計入。")
    parser.add_argument("--no-tax", action="store_true", help="不計入期交稅。")
    parser.add_argument("--max-strategies", type=int, default=BATCH_MAX_STRATEGIES,
                        help=f"單批策略數上限，預設 {BATCH_MAX_STRATEGIES}。")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        folder = run_batch(args)
        print(f"PASS：批次回測完成，已輸出到：{folder}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"FAIL：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
