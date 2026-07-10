# -*- coding: utf-8 -*-
"""
check_data.py - 全資料載入檢查（2015~2025 多年度 CSV）。

用法：
    python check_data.py                          # 檢查 ./data，主商品 MTX、TMF
    python check_data.py C:\\Users\\PG\\Desktop\\回測數據
    python check_data.py <資料夾> --symbols MTX TMF TX --out output

輸出：
- 各檔案讀取結果（成功/跳過、筆數、缺漏欄位警告）
- 各年度筆數、各年度可用商品（MTX/TMF/TX）筆數、一般盤/盤後盤筆數
- 各主商品：清理後筆數、連續契約筆數、起訖日期、日期中斷警告
- output/clean_continuous_<商品>.csv 與 output/rollover_log_<商品>.csv
"""
import argparse
import os
import sys

import pandas as pd

from config import DATA_DIR, OUTPUT_DIR, SYMBOLS
from continuous_contract import build_continuous
from data_loader import DataError, clean_data, load_folder, match_symbol
from utils import export_csv

GAP_CALENDAR_DAYS = 15  # 連續契約相鄰兩根K棒相隔超過此天數視為日期中斷


def check_folder(folder: str, symbols: list, out_dir: str,
                 method: str = "stable_rollover", n_confirm: int = 3) -> int:
    print(f"=== 資料夾：{folder} ===")
    report = []
    try:
        raw = load_folder(folder, skip_bad=True, report=report)
    except DataError as e:
        print(f"[錯誤] {e}")
        return 1

    # ---- 檔案層報告 ----
    print("\n--- 檔案讀取結果 ---")
    for r in report:
        mark = "OK  " if r["status"] == "ok" else "跳過"
        msg = f"  [警告] {r['message']}" if r["message"] else ""
        print(f"[{mark}] {r['file']:<24} {r['rows']:>9,} 筆{msg}")
    n_ok = sum(1 for r in report if r["status"] == "ok")
    print(f"共 {len(report)} 檔，成功 {n_ok}，跳過 {len(report) - n_ok}，"
          f"合併原始筆數 {len(raw):,}")

    # ---- 年度層報告 ----
    print("\n--- 年度統計（檔案列數快速檢查）---")
    # 真實 2015~2025 資料約 400 萬列；避免在整張 raw 大表上重複
    # 字串掃描造成記憶體壓力，年度總筆數直接由檔案讀取報告彙總。
    year_rows = {}
    for r in report:
        if r["status"] != "ok":
            continue
        y = os.path.basename(r["file"])[:4]
        year_rows[y] = year_rows.get(y, 0) + int(r["rows"])
    for year in sorted(year_rows):
        if not str(year).isdigit():
            print(f"  [警告] 異常年度值: {year!r}（{year_rows[year]:,} 筆）")
        else:
            print(f"  {year}  總筆數 {year_rows[year]:>9,}")

    # ---- 商品層報告（清理 + 連續契約）----
    rc = 0
    for sym in symbols:
        print(f"\n--- 商品 {sym} {SYMBOLS[sym]['name']}（一般盤）---")
        try:
            clean = clean_data(raw, symbol=sym, session="regular")
        except DataError as e:
            print(f"  [警告] {e}")
            continue
        print(f"  清理後有效筆數：{len(clean):,}")
        try:
            cont, log = build_continuous(clean, method=method,
                                         n_confirm=n_confirm)
        except ValueError as e:
            print(f"  [警告] 連續契約建立失敗：{e}")
            rc = 1
            continue
        d0, d1 = cont["datetime"].min(), cont["datetime"].max()
        print(f"  連續契約筆數：{len(cont):,}（{method}）")
        print(f"  起訖日期：{d0.date()} ~ {d1.date()}")
        print(f"  換倉次數：{len(log)}（含 initial/expired）")

        # 日期中斷檢查
        gaps = cont["datetime"].diff().dt.days
        breaks = cont[gaps > GAP_CALENDAR_DAYS]
        if breaks.empty:
            print(f"  日期檢查：無超過 {GAP_CALENDAR_DAYS} 天的中斷")
        else:
            print(f"  [警告] 發現 {len(breaks)} 處日期中斷（>{GAP_CALENDAR_DAYS}天）：")
            for i, row in breaks.iterrows():
                prev = cont.loc[i - 1, "datetime"].date()
                print(f"    {prev} -> {row['datetime'].date()}"
                      f"（{int(gaps.loc[i])} 天）")

        # 契約單調性檢查（stable_rollover 不應回頭）
        cm = cont["contract_month"]
        back = (cm < cm.shift(1)).sum()
        if back:
            print(f"  [警告] 契約月份出現 {back} 次回跳（換倉邏輯需檢查）")
        else:
            print("  契約月份單調往後，無來回跳動")

        # 匯出
        p1 = export_csv(cont, os.path.join(out_dir, f"clean_continuous_{sym}.csv"))
        p2 = export_csv(log, os.path.join(out_dir, f"rollover_log_{sym}.csv"))
        print(f"  已匯出：{p1}")
        print(f"  已匯出：{p2}")
    return rc


def main():
    ap = argparse.ArgumentParser(description="期交所 CSV 全資料檢查")
    ap.add_argument("folder", nargs="?", default=DATA_DIR, help="CSV 資料夾")
    ap.add_argument("--symbols", nargs="+", default=["MTX", "TMF"],
                    help="要檢查的商品（預設 MTX TMF；主商品為小台 MTX）")
    ap.add_argument("--method", default="stable_rollover",
                    choices=["stable_rollover", "volume_max_daily", "oi_max_daily"])
    ap.add_argument("--n-confirm", type=int, default=3, help="穩定換倉確認天數")
    ap.add_argument("--out", default=OUTPUT_DIR, help="匯出資料夾")
    args = ap.parse_args()

    bad = [s for s in args.symbols if s not in SYMBOLS]
    if bad:
        print(f"[錯誤] 未知商品 {bad}，可用：{list(SYMBOLS.keys())}")
        return 1
    return check_folder(args.folder, args.symbols, args.out,
                        method=args.method, n_confirm=args.n_confirm)


if __name__ == "__main__":
    sys.exit(main())
