# -*- coding: utf-8 -*-
"""
compare_trades.py - v0.4.6 兩策略交易明細比對工具。

用途：
- 回答「A 為什麼比 B 好」這類問題。
- 以「訊號日 + 方向」對齊兩策略的交易，分類為：
    1. 相同進場、相同出場（僅價差）
    2. 相同進場、不同出場（重點研究對象）
    3. 只有 A 有的交易
    4. 只有 B 有的交易
- 輸出逐年損益差異，看差異集中在哪些年份。

輸入：批次回測輸出的策略資料夾（內含 trades.csv），或直接給 trades.csv 路徑。

範例：
    python compare_trades.py "路徑/04_新主力_吊燈22x2_5" "路徑/03_新主力基準_吊燈22x3"
    python compare_trades.py a_trades.csv b_trades.csv --out 比對結果
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

KEY_COLS = ["訊號日", "方向"]
NEED_COLS = ["訊號日", "方向", "進場日", "出場日", "進場價", "出場價",
             "損益金額", "持倉K棒數", "出場原因"]


def load_trades(path_str: str) -> tuple[str, pd.DataFrame]:
    path = Path(path_str)
    if path.is_dir():
        name = path.name
        path = path / "trades.csv"
    else:
        name = path.parent.name or path.stem
    if not path.exists():
        raise FileNotFoundError(f"找不到交易明細：{path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    missing = [c for c in NEED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} 缺少欄位：{missing}（需要批次回測輸出的中文欄位 trades.csv）")
    return name, df


def markdown_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "（無資料）"
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(
            "" if pd.isna(v) else str(v).replace("|", "／") for v in row) + " |")
    return "\n".join(lines)


def compare(name_a: str, a: pd.DataFrame, name_b: str, b: pd.DataFrame) -> dict:
    a = a[NEED_COLS].copy()
    b = b[NEED_COLS].copy()
    m = a.merge(b, on=KEY_COLS, how="outer", suffixes=("_A", "_B"), indicator=True)

    both = m[m["_merge"] == "both"].copy()
    only_a = m[m["_merge"] == "left_only"].copy()
    only_b = m[m["_merge"] == "right_only"].copy()

    both["損益差(A-B)"] = both["損益金額_A"].astype(float) - both["損益金額_B"].astype(float)
    same_exit = both[both["出場日_A"] == both["出場日_B"]].copy()
    diff_exit = both[both["出場日_A"] != both["出場日_B"]].copy()

    # 逐年損益差
    year_rows = []
    m["_year"] = pd.to_datetime(m["訊號日"], format="%Y/%m/%d", errors="coerce").dt.year
    for y, g in m.groupby("_year"):
        pa = g["損益金額_A"].astype(float).sum()
        pb = g["損益金額_B"].astype(float).sum()
        year_rows.append({"年度": str(int(y)), f"A_{name_a}損益": round(pa, 0),
                          f"B_{name_b}損益": round(pb, 0), "差異(A-B)": round(pa - pb, 0)})
    yearly = pd.DataFrame(year_rows)

    return {"both": both, "same_exit": same_exit, "diff_exit": diff_exit,
            "only_a": only_a, "only_b": only_b, "yearly": yearly}


def build_report(name_a: str, name_b: str, r: dict) -> str:
    total_a = r["both"]["損益金額_A"].astype(float).sum() + \
        r["only_a"]["損益金額_A"].astype(float).sum()
    total_b = r["both"]["損益金額_B"].astype(float).sum() + \
        r["only_b"]["損益金額_B"].astype(float).sum()
    diff_cols = ["訊號日", "方向", "出場日_A", "出場日_B", "出場價_A", "出場價_B",
                 "出場原因_A", "出場原因_B", "損益金額_A", "損益金額_B", "損益差(A-B)"]
    de = r["diff_exit"]
    se = r["same_exit"]
    lines = [
        f"# 交易明細比對｜A={name_a} vs B={name_b}",
        "",
        "## 總覽",
        "",
        f"- A 總損益：{round(total_a, 0)} 元 / B 總損益：{round(total_b, 0)} 元 / 差異：{round(total_a - total_b, 0)} 元",
        f"- 相同進場、相同出場：{len(se)} 筆（損益差 {round(se['損益差(A-B)'].sum(), 0) if len(se) else 0} 元）",
        f"- 相同進場、不同出場：{len(de)} 筆（損益差 {round(de['損益差(A-B)'].sum(), 0) if len(de) else 0} 元）",
        f"- 只有 A 有的交易：{len(r['only_a'])} 筆（損益 {round(r['only_a']['損益金額_A'].astype(float).sum(), 0) if len(r['only_a']) else 0} 元）",
        f"- 只有 B 有的交易：{len(r['only_b'])} 筆（損益 {round(r['only_b']['損益金額_B'].astype(float).sum(), 0) if len(r['only_b']) else 0} 元）",
        "",
        "## 判讀提示",
        "",
        "- 若差異只集中在少數幾筆「不同出場」交易，兩策略差異可能是雜訊，不宜據此細調參數。",
        "- 若「只有 A / 只有 B」筆數多，代表出場時點改變了後續進場序列，屬結構性差異。",
        "",
        "## 逐年損益比較",
        "",
        markdown_table(r["yearly"]),
        "",
        f"## 相同進場、不同出場明細（{len(de)} 筆）",
        "",
        markdown_table(de[diff_cols] if len(de) else pd.DataFrame()),
        "",
        f"## 只有 A（{name_a}）的交易",
        "",
        markdown_table(r["only_a"][["訊號日", "方向", "進場日_A", "出場日_A",
                                    "出場原因_A", "損益金額_A"]] if len(r["only_a"]) else pd.DataFrame()),
        "",
        f"## 只有 B（{name_b}）的交易",
        "",
        markdown_table(r["only_b"][["訊號日", "方向", "進場日_B", "出場日_B",
                                    "出場原因_B", "損益金額_B"]] if len(r["only_b"]) else pd.DataFrame()),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="兩策略交易明細比對 v0.4.6")
    parser.add_argument("a", help="策略 A 資料夾或 trades.csv")
    parser.add_argument("b", help="策略 B 資料夾或 trades.csv")
    parser.add_argument("--out", default="", help="輸出資料夾；預設為策略 A 的上層資料夾。")
    args = parser.parse_args()
    try:
        name_a, ta = load_trades(args.a)
        name_b, tb = load_trades(args.b)
        r = compare(name_a, ta, name_b, tb)
        report = build_report(name_a, name_b, r)
        out_dir = Path(args.out) if args.out else Path(args.a).resolve().parent
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / f"交易比對_{name_a[:40]}_vs_{name_b[:40]}.md"
        md_path.write_text(report, encoding="utf-8")
        csv_path = out_dir / f"交易比對_{name_a[:40]}_vs_{name_b[:40]}.csv"
        r["both"].to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"PASS：比對完成\n- 報告：{md_path}\n- 對齊明細：{csv_path}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"FAIL：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
