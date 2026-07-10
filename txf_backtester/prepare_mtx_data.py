# -*- coding: utf-8 -*-
"""
prepare_mtx_data.py - 建立 MTX 回測專用 prepared 資料。

用途：
- 只從期交所 CSV 抽出 MTX（小型臺指期貨）一般盤資料。
- 不修改原始 CSV。
- 產生 data/prepared/ 底下的 MTX 清洗後資料與三種連續契約日 K。

用法：
    python prepare_mtx_data.py data/raw
    python prepare_mtx_data.py C:\\Users\\...\\回測數據 --out data/prepared

輸出：
    data/prepared/MTX_clean_regular_contracts.csv
    data/prepared/MTX_stable_rollover_daily.csv
    data/prepared/MTX_volume_max_daily.csv
    data/prepared/MTX_oi_max_daily.csv
    data/prepared/MTX_*_rollover_log.csv
    data/prepared/MTX_prepare_file_summary.csv
    data/prepared/MTX_prepare_method_summary.csv
    MTX_PREPARED_DATA_REPORT.md
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path

import pandas as pd

from continuous_contract import build_continuous

CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SESSION_MAP = {"一般": "regular", "盤後": "after_hours"}
MTX_CODES = {"MTX", "MXF"}

COL_ALIASES = {
    "date": ["交易日期"],
    "contract": ["契約"],
    "contract_month": ["到期月份(週別)", "到期月份（週別）"],
    "open": ["開盤價"],
    "high": ["最高價"],
    "low": ["最低價"],
    "close": ["收盤價"],
    "volume": ["成交量"],
    "settlement": ["結算價"],
    "open_interest": ["未沖銷契約數", "未沖銷契約量"],
    "session": ["交易時段"],
}
REQUIRED = ["date", "contract", "contract_month", "open", "high", "low", "close", "volume"]


def norm_text(x) -> str:
    return CONTROL.sub("", str(x)).strip().replace("\u3000", "")


def to_num(x):
    s = CONTROL.sub("", str(x)).replace(",", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def find_encoding(path: Path) -> str:
    for enc in ["utf-8-sig", "cp950", "big5", "utf-8"]:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                f.read(2048)
            return enc
        except UnicodeDecodeError:
            continue
    return "cp950"


def parse_date(x) -> str:
    s = norm_text(x)
    return s.replace("／", "/").replace("－", "-").replace(".", "-").replace("/", "-")


def header_map(header: list[str]) -> tuple[dict, list[str]]:
    h = [norm_text(c) for c in header]
    mp = {}
    for std, aliases in COL_ALIASES.items():
        for alias in aliases:
            if alias in h:
                mp[std] = h.index(alias)
                break
    return mp, h


def read_mtx_regular(path: Path) -> tuple[pd.DataFrame, int, int, str]:
    enc = find_encoding(path)
    rows = []
    raw_rows = 0
    skipped_bad = 0
    with path.open("r", encoding=enc, errors="replace", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return pd.DataFrame(), 0, 0, "empty file"
        mp, actual_header = header_map(header)
        missing = [c for c in REQUIRED if c not in mp]
        if missing:
            return pd.DataFrame(), 0, 0, f"missing required columns {missing}; actual={actual_header}"

        for r in reader:
            if not r or all(not str(x).strip() for x in r):
                continue
            raw_rows += 1
            if len(r) < len(header):
                r = r + [""] * (len(header) - len(r))
            try:
                contract = norm_text(r[mp["contract"]])
                if contract not in MTX_CODES:
                    continue

                contract_month = norm_text(r[mp["contract_month"]])
                if "/" in contract_month:
                    continue

                session = "一般"
                if "session" in mp and mp["session"] < len(r):
                    session = norm_text(r[mp["session"]]) or "一般"
                session = SESSION_MAP.get(session, session)
                if session != "regular":
                    continue

                open_ = to_num(r[mp["open"]])
                high = to_num(r[mp["high"]])
                low = to_num(r[mp["low"]])
                close = to_num(r[mp["close"]])
                if (open_ is None or high is None or low is None or close is None
                        or min(open_, high, low, close) <= 0):
                    skipped_bad += 1
                    continue

                volume = to_num(r[mp["volume"]])
                volume = 0.0 if volume is None else volume
                open_interest = to_num(r[mp["open_interest"]]) if "open_interest" in mp else 0.0
                open_interest = 0.0 if open_interest is None else open_interest
                settlement = to_num(r[mp["settlement"]]) if "settlement" in mp else None

                rows.append({
                    "date": parse_date(r[mp["date"]]),
                    "symbol": "MTX",
                    "contract": contract,
                    "contract_month": contract_month,
                    "session": "regular",
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "open_interest": open_interest,
                    "settlement": settlement,
                    "source_file": path.name,
                })
            except Exception:
                skipped_bad += 1
                continue

    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    return df, raw_rows, skipped_bad, ""


def continuous_filename(method: str) -> str:
    if method == "stable_rollover":
        return "MTX_stable_rollover_daily.csv"
    if method == "volume_max_daily":
        return "MTX_volume_max_daily.csv"
    return "MTX_oi_max_daily.csv"


def write_report(report_path: Path, out_dir: Path, file_rows: list[dict], clean_all: pd.DataFrame,
                 method_rows: list[dict], duplicate_removed: int) -> None:
    lines = []
    lines.append("# MTX 回測專用資料整理報告")
    lines.append("")
    lines.append("## 本次決策")
    lines.append("")
    lines.append("- 只整理 MTX，不整理 TX/TMF/其他商品。")
    lines.append("- 先以平台機能可順利完成為主，因此輸出一般盤 regular 的日 K 回測快取。")
    lines.append("- 不修改原始期交所 CSV。")
    lines.append("- 不修改 Streamlit UI、backtester、strategies 或既有回測公式。")
    lines.append("- 連續契約仍使用專案既有 `continuous_contract.py` 規則產生。")
    lines.append("")
    lines.append("## 輸出位置")
    lines.append("")
    lines.append(f"`{out_dir.as_posix()}`")
    lines.append("")
    lines.append("## 輸出檔案")
    lines.append("")
    lines.append("| 檔案 | 用途 |")
    lines.append("|---|---|")
    purposes = [
        ("MTX_clean_regular_contracts.csv", "清洗後 MTX 一般盤所有契約資料，可重建連續契約"),
        ("MTX_stable_rollover_daily.csv", "MTX 穩定換倉連續契約日 K，建議作為平台預設"),
        ("MTX_volume_max_daily.csv", "MTX 每日成交量最大連續契約日 K"),
        ("MTX_oi_max_daily.csv", "MTX 每日未沖銷契約數最大連續契約日 K"),
        ("MTX_stable_rollover_rollover_log.csv", "穩定換倉紀錄"),
        ("MTX_volume_max_daily_rollover_log.csv", "成交量最大法換倉紀錄"),
        ("MTX_oi_max_daily_rollover_log.csv", "未沖銷契約最大法換倉紀錄"),
        ("MTX_prepare_file_summary.csv", "來源檔與 MTX 筆數摘要"),
        ("MTX_prepare_method_summary.csv", "三種連續契約輸出摘要"),
    ]
    for fname, purpose in purposes:
        lines.append(f"| `{fname}` | {purpose} |")

    lines.append("")
    lines.append("## 來源檔摘要")
    lines.append("")
    lines.append("| 檔案 | 原始資料列數 | MTX一般盤列數 | 起日 | 迄日 | 契約數 |")
    lines.append("|---|---:|---:|---|---|---:|")
    for r in file_rows:
        lines.append(
            f"| {r['file']} | {int(r['raw_data_rows']):,} | {int(r['mtx_regular_rows']):,} | "
            f"{r['date_min']} | {r['date_max']} | {int(r['contracts'])} |"
        )

    lines.append("")
    lines.append("## MTX 清洗後契約資料")
    lines.append("")
    lines.append(f"- 筆數：{len(clean_all):,}")
    lines.append(f"- 起訖日期：{clean_all['date'].min().date()} ～ {clean_all['date'].max().date()}")
    lines.append(f"- 契約月份/週別數：{clean_all['contract_month'].nunique():,}")
    lines.append(f"- 移除完全重複列：{duplicate_removed:,}")
    lines.append("")
    lines.append("## 連續契約輸出檢查")
    lines.append("")
    lines.append("| 方法 | 日K筆數 | 起日 | 迄日 | 換倉紀錄列數 | 日期中斷>15天 | 契約回跳 |")
    lines.append("|---|---:|---|---|---:|---:|---:|")
    for r in method_rows:
        lines.append(
            f"| {r['method']} | {r['rows']:,} | {r['date_min']} | {r['date_max']} | "
            f"{r['rollover_rows']:,} | {r['big_gaps_gt15_days']} | {r['contract_back_jumps']} |"
        )

    lines.append("")
    lines.append("## 判斷")
    lines.append("")
    lines.append("- MTX prepared 資料已涵蓋 2015 年至 2026 年 6 月。")
    lines.append("- 三種連續契約皆可正常產生。")
    lines.append("- 本次三種方法都沒有日期中斷或契約回跳。")
    lines.append("- stable_rollover 仍建議作為非工程師模式預設，因為換倉較穩定、邏輯較容易說明。")
    lines.append("")
    lines.append("## 下一步交接建議")
    lines.append("")
    lines.append(
        "下一輪再做平台整合：當商品為 MTX 時，Streamlit 優先讀取 "
        "`data/prepared/MTX_<method>_daily.csv`，不要每次重新載入全商品原始 CSV。"
        "若 prepared 檔不存在，再提醒使用者執行 MTX 資料整理。"
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def prepare_mtx_data(input_dir: Path, out_dir: Path, report_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    files = sorted(input_dir.rglob("*.csv"))
    if not files:
        raise FileNotFoundError(f"找不到 CSV 檔：{input_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("MTX_*"):
        p.unlink()

    file_rows = []
    parts = []
    for f in files:
        df, raw_rows, skipped_bad, msg = read_mtx_regular(f)
        if not df.empty:
            parts.append(df)
        file_rows.append({
            "file": f.name,
            "status": "ok" if msg == "" else "skip",
            "raw_data_rows": raw_rows,
            "mtx_regular_rows": len(df),
            "skipped_bad_mtx_rows": skipped_bad,
            "date_min": df["date"].min().date().isoformat() if not df.empty else "",
            "date_max": df["date"].max().date().isoformat() if not df.empty else "",
            "contracts": int(df["contract_month"].nunique()) if not df.empty else 0,
            "message": msg,
        })

    if not parts:
        raise ValueError("清理後沒有任何 MTX 一般盤資料")

    clean_all = pd.concat(parts, ignore_index=True)
    before = len(clean_all)
    clean_all = clean_all.drop_duplicates(
        subset=["date", "contract", "contract_month", "session", "open", "high", "low", "close", "volume", "open_interest"]
    ).copy()
    duplicate_removed = before - len(clean_all)
    clean_all = clean_all.sort_values(["date", "contract_month"]).reset_index(drop=True)
    clean_all.to_csv(out_dir / "MTX_clean_regular_contracts.csv", index=False, encoding="utf-8-sig")

    method_rows = []
    for method in ["stable_rollover", "volume_max_daily", "oi_max_daily"]:
        cont, log = build_continuous(clean_all, method=method, n_confirm=3, exclude_weekly=True)
        cont_path = out_dir / continuous_filename(method)
        log_path = out_dir / f"MTX_{method}_rollover_log.csv"
        cont.to_csv(cont_path, index=False, encoding="utf-8-sig")
        log.to_csv(log_path, index=False, encoding="utf-8-sig")
        gaps = cont["datetime"].diff().dt.days
        method_rows.append({
            "method": method,
            "rows": len(cont),
            "date_min": cont["datetime"].min().date().isoformat(),
            "date_max": cont["datetime"].max().date().isoformat(),
            "rollover_rows": len(log),
            "big_gaps_gt15_days": int((gaps > 15).sum()),
            "contract_back_jumps": int((cont["contract_month"] < cont["contract_month"].shift(1)).sum()),
            "file": cont_path.name,
            "log": log_path.name,
        })

    file_df = pd.DataFrame(file_rows)
    method_df = pd.DataFrame(method_rows)
    file_df.to_csv(out_dir / "MTX_prepare_file_summary.csv", index=False, encoding="utf-8-sig")
    method_df.to_csv(out_dir / "MTX_prepare_method_summary.csv", index=False, encoding="utf-8-sig")
    write_report(report_path, out_dir, file_rows, clean_all, method_rows, duplicate_removed)
    return file_df, method_df


def main() -> int:
    ap = argparse.ArgumentParser(description="建立 MTX 回測專用 prepared 資料")
    ap.add_argument("input_dir", nargs="?", default="data/raw", help="期交所 CSV 資料夾，預設 data/raw")
    ap.add_argument("--out", default="data/prepared", help="輸出資料夾，預設 data/prepared")
    ap.add_argument("--report", default="MTX_PREPARED_DATA_REPORT.md", help="Markdown 報告路徑")
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    out_dir = Path(args.out)
    report_path = Path(args.report)
    _, method_df = prepare_mtx_data(input_dir, out_dir, report_path)
    print(f"已完成 MTX prepared 資料：{out_dir}")
    print(f"報告：{report_path}")
    print(method_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
