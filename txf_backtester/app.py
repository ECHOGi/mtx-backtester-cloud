# -*- coding: utf-8 -*-
"""MTX 台指期回測平台 v0.8.2｜自動契約換算版（精簡介面）。

所有操作集中在左側；中央只呈現回測結果。
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backtester import CostModel
from batch_utils import apply_position_mode, parse_strategy_batch
from config import DEFAULT_SYMBOL, SYMBOLS
from continuous_contract import build_session_continuous
from data_loader import clean_data, load_folder
from google_drive_uploader import (download_drive_file_bytes,
                                   list_json_files_in_drive_folder,
                                   upload_zip_result_to_drive)
from monte_carlo_batch import run_batch_monte_carlo

APP_VERSION = "v0.8.2"
APP_RELEASE_NAME = "自動契約換算版"
DEFAULT_GDRIVE_RESULTS_PARENT_FOLDER_ID = "1KhjGNzHqPTXzIcDEM_fy0clOCZoy25Fa"
DEFAULT_GDRIVE_STRATEGY_FOLDER_ID = "1boC1wtRriJv1SADAOZ-d9uA3KLkmqWtR"


def _safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", str(text).strip()) or "未命名"


def _record_dir() -> Path:
    env = os.environ.get("MTX_TEST_RECORD_DIR")
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    candidates = [env, home / "我的雲端硬碟" / "MTX Test Record",
                  Path(r"G:\我的雲端硬碟\MTX Test Record")]
    for p in candidates:
        if p and Path(p).exists():
            return Path(p)
    return Path(candidates[1])


def _secret(key: str, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return os.environ.get(key, default)


def _drive_auth():
    client_id = _secret("GDRIVE_OAUTH_CLIENT_ID")
    client_secret = _secret("GDRIVE_OAUTH_CLIENT_SECRET")
    refresh_token = _secret("GDRIVE_OAUTH_REFRESH_TOKEN")
    if client_id and client_secret and refresh_token:
        return {"auth_type": "oauth", "client_id": client_id,
                "client_secret": client_secret, "refresh_token": refresh_token,
                "token_uri": _secret("GDRIVE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token")}
    try:
        if "gcp_service_account" in st.secrets:
            return {"auth_type": "service_account",
                    "service_account_info": dict(st.secrets["gcp_service_account"])}
    except Exception:
        pass
    return None


@st.cache_data(ttl=60, show_spinner=False)
def _cloud_files(auth_json: str, folder_id: str):
    return list_json_files_in_drive_folder(json.loads(auth_json), folder_id)


def _default_prepared_path() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "data" / "prepared" / "MTX_stable_rollover_sessions.csv",
        here.parent / "data" / "prepared" / "MTX_stable_rollover_sessions.csv",
        Path("data/prepared/MTX_stable_rollover_sessions.csv"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _add_previous_trade_date(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    dates = sorted(pd.to_datetime(out["trade_date"], errors="coerce").dropna().dt.normalize().unique())
    mapping = {pd.Timestamp(d): (pd.Timestamp(dates[i - 1]) if i > 0 else pd.Timestamp(d) - pd.offsets.BDay(1))
               for i, d in enumerate(dates)}
    normalized = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
    out["previous_trade_date"] = normalized.map(mapping)
    return out


@st.cache_data(show_spinner=False)
def _load_session_data(path_text: str, symbol: str) -> pd.DataFrame:
    path = Path(path_text)
    if path.is_file():
        df = pd.read_csv(path, encoding="utf-8-sig")
        for c in ["datetime", "trade_date"]:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        if "trade_date" not in df.columns:
            df["trade_date"] = pd.to_datetime(df["datetime"], errors="coerce").dt.normalize()
        df = df.dropna(subset=["trade_date"]).sort_values(["trade_date", "session"]).reset_index(drop=True)
        return _add_previous_trade_date(df)
    if path.is_dir():
        raw = load_folder(str(path), skip_bad=True)
        clean = clean_data(raw, symbol=symbol, session="all")
        sessions, _ = build_session_continuous(clean, method="stable_rollover", n_confirm=3)
        return _add_previous_trade_date(sessions)
    raise FileNotFoundError(f"找不到資料：{path}")


def _apply_timeframe_mode(cfg: dict, mode: str) -> dict:
    out = json.loads(json.dumps(cfg, ensure_ascii=False))
    if mode == "依策略JSON":
        return out
    if mode == "完整日K":
        out["timeframe"] = "1D"
        out["multi_timeframe"] = {"enabled": False}
    elif mode == "模擬60分K":
        out["timeframe"] = "60m"
        out["multi_timeframe"] = {"enabled": False}
    elif mode == "模擬30分K":
        out["timeframe"] = "30m"
        out["multi_timeframe"] = {"enabled": False}
    else:
        out["timeframe"] = "30m"
        out["multi_timeframe"] = {
            "enabled": True,
            "long_signal_timeframe": "1D",
            "short_signal_timeframe": "60m",
            "execution_timeframe": "30m",
            "long_exit_signal_timeframe": "1D",
            "short_exit_signal_timeframe": "60m",
        }
    return out


def _cfg_value(cfg: dict, key: str, default=None):
    for section in ("position_policy", "exit", "research", "holding_policy"):
        values = cfg.get(section) or {}
        if key in values:
            return values[key]
    return cfg.get(key, default)


def _position_basis_text(cfg: dict) -> str:
    mode = str(_cfg_value(cfg, "position_sizing_mode", "fixed"))
    compounding = bool(_cfg_value(cfg, "position_compounding", False))
    mix = str(_cfg_value(cfg, "position_contract_mix_mode", "small_micro_only"))
    if mix in {"min_contract_count", "min_contracts", "auto_min_contracts"}:
        mix_text = "｜契約自動換算：大台→小台→微台，總口數最少"
    else:
        mix_text = ""
    if mode == "fixed":
        return "固定口數（不複利）" + mix_text
    if compounding:
        return f"{mode}｜獲利與虧損皆隨權益複利增減口數{mix_text}"
    return f"{mode}｜舊口徑：獲利不加口、虧損會減口{mix_text}"


def _exit_override_table(cfg: dict) -> pd.DataFrame:
    common = cfg.get("exit") or {}
    long_exit = cfg.get("long_exit") or cfg.get("exit_long") or {}
    short_exit = cfg.get("short_exit") or cfg.get("exit_short") or {}
    keys = sorted(set(long_exit) | set(short_exit))
    rows = []
    for key in keys:
        rows.append({
            "欄位": key,
            "共用值": common.get(key, "—"),
            "多單覆寫": long_exit.get(key, "沿用共用"),
            "空單覆寫": short_exit.get(key, "沿用共用"),
        })
    return pd.DataFrame(rows)


def _result_zip(batch_name: str, raw_json: str, result: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_批次比較.csv", result["comparison"].to_csv(index=False).encode("utf-8-sig"))
        z.writestr("01_隨機路徑分布.csv", result["distribution"].to_csv(index=False).encode("utf-8-sig"))
        z.writestr("strategy_batch.json", raw_json.encode("utf-8"))
        z.writestr("02_執行設定.json", json.dumps(result.get("run_settings", {}), ensure_ascii=False, indent=2).encode("utf-8"))
        readme = [
            f"# {batch_name}", "", f"- 平台：{APP_VERSION}",
            f"- 實際執行路徑：{len(result['seeds'])}",
            f"- 原要求路徑：{len(result.get('requested_seeds', result['seeds']))}",
            f"- 斷頭檢查：{'啟用' if result.get('run_settings', {}).get('use_margin_call_check') else '停用'}",
            f"- 固定口數安全緩衝：{result.get('run_settings', {}).get('safety_buffer_amount', 0):,.0f} 元",
        ]
        if result.get("deterministic_1d_fast_mode"):
            readme.append("- 純日K屬確定性回測，已自動縮為單路徑，避免重複計算相同結果。")
        else:
            readme += [
                "- 30分K為受日夜OHLC約束的隨機模擬；60分K由同一套30分K聚合。",
                "- 模擬資料不是歷史真實盤中行情。",
            ]
        validation = result.get("simulation_validation") or {}
        readme.append(f"- 模擬OHLCV還原驗證：{validation.get('status', '未執行')}")
        readme += ["", "## 各策略部位／複利口徑"]
        for name, rep in result["representatives"].items():
            readme.append(f"- {name}：{_position_basis_text(rep['config'])}")
        readme.append("")
        z.writestr("README_結果說明.md", "\n".join(readme).encode("utf-8"))
        for name, rep in result["representatives"].items():
            prefix = _safe_name(name) + "/"
            z.writestr(prefix + "代表路徑_交易明細.csv", rep["trades"].to_csv(index=False).encode("utf-8-sig"))
            z.writestr(prefix + "代表路徑_資金曲線.csv", rep["equity"].to_csv(index=False).encode("utf-8-sig"))
            z.writestr(prefix + "代表路徑_年度統計.csv", rep["yearly"].to_csv(index=False).encode("utf-8-sig"))
            z.writestr(prefix + "策略設定.json", json.dumps(rep["config"], ensure_ascii=False, indent=2).encode("utf-8"))
            z.writestr(prefix + "代表seed.txt", str(rep["seed"]).encode("utf-8"))
    return buf.getvalue()


def _equity_figure(eq: pd.DataFrame, initial_capital: float) -> go.Figure:
    frame = eq.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    if "account_equity" in frame.columns:
        account = pd.to_numeric(frame["account_equity"], errors="coerce")
    else:
        account = float(initial_capital) + pd.to_numeric(frame["equity"], errors="coerce").fillna(0)
    frame["帳戶權益"] = account
    peak = account.cummax()
    dd = account - peak
    dd_end = dd.idxmin() if len(dd) else None
    dd_start = account.loc[:dd_end].idxmax() if dd_end is not None else None

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=frame["datetime"], y=frame["帳戶權益"], mode="lines",
                             name="帳戶權益", line=dict(color="#356D60", width=2)))
    if dd_start is not None and dd_end is not None and dd.loc[dd_end] < 0:
        x0 = frame.loc[dd_start, "datetime"]
        x1 = frame.loc[dd_end, "datetime"]
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=0, y1=1, yref="paper",
                      fillcolor="rgba(210,102,76,.15)", line_width=0, layer="below")
        fig.add_annotation(x=x0, y=1, yref="paper", text="最大回撤區間",
                           showarrow=False, xanchor="left", yanchor="bottom")
    if "maintenance_margin_amount" in frame.columns:
        maint = pd.to_numeric(frame["maintenance_margin_amount"], errors="coerce").fillna(0)
        if maint.max() > 0:
            fig.add_trace(go.Scatter(x=frame["datetime"], y=maint, mode="lines",
                                     name="維持保證金／斷頭線",
                                     line=dict(color="#C45D4C", dash="dash", width=1.5)))
    if "account_disabled" in frame.columns and frame["account_disabled"].astype(bool).any():
        first = frame.loc[frame["account_disabled"].astype(bool), "datetime"].iloc[0]
        fig.add_shape(type="line", x0=first, x1=first, y0=0, y1=1, yref="paper",
                      line=dict(color="#8A3340", dash="dot", width=1.5))
        fig.add_annotation(x=first, y=1, yref="paper", text="斷頭後停止交易",
                           showarrow=False, xanchor="left", yanchor="top")
    fig.update_layout(height=330, margin=dict(l=15, r=15, t=35, b=15),
                      paper_bgcolor="white", plot_bgcolor="white",
                      yaxis_title="帳戶權益（元）", legend=dict(orientation="h"))
    return fig


st.set_page_config(page_title=f"MTX 回測 {APP_VERSION}", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
:root { --bg:#F7FAF9; --panel:#FFFFFF; --ink:#334155; --muted:#6B7A80; --line:#DDE8E3;
        --green:#467F70; --green2:#315F54; --soft:#EAF4F0; --orange:#C98255; --danger:#C45D4C; }
.stApp { background:var(--bg); color:var(--ink); }
.block-container { max-width:1500px; padding-top:1rem; }
[data-testid="stSidebar"] { background:#FFF8F0; border-right:1px solid #EAD9C9; }
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none; }
.hero { background:linear-gradient(135deg,#fff,#EDF5F1); border:1px solid var(--line); border-radius:20px;
        padding:20px 24px; margin-bottom:14px; box-shadow:0 8px 22px rgba(45,70,60,.06); }
.hero .eyebrow { color:var(--green); font-size:.76rem; font-weight:800; letter-spacing:.1em; }
.hero .title { font-size:1.65rem; font-weight:900; margin:3px 0; }
.hero .sub { color:var(--muted); font-size:.9rem; }
.sidebrand { background:#fff; border:1px solid #EAD9C9; border-radius:16px; padding:13px 14px; margin-bottom:10px; }
.sidebrand b { font-size:1.05rem; } .sidebrand span { color:var(--green); font-weight:800; }
.section { font-size:.78rem; font-weight:850; color:#855B43; margin:12px 0 5px; letter-spacing:.05em; }
.result-note { background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px 15px; color:var(--muted); }
.best-banner { background:linear-gradient(90deg,#E7F3EE,#FFF); border:1px solid #BFD8CE; border-radius:14px;
               padding:11px 15px; margin:8px 0 12px; font-weight:800; color:#315F54; }
.badge-row { display:flex; gap:8px; flex-wrap:wrap; margin:6px 0 12px; }
.badge { border-radius:999px; padding:5px 10px; font-size:.8rem; font-weight:750; border:1px solid var(--line); background:#fff; }
[data-testid="stMetric"] { background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px; }
div[data-testid="stButton"] > button[kind="primary"] { background:var(--green); border-color:var(--green); font-weight:800; }
div[data-testid="stButton"] > button[kind="primary"]:hover { background:var(--green2); border-color:var(--green2); }
[data-testid="stSidebar"] .stExpander { background:rgba(255,255,255,.55); border-radius:12px; }
@media(max-width:768px){ .block-container{padding:.5rem}.hero{padding:14px}.hero .title{font-size:1.3rem} }
</style>
""", unsafe_allow_html=True)

prepared_default = str(_default_prepared_path())

with st.sidebar:
    st.markdown(f'<div class="sidebrand"><b>MTX 回測平台</b> <span>{APP_VERSION}</span><br><small>{APP_RELEASE_NAME}</small></div>', unsafe_allow_html=True)
    st.markdown('<div class="section">策略</div>', unsafe_allow_html=True)
    auth = _drive_auth()
    source_options = (["Google Drive 投放箱"] if auth else []) + ["上傳 JSON", "本機投放箱"]
    source = st.selectbox("策略來源", source_options, label_visibility="collapsed")
    raw_json = ""
    display_name = ""
    selected_drive_id = None
    if source == "Google Drive 投放箱":
        folder_id = _secret("GDRIVE_STRATEGY_FOLDER_ID", DEFAULT_GDRIVE_STRATEGY_FOLDER_ID)
        try:
            files = _cloud_files(json.dumps(auth, ensure_ascii=False), folder_id)
            if files:
                pick = st.selectbox("策略檔", range(len(files)), format_func=lambda i: files[i]["name"])
                display_name = files[pick]["name"]
                selected_drive_id = files[pick]["id"]
            else:
                st.caption("投放箱沒有 JSON")
        except Exception as e:
            st.warning(f"投放箱讀取失敗：{e}")
    elif source == "上傳 JSON":
        uploaded = st.file_uploader("策略 JSON", type=["json"], label_visibility="collapsed")
        if uploaded:
            raw_json = uploaded.getvalue().decode("utf-8-sig")
            display_name = uploaded.name
    else:
        inbox = _record_dir() / "_策略投放箱"
        inbox.mkdir(parents=True, exist_ok=True)
        files = sorted(inbox.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            pick = st.selectbox("策略檔", range(len(files)), format_func=lambda i: files[i].name)
            raw_json = files[pick].read_text(encoding="utf-8-sig")
            display_name = files[pick].name
        else:
            st.caption("本機投放箱沒有 JSON")
    st.caption("JSON含 sweep／sweeps 時會自動展開，單批最多50組策略。")

    st.markdown('<div class="section">回測方式</div>', unsafe_allow_html=True)
    timeframe_mode = st.selectbox("週期", ["依策略JSON", "完整日K", "模擬60分K", "模擬30分K", "日K多方＋60分空方＋30分執行"])
    path_count = st.select_slider("模擬路徑", options=[1, 5, 10, 20, 30, 50, 100], value=20)
    st.caption("若所有策略只需要完整日K，系統會自動改為單路徑快速模式。")

    st.markdown('<div class="section">部位</div>', unsafe_allow_html=True)
    position_label = st.selectbox("部位模式", ["依策略JSON", "固定1口", "動態安全資金", "動態安全資金＋口數上限"])
    mode_map = {"依策略JSON": "json", "固定1口": "fixed", "動態安全資金": "dynamic_safe_capital",
                "動態安全資金＋口數上限": "dynamic_safe_capital_capped"}
    initial_capital = st.number_input("初始資金", min_value=50000, value=500000, step=50000)
    if position_label.startswith("動態"):
        safe_per_small = st.number_input("每口小台安全資金", min_value=100000, value=500000, step=50000)
        position_compounding = st.checkbox("獲利後啟用複利加口", value=False,
                                           help="關閉＝舊口徑：獲利不加口、虧損仍會減口；開啟才使用完整複利。")
        if position_label.endswith("口數上限"):
            max_small = st.number_input("最大等值小台口數", min_value=1, value=10, step=1)
        else:
            max_small = 200
    else:
        safe_per_small, max_small, position_compounding = 500000, 10, False

    with st.expander("進階設定", expanded=False):
        symbol = st.selectbox("商品", list(SYMBOLS), index=list(SYMBOLS).index(DEFAULT_SYMBOL))
        data_path = st.text_input("資料位置", value=prepared_default)
        fee = st.number_input("單邊手續費", min_value=0.0, value=float(SYMBOLS[symbol]["fee"]), step=1.0)
        slippage = st.number_input("單邊滑價點數", min_value=0.0, value=float(SYMBOLS[symbol]["slippage_points"]), step=.5)
        use_tax = st.checkbox("計入期交稅", value=True)
        use_margin_call_check = st.checkbox("啟用斷頭檢查", value=True)
        st.caption("固定口數安全緩衝＝初始資金－原始保證金；動態部位依帳戶權益與維持保證金判斷。")
        base_seed = st.number_input("模擬 seed", min_value=0, max_value=2_000_000_000, value=20260712, step=1)

    try:
        preview = _load_session_data(data_path, symbol)
        dmin = pd.to_datetime(preview["trade_date"]).min().date()
        dmax = pd.to_datetime(preview["trade_date"]).max().date()
        dates = st.date_input("回測期間", value=(dmin, dmax), min_value=dmin, max_value=dmax)
        if isinstance(dates, (tuple, list)) and len(dates) == 2:
            start_date, end_date = dates
        else:
            start_date, end_date = dmin, dmax
        st.caption(f"資料：{dmin}～{dmax}｜{len(preview):,}個時段")
        data_error = ""
    except Exception as e:
        preview = pd.DataFrame()
        start_date = end_date = None
        data_error = str(e)
        st.error(f"資料無法讀取：{e}")

    run_clicked = st.button("開始回測", type="primary", use_container_width=True, disabled=bool(data_error))

if run_clicked:
    try:
        if source == "Google Drive 投放箱":
            if not selected_drive_id:
                raise ValueError("尚未選擇策略 JSON")
            raw_json = download_drive_file_bytes(auth, selected_drive_id).decode("utf-8-sig")
        if not raw_json.strip():
            raise ValueError("尚未載入策略 JSON")
        batch_name, items, batch_meta = parse_strategy_batch(raw_json, symbol=symbol)
        filtered = preview[(pd.to_datetime(preview["trade_date"]).dt.date >= start_date) &
                           (pd.to_datetime(preview["trade_date"]).dt.date <= end_date)].reset_index(drop=True)
        if len(filtered) < 40:
            raise ValueError("回測區間資料不足40個時段")
        final_items = []
        for name, cfg in items:
            cfg = _apply_timeframe_mode(cfg, timeframe_mode)
            cfg = apply_position_mode(
                cfg, mode_map[position_label], initial_capital,
                safe_capital_per_small=safe_per_small,
                max_small_contracts=max_small,
                position_compounding=position_compounding)
            exit_cfg = cfg.setdefault("exit", {})
            exit_cfg["position_large_fee"] = float(SYMBOLS["TX"]["fee"])
            exit_cfg["position_small_fee"] = float(fee) if symbol == "MTX" else float(SYMBOLS["MTX"]["fee"])
            exit_cfg["position_micro_fee"] = float(fee) if symbol == "TMF" else float(SYMBOLS["TMF"]["fee"])
            final_items.append((name, cfg))

        seeds = [int(base_seed + i * 7919) for i in range(int(path_count))]
        spec = SYMBOLS[symbol]
        original_margin = float(spec["margin_reference"])
        safety_buffer_amount = max(float(initial_capital) - original_margin, 0.0)
        cost = CostModel(
            point_value=float(spec["point_value"]), fee=float(fee),
            slippage_points=float(slippage),
            tax_rate=float(spec["tax_rate"]) if use_tax else 0.0,
            original_margin_amount=original_margin,
            use_margin_call_check=bool(use_margin_call_check),
            safety_buffer_amount=safety_buffer_amount)
        progress = st.sidebar.progress(0, text="準備回測")
        result = run_batch_monte_carlo(
            filtered, final_items, cost, seeds, float(initial_capital),
            progress_callback=lambda pct, txt: progress.progress(pct, text=txt))
        progress.empty()
        result["run_settings"] = {
            "initial_capital": float(initial_capital),
            "use_margin_call_check": bool(use_margin_call_check),
            "safety_buffer_amount": safety_buffer_amount,
            "original_margin_amount": original_margin,
            "position_ui_mode": position_label,
            "position_compounding_ui": bool(position_compounding),
            "timeframe_mode": timeframe_mode,
            "expanded_strategy_count": len(final_items),
        }
        zip_bytes = _result_zip(batch_name, raw_json, result)
        stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"MTX_模擬回測_{stamp}_{_safe_name(batch_name)}.zip"
        st.session_state["v081_result"] = {
            "batch_name": batch_name, "display_name": display_name,
            "result": result, "zip": zip_bytes, "zip_name": zip_name,
            "start": str(start_date), "end": str(end_date),
            "requested_paths": int(path_count), "effective_paths": len(result["seeds"]),
            "initial_capital": float(initial_capital),
        }
        if auth:
            parent = _secret("GDRIVE_RESULTS_PARENT_FOLDER_ID", DEFAULT_GDRIVE_RESULTS_PARENT_FOLDER_ID)
            try:
                uploaded_info = upload_zip_result_to_drive(
                    auth_config=auth, parent_folder_id=parent,
                    result_folder_name=Path(zip_name).stem, zip_name=zip_name, zip_bytes=zip_bytes)
                st.session_state["v081_result"]["drive_url"] = uploaded_info.get("folder_url", "")
            except Exception as e:
                st.session_state["v081_result"]["upload_error"] = str(e)
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"回測失敗：{e}")

state = st.session_state.get("v081_result")
st.markdown(f'''<div class="hero"><div class="eyebrow">MONTE CARLO FUTURES RESEARCH</div>
<div class="title">台指期多週期安全回測</div>
<div class="sub">日夜盤OHLC約束模擬｜30分K生成｜60分K一致聚合｜多空獨立出場｜可選複利口數｜斷頭檢查</div></div>''', unsafe_allow_html=True)

if not state:
    st.markdown('<div class="result-note">請在左側選擇策略、週期與部位模式後開始回測。中央區只呈現回測結果。</div>', unsafe_allow_html=True)
else:
    result = state["result"]
    compare = result["comparison"]
    best = compare.iloc[0] if not compare.empty else None
    st.caption(f"{state['batch_name']}｜{state['start']}～{state['end']}｜實際{state['effective_paths']}條路徑（原設定{state['requested_paths']}）")

    badge_html = []
    if result.get("deterministic_1d_fast_mode"):
        badge_html.append('<span class="badge">⚡ 純日K單路徑快速模式</span>')
    validation = result.get("simulation_validation") or {}
    if validation.get("status") == "通過":
        badge_html.append(f'<span class="badge">✓ 模擬OHLCV還原通過（{validation.get("checked_seeds", 0)}條）</span>')
    elif validation.get("status") == "失敗":
        badge_html.append(f'<span class="badge">⚠ 模擬還原失敗（{validation.get("error_count", 0)}項）</span>')
    if result.get("run_settings", {}).get("use_margin_call_check"):
        badge_html.append('<span class="badge">✓ 斷頭檢查已啟用</span>')
    st.markdown('<div class="badge-row">' + ''.join(badge_html) + '</div>', unsafe_allow_html=True)

    if best is not None:
        st.markdown(f'<div class="best-banner">最佳策略：{best["策略名稱"]}</div>', unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("年化報酬率中位數", f"{float(best.get('年化報酬率中位數(%)', 0)):.2f}%")
        c2.metric("報酬回撤比中位數", best.get("報酬回撤比中位數", "—"))
        c3.metric("最大回撤中位數", f"{float(best.get('最大回撤中位數', 0)):,.0f}")
        c4.metric("獲利因子中位數", best.get("獲利因子中位數", "—"))

    st.subheader("策略穩健度比較")
    compare_cols = [c for c in [
        "策略名稱", "報酬回撤比中位數", "總損益中位數", "總損益P25",
        "最大回撤中位數", "年化報酬率中位數(%)", "獲利路徑比例(%)",
        "獲利因子中位數", "交易次數中位數", "期末強制平倉損益中位數",
        "歷史最低運作資金中位數", "斷頭路徑數"] if c in compare.columns]
    compare_view = compare[compare_cols].copy()
    def _highlight_best(row):
        return ["background-color:#E6F2ED;font-weight:750" if row.name == 0 else "" for _ in row]
    st.dataframe(compare_view.style.apply(_highlight_best, axis=1), use_container_width=True, hide_index=True)

    if not compare.empty:
        scatter = go.Figure()
        scatter.add_trace(go.Scatter(
            x=compare["最大回撤中位數"].abs(), y=compare["總損益中位數"],
            mode="markers+text", text=compare["策略名稱"], textposition="top center",
            marker=dict(size=12 + compare["獲利路徑比例(%)"].fillna(0) / 10,
                        color=compare["報酬回撤比中位數"], colorscale="Teal", showscale=True,
                        colorbar=dict(title="報酬回撤比")),
            customdata=compare[["獲利路徑比例(%)", "年化報酬率中位數(%)"]],
            hovertemplate="%{text}<br>回撤絕對值=%{x:,.0f}<br>損益=%{y:,.0f}<br>獲利路徑=%{customdata[0]:.1f}%<br>年化=%{customdata[1]:.2f}%<extra></extra>"))
        scatter.update_layout(title="報酬與回撤散點圖", xaxis_title="最大回撤中位數絕對值（元）",
                              yaxis_title="總損益中位數（元）", height=430,
                              margin=dict(l=20, r=20, t=55, b=20), paper_bgcolor="white", plot_bgcolor="white")
        st.plotly_chart(scatter, use_container_width=True)

    dist = result["distribution"]
    if not dist.empty:
        fig = go.Figure()
        for name, grp in dist.groupby("策略名稱", sort=False):
            fig.add_trace(go.Box(y=grp["總損益(元)"], name=name, boxmean=True))
        fig.update_layout(title="各隨機路徑總損益分布", yaxis_title="總損益（元）",
                          margin=dict(l=20, r=20, t=55, b=20), height=430,
                          paper_bgcolor="white", plot_bgcolor="white", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    if validation.get("status") == "失敗" and validation.get("errors"):
        with st.expander("模擬還原錯誤", expanded=True):
            st.code("\n".join(validation["errors"]))

    st.subheader("代表路徑明細")
    for name in compare["策略名稱"].tolist():
        rep = result["representatives"][name]
        m = rep["metrics"]
        with st.expander(f"{name}｜代表 seed {rep['seed']}", expanded=(name == compare.iloc[0]["策略名稱"])):
            st.caption(f"部位口徑：{_position_basis_text(rep['config'])}")
            a, b, c, d, e = st.columns(5)
            a.metric("總損益", f"{float(m.get('總損益(元)', 0)):,.0f}")
            b.metric("最大回撤", f"{float(m.get('最大回撤(元)', 0)):,.0f}")
            c.metric("交易次數", int(m.get("交易次數", 0)))
            d.metric("期末強平損益", f"{float(m.get('期末強制平倉損益(元)', 0)):,.0f}")
            e.metric("最低運作資金", f"{float(m.get('歷史最低運作資金(元)', 0) or 0):,.0f}")
            eq = rep["equity"].copy()
            if not eq.empty:
                st.plotly_chart(_equity_figure(eq, float(state.get("initial_capital", 500000))), use_container_width=True)

            override_df = _exit_override_table(rep["config"])
            if not override_df.empty:
                st.markdown("**多空出場覆寫差異**")
                st.dataframe(override_df, use_container_width=True, hide_index=True)
            else:
                st.caption("多空出場未設定覆寫，沿用共用出場規則。")

            trades = rep["trades"].copy()
            trade_columns = {
                "entry_date": "進場時間", "exit_date": "出場時間", "direction": "方向",
                "entry_price": "進場價", "exit_price": "出場價",
                "large_quantity": "大台口數", "small_quantity": "小台口數", "micro_quantity": "微台口數",
                "position_action": "口數變化", "position_compounding": "複利啟用",
                "position_equity_basis": "部位權益口徑", "available_equity_at_entry": "進場前部位計算權益",
                "effective_leverage": "有效槓桿", "margin_utilization_pct": "保證金占用率(%)",
                "safe_capital_balance": "安全資金餘額", "pnl_amount": "損益(元)",
                "holding_bars": "持有K棒", "exit_reason": "出場原因"}
            visible = [c for c in trade_columns if c in trades.columns]
            st.dataframe(trades[visible].rename(columns=trade_columns), use_container_width=True, hide_index=True)

    with st.sidebar:
        st.markdown('<div class="section">結果</div>', unsafe_allow_html=True)
        if state.get("drive_url"):
            st.success("已上傳 Google Drive")
        elif state.get("upload_error"):
            st.warning("雲端上傳失敗，仍可下載 ZIP")
        st.download_button("下載完整結果 ZIP", state["zip"], file_name=state["zip_name"],
                           mime="application/zip", use_container_width=True)
