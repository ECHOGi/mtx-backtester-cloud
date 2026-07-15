# -*- coding: utf-8 -*-
"""MTX 台指期回測平台 v0.8.7.7｜多空吊燈週期隔離修正版。

所有操作集中在左側；中央只呈現回測與情境比較結果。
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
from benchmark_00631l import (BENCHMARK_NAME, benchmark_metrics,
                              historical_buy_hold_curve, load_benchmark)
from checkpointing import (append_rows as append_checkpoint_rows, checkpoint_paths,
                           clear_checkpoint, make_signature, prepare_resume,
                           read_meta as read_checkpoint_meta, write_meta as write_checkpoint_meta)
from event_checkpointing import (clear_event_checkpoint, event_meta_path,
                                 prepare_event_resume, save_event_unit)
from future_scenarios import ScenarioConfig, run_cutoff_scenarios
from config import DEFAULT_SYMBOL, SYMBOLS
from continuous_contract import build_session_continuous
from data_loader import clean_data, load_folder
from google_drive_uploader import (download_drive_file_bytes,
                                   list_json_files_in_drive_folder,
                                   upload_zip_result_to_drive)
from monte_carlo_batch import run_batch_event_monte_carlo, run_batch_monte_carlo

_VERSION_FALLBACK = {
    "version": "v0.8.7.7",
    "release_name": "多空吊燈週期隔離修正版",
    "build_id": "20260716-1",
}
try:
    _version_info = json.loads((Path(__file__).resolve().parent / "version.json").read_text(encoding="utf-8"))
except Exception:
    _version_info = _VERSION_FALLBACK
APP_VERSION = str(_version_info.get("version", _VERSION_FALLBACK["version"]))
APP_RELEASE_NAME = str(_version_info.get("release_name", _VERSION_FALLBACK["release_name"]))
APP_BUILD_ID = str(_version_info.get("build_id", _VERSION_FALLBACK["build_id"]))
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


def _benchmark_cache_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "benchmark" / "00631L_twse.csv"


@st.cache_data(ttl=3600, show_spinner=False)
def _load_benchmark_official(start_text: str, end_text: str, cache_text: str, refresh: bool = False):
    return load_benchmark(start_text, end_text, cache_path=cache_text, refresh=refresh)


def _parse_benchmark_upload(uploaded_file) -> pd.DataFrame | None:
    if uploaded_file is None:
        return None
    raw = uploaded_file.getvalue()
    for enc in ("utf-8-sig", "cp950", "big5"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc)
        except Exception:
            continue
    raise ValueError("00631L CSV無法辨識編碼")


def _default_cutoffs(dmin, dmax) -> list[pd.Timestamp]:
    start_y, end_y = pd.Timestamp(dmin).year, pd.Timestamp(dmax).year
    targets = []
    for year in range(max(start_y, 2021), end_y + 1):
        target = pd.Timestamp(year, 12, 31)
        if year == end_y:
            target = pd.Timestamp(dmax)
        if pd.Timestamp(dmin) < target <= pd.Timestamp(dmax):
            targets.append(target)
    if pd.Timestamp(dmax).normalize() not in [x.normalize() for x in targets]:
        targets.append(pd.Timestamp(dmax).normalize())
    return sorted(set(targets))


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
    brake_text = ""
    if mode == "dynamic_risk" and bool(_cfg_value(cfg, "use_drawdown_risk_brake", False)):
        start = float(_cfg_value(cfg, "position_drawdown_brake_start_pct", 0.0) or 0.0)
        full = float(_cfg_value(cfg, "position_drawdown_brake_full_pct", 0.0) or 0.0)
        floor = float(_cfg_value(cfg, "position_drawdown_brake_floor", 1.0) or 1.0)
        brake_text = f"｜已實現權益回撤{start:g}%啟動煞車，{full:g}%降至{floor:.0%}風險"
    if mode == "fixed":
        return "固定口數（不複利）" + mix_text
    if compounding:
        return f"{mode}｜獲利與虧損皆隨權益複利增減口數{brake_text}{mix_text}"
    return f"{mode}｜舊口徑：獲利不加口、虧損會減口{brake_text}{mix_text}"


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
        if isinstance(result.get("event_distribution"), pd.DataFrame) and not result["event_distribution"].empty:
            z.writestr("02_五次事件逐策略分布.csv", result["event_distribution"].to_csv(index=False).encode("utf-8-sig"))
        z.writestr("strategy_batch.json", raw_json.encode("utf-8"))
        z.writestr("02_執行設定.json", json.dumps(result.get("run_settings", {}), ensure_ascii=False, indent=2).encode("utf-8"))
        if isinstance(result.get("benchmark_data"), pd.DataFrame) and not result["benchmark_data"].empty:
            z.writestr("03_00631L正二基準_分割調整資料.csv",
                       result["benchmark_data"].to_csv(index=False).encode("utf-8-sig"))
        if result.get("benchmark_metrics"):
            z.writestr("04_00631L正二基準績效.json",
                       json.dumps(result["benchmark_metrics"], ensure_ascii=False, indent=2).encode("utf-8"))
        scenario = result.get("scenario_analysis") or {}
        if isinstance(scenario.get("comparison"), pd.DataFrame) and not scenario["comparison"].empty:
            z.writestr("05_多截止日未來情境_策略比較.csv",
                       scenario["comparison"].to_csv(index=False).encode("utf-8-sig"))
            z.writestr("06_多截止日未來情境_完整分布.csv",
                       scenario["distribution"].to_csv(index=False).encode("utf-8-sig"))
            z.writestr("07_各市場狀態比較.csv",
                       scenario["state_summary"].to_csv(index=False).encode("utf-8-sig"))
        readme = [
            f"# {batch_name}", "", f"- 平台：{APP_VERSION}",
            f"- 實際執行路徑：{len(result['seeds'])}",
            f"- 原要求路徑：{len(result.get('requested_seeds', result['seeds']))}",
            f"- 斷頭檢查：{'啟用' if result.get('run_settings', {}).get('use_margin_call_check') else '停用'}",
            f"- 固定口數安全緩衝：{result.get('run_settings', {}).get('safety_buffer_amount', 0):,.0f} 元",
        ]
        if result.get("event_mode"):
            readme += [
                "- 本批採事件區間加速回測；只允許在指定五次事件期間進場。",
                f"- 指標暖機：每個事件向前保留 {result.get('event_warmup_trade_days', 0)} 個交易日。",
                "- 各事件結束時仍持倉者以事件最後一根收盤強制回補，避免跨事件持倉。",
                "- 事件區間：" + "；".join(
                    f"{x.get('label')} {x.get('start')}～{x.get('end')}"
                    for x in result.get("event_windows", [])),
                "- 事件模式不執行00631L比較；找到穩定空單後再回完整期間及多空合併。",
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
        if result.get("benchmark_metrics"):
            bm = result["benchmark_metrics"]
            readme += ["", "## 正二基準", f"- {bm.get('基準名稱', BENCHMARK_NAME)}",
                       f"- 年化報酬率：{bm.get('年化報酬率(%)', '—')}%",
                       f"- 最大回撤率：{bm.get('最大回撤率(%)', '—')}%"]
        scenario = result.get("scenario_analysis") or {}
        if scenario:
            readme += ["", "## 多截止日未來情境",
                       f"- 截止日：{', '.join(scenario.get('cutoff_dates', []))}",
                       f"- 未來延伸：{scenario.get('future_days', '—')}個交易日",
                       f"- 每種情境路徑數：{result.get('run_settings', {}).get('scenario_paths_per_state', '—')}",
                       f"- 正式排名口徑：{scenario.get('ranking_basis', '共同路徑期末總權益')}",
                       "- 未自然出場只表示模擬終點仍有持倉，不作為必須消除的錯誤。",
                       f"- 情境：{', '.join(scenario.get('scenario_states', []))}",
                       "- 回撤煞車觀察：完整分布與策略比較均包含觸發次數、煞車狀態交易日占比、平均與最低每日煞車倍率。"]
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
    st.markdown(f'<div class="sidebrand"><b>MTX 回測平台</b> <span>{APP_VERSION}</span><br><small>{APP_RELEASE_NAME}<br>建置：{APP_BUILD_ID}</small></div>', unsafe_allow_html=True)
    st.markdown('<div class="section">策略</div>', unsafe_allow_html=True)
    auth = _drive_auth()
    source_options = (["Google Drive 投放箱"] if auth else []) + ["上傳 JSON", "本機投放箱"]
    source = st.selectbox("策略來源", source_options, label_visibility="collapsed", key="strategy_source")
    raw_json = ""
    display_name = ""
    selected_drive_id = None
    if source == "Google Drive 投放箱":
        folder_id = _secret("GDRIVE_STRATEGY_FOLDER_ID", DEFAULT_GDRIVE_STRATEGY_FOLDER_ID)
        try:
            files = _cloud_files(json.dumps(auth, ensure_ascii=False), folder_id)
            if files:
                file_map = {str(item["id"]): str(item["name"]) for item in files}
                selected_drive_id = st.selectbox(
                    "策略檔", options=list(file_map),
                    format_func=lambda file_id: file_map.get(str(file_id), str(file_id)),
                    key="drive_strategy_file_id_v0864")
                display_name = file_map.get(str(selected_drive_id), "")
                st.caption(f"目前選擇：{display_name}")
                if st.button("重新整理策略清單", key="refresh_drive_strategy_list", use_container_width=True):
                    _cloud_files.clear()
                    st.rerun()
            else:
                st.caption("投放箱沒有 JSON")
                if st.button("重新整理策略清單", key="refresh_empty_drive_strategy_list", use_container_width=True):
                    _cloud_files.clear()
                    st.rerun()
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
            pick = st.selectbox("策略檔", range(len(files)), format_func=lambda i: files[i].name, key="local_strategy_pick")
            raw_json = files[pick].read_text(encoding="utf-8-sig")
            display_name = files[pick].name
        else:
            st.caption("本機投放箱沒有 JSON")
    st.caption("JSON含 sweep／sweeps 時會自動展開，單批最多50組策略。")

    st.markdown('<div class="section">回測方式</div>', unsafe_allow_html=True)
    research_mode = st.selectbox(
        "研究模式", ["標準回測", "多截止日＋未來情境＋正二比較"],
        help="標準回測檢查完整歷史結果；多截止日模式會在多個歷史終點後接上六種未來日K情境，並與00631L正二使用相同未來來源區段比較。")
    timeframe_mode = st.selectbox(
        "資料週期", ["依策略JSON", "完整日K", "模擬60分K", "模擬30分K", "日K多方＋60分空方＋30分執行"],
        help="依策略JSON會讀取每個策略自己的週期設定。完整日K不受盤中價格順序影響；30分與60分為受原始日夜OHLC約束的模擬K線。")
    path_count = st.select_slider(
        "盤中模擬情境數", options=[1, 5, 10, 20, 30, 50, 100], value=20,
        help="一條情境代表同一組日夜OHLC下，一種可能的盤中30分K走法。僅使用完整日K時不受盤中順序影響，系統會自動只回測一次。")
    st.caption("僅使用完整日K時，盤中模擬不影響結果，系統只回測一次。")

    st.markdown('<div class="section">部位</div>', unsafe_allow_html=True)
    position_label = st.selectbox(
        "部位模式", ["依策略JSON", "覆寫為安全約束動態複利"],
        help="安全約束動態複利會依當下帳戶權益增減曝險，但每次加口都必須同時通過安全資金、保證金、停損、跳空壓力與回撤準備檢查。")
    mode_map = {"依策略JSON": "json", "覆寫為安全約束動態複利": "dynamic_safe_capital"}
    initial_capital = st.number_input(
        "初始資金", min_value=50000, value=500000, step=50000,
        help="策略與00631L正二基準都使用相同起始資金。這是本次帳戶資金，不是單口保證金。")
    if position_label.startswith("覆寫"):
        safe_per_small = st.number_input(
            "每口小台等值安全資金", min_value=100000, value=500000, step=50000,
            help="每累積這筆可用安全資金，才允許增加1口小台等值曝險。這是安全政策，不應用歷史績效挑選甜蜜點。")
    else:
        safe_per_small = 500000
    max_small, position_compounding = 0, True

    st.markdown('<div class="section">正二基準</div>', unsafe_allow_html=True)
    benchmark_enabled = st.checkbox(
        "納入00631L正二比較", value=True,
        help="使用元大台灣50正2（00631L）實際歷史價格。2026年1拆22會以分割事件調整，不會被誤判為暴跌。")
    benchmark_source = st.selectbox(
        "正二資料來源", ["TWSE官方自動下載", "上傳00631L CSV"],
        disabled=not benchmark_enabled,
        help="自動下載會逐月讀取證交所日行情並快取；上傳模式至少需要日期與收盤價欄位。")
    benchmark_upload = None
    if benchmark_enabled and benchmark_source == "上傳00631L CSV":
        benchmark_upload = st.file_uploader("00631L歷史資料", type=["csv"], key="benchmark_csv",
                                            help="可使用原始未調整價格；平台會依1拆22事件建立調整後報酬。")
    benchmark_fee_rate = st.number_input(
        "正二買進手續費率(%)", min_value=0.0, value=0.1425, step=0.01,
        disabled=not benchmark_enabled,
        help="只在起始日買進一次，保留買不到整數股的現金。可依實際券商折扣調整。") / 100.0

    with st.expander("進階設定", expanded=False):
        symbol = st.selectbox("商品", list(SYMBOLS), index=list(SYMBOLS).index(DEFAULT_SYMBOL),
                              help="策略訊號資料來源。動態部位會自動用大台、小台、微台組成相同曝險且口數最少的組合。")
        data_path = st.text_input("資料位置", value=prepared_default,
                                  help="可指定已整理的日夜盤連續契約CSV，或原始資料資料夾。")
        fee = st.number_input("單邊手續費", min_value=0.0, value=float(SYMBOLS[symbol]["fee"]), step=1.0,
                              help="目前商品每口單邊手續費；自動契約換算後會依大台、小台、微台各自費用計算。")
        slippage = st.number_input("單邊滑價點數", min_value=0.0, value=float(SYMBOLS[symbol]["slippage_points"]), step=.5,
                                   help="每次進場與出場各扣除的價格滑價。換月價差尚未另外模擬。")
        use_tax = st.checkbox("計入期交稅", value=True, help="依成交價格與商品稅率估算期交稅。")
        use_margin_call_check = st.checkbox("啟用斷頭檢查", value=True,
                                            help="每日權益低於維持保證金時強制平倉；策略可設定斷頭後停止交易。")
        base_seed = st.number_input("模擬seed", min_value=0, max_value=2_000_000_000, value=20260712, step=1,
                                    help="相同seed會重現相同盤中與未來情境路徑，方便不同策略公平比較。")
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
        available_trade_dates = sorted(pd.to_datetime(preview["trade_date"], errors="coerce").dropna().dt.normalize().unique())
        cutoff_values = []
        for target in _default_cutoffs(dmin, dmax):
            eligible = [pd.Timestamp(x) for x in available_trade_dates if pd.Timestamp(x) <= target]
            if eligible:
                cutoff_values.append(max(eligible))
        cutoff_values = sorted(set(cutoff_values))
        if research_mode.startswith("多截止日"):
            if len(cutoff_values) <= 3:
                default_cutoffs = cutoff_values
            else:
                default_cutoffs = [cutoff_values[0], cutoff_values[len(cutoff_values)//2], cutoff_values[-1]]
            selected_cutoffs = st.multiselect(
                "共同歷史截止日", cutoff_values, default=default_cutoffs,
                format_func=lambda x: pd.Timestamp(x).strftime("%Y-%m-%d"),
                help="每個截止日都會重新建立當時可見的歷史，再接上六種共同未來日K情境；不是事後只扣除最後一筆浮盈。")
            future_paths_per_state = st.select_slider(
                "每種未來狀態路徑數", options=[1, 2, 3, 5, 10, 15, 20], value=5,
                help="六種未來狀態各產生相同數量的路徑。所有策略與正二共用相同來源日期與seed；正式壓力測試最多可選20條。")
            restart_scenario_checkpoint = st.checkbox(
                "忽略同設定的中斷進度，從頭重跑", value=False,
                help="平常不要勾選。長回測若因瀏覽器或工作階段中斷，再按開始回測會自動接續；只有需要刻意清除舊進度時才勾選。")
            st.caption("長回測每200筆寫入檢查點；正常結束或中斷前仍會補寫不足200筆的尾批。畫面重置後使用相同設定再按一次，即會接續已保存部分。")
        else:
            selected_cutoffs, future_paths_per_state = [], 0
            restart_scenario_checkpoint = st.checkbox(
                "忽略事件回測的中斷進度，從頭重跑", value=False,
                help="只有事件區間策略批次會使用。平常不要勾選；斷線後以相同設定再按開始回測，平台會自動接續已完成的策略×路徑×事件。")
            st.caption("事件區間回測每完成一個策略×seed×事件就立即保存；斷線後使用相同設定可自動續跑。")
        data_error = ""
    except Exception as e:
        preview = pd.DataFrame()
        start_date = end_date = None
        data_error = str(e)
        selected_cutoffs, future_paths_per_state = [], 0
        restart_scenario_checkpoint = False
        st.error(f"資料無法讀取：{e}")

    run_clicked = st.button("開始回測", type="primary", use_container_width=True, disabled=bool(data_error))

if run_clicked:
    st.session_state["v086_run_status"] = "running"
    st.session_state.pop("v086_result", None)
    progress = None
    active_checkpoint_signature = ""
    active_checkpoint_meta_path = None
    active_batch_name = ""
    try:
        if source == "Google Drive 投放箱":
            if not selected_drive_id:
                raise ValueError("尚未選擇策略 JSON")
            raw_json = download_drive_file_bytes(auth, selected_drive_id).decode("utf-8-sig")
        if not raw_json.strip():
            raise ValueError("尚未載入策略 JSON")
        batch_name, items, batch_meta = parse_strategy_batch(raw_json, symbol=symbol)
        event_windows = batch_meta.get("event_windows") or []
        event_mode = bool(event_windows)
        event_warmup_trade_days = int(batch_meta.get("event_warmup_trade_days", 140) or 140)
        if event_mode and research_mode.startswith("多截止日"):
            raise ValueError("事件區間批次請使用標準回測，不與多截止日未來情境同時執行")
        benchmark_enabled_for_run = bool(benchmark_enabled and not event_mode)
        if event_mode:
            labels = "、".join(str(x.get("label") or "事件") for x in event_windows)
            st.sidebar.info(f"事件區間加速模式：{labels}｜短K只生成指定區間與暖機資料")
            if benchmark_enabled:
                st.sidebar.caption("事件初選階段自動略過00631L；完整期間決選時再比較。")
        active_batch_name = batch_name
        st.sidebar.caption(f"執行中批次：{batch_name}")
        if event_mode:
            # 事件批次以JSON內日期為唯一口徑，避免側欄完整期間或手動日期誤裁掉事件暖機資料。
            filtered = preview.reset_index(drop=True)
        else:
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

        checkpoint_signature = ""
        checkpoint_rows_path = checkpoint_meta_path = None
        resume_distribution = pd.DataFrame()
        event_resume_units = {}
        event_resume_units_loaded = 0
        if event_mode:
            data_dates = pd.to_datetime(filtered["trade_date"], errors="coerce")
            checkpoint_payload = {
                "checkpoint_type": "event_units_v1",
                "platform": APP_VERSION,
                "batch_name": batch_name,
                "strategies": final_items,
                "data_rows": int(len(filtered)),
                "data_start": str(data_dates.min()),
                "data_end": str(data_dates.max()),
                "data_first_close": float(pd.to_numeric(filtered["close"], errors="coerce").iloc[0]),
                "data_last_close": float(pd.to_numeric(filtered["close"], errors="coerce").iloc[-1]),
                "event_windows": event_windows,
                "event_warmup_trade_days": int(event_warmup_trade_days),
                "path_count": int(path_count),
                "seed": int(base_seed),
                "initial_capital": float(initial_capital),
                "symbol": symbol, "fee": float(fee), "slippage": float(slippage),
                "use_tax": bool(use_tax), "margin_check": bool(use_margin_call_check),
            }
            checkpoint_signature = make_signature(checkpoint_payload)
            checkpoint_meta_path = event_meta_path(checkpoint_signature)
            active_checkpoint_signature = checkpoint_signature
            active_checkpoint_meta_path = checkpoint_meta_path
            event_resume_units, checkpoint_meta, cleared_completed_checkpoint = prepare_event_resume(
                checkpoint_signature, restart=bool(restart_scenario_checkpoint))
            event_resume_units_loaded = int(len(event_resume_units))
            if cleared_completed_checkpoint:
                st.sidebar.caption("已清除上一批完整事件檢查點，這次將建立新的回測工作。")
            if event_resume_units_loaded:
                st.sidebar.info(
                    f"發現同設定中斷進度：已完成{event_resume_units_loaded:,}個事件單元，將自動續跑。")
            write_checkpoint_meta(checkpoint_meta_path, {
                "signature": checkpoint_signature, "batch_name": batch_name,
                "done": event_resume_units_loaded, "total": None,
                "complete": False, "status": "running", "checkpoint_type": "event_units_v1",
                "updated_at": pd.Timestamp.now().isoformat(),
            })
        elif research_mode.startswith("多截止日"):
            data_dates = pd.to_datetime(filtered["trade_date"], errors="coerce")
            checkpoint_payload = {
                "platform": APP_VERSION,
                "batch_name": batch_name,
                "strategies": final_items,
                "data_rows": int(len(filtered)),
                "data_start": str(data_dates.min()),
                "data_end": str(data_dates.max()),
                "data_first_close": float(pd.to_numeric(filtered["close"], errors="coerce").iloc[0]),
                "data_last_close": float(pd.to_numeric(filtered["close"], errors="coerce").iloc[-1]),
                "cutoffs": [str(pd.Timestamp(x).date()) for x in selected_cutoffs],
                "paths_per_state": int(future_paths_per_state),
                "seed": int(base_seed),
                "initial_capital": float(initial_capital),
                "symbol": symbol, "fee": float(fee), "slippage": float(slippage),
                "use_tax": bool(use_tax), "margin_check": bool(use_margin_call_check),
                "benchmark_enabled": bool(benchmark_enabled_for_run),
                "benchmark_source": benchmark_source if benchmark_enabled_for_run else "停用",
                "benchmark_fee_rate": float(benchmark_fee_rate),
            }
            checkpoint_signature = make_signature(checkpoint_payload)
            checkpoint_rows_path, checkpoint_meta_path = checkpoint_paths(checkpoint_signature)
            active_checkpoint_signature = checkpoint_signature
            active_checkpoint_meta_path = checkpoint_meta_path
            resume_distribution, checkpoint_meta, cleared_completed_checkpoint = prepare_resume(
                checkpoint_signature, restart=bool(restart_scenario_checkpoint))
            if cleared_completed_checkpoint:
                st.sidebar.caption("已清除上一批完整檢查點，這次將建立新的回測工作。")
            if not resume_distribution.empty:
                st.sidebar.info(
                    f"發現同設定中斷進度：{len(resume_distribution):,}筆，將自動續跑。")
            write_checkpoint_meta(checkpoint_meta_path, {
                "signature": checkpoint_signature, "batch_name": batch_name,
                "done": int(len(resume_distribution)), "total": None,
                "complete": False, "status": "running",
                "updated_at": pd.Timestamp.now().isoformat(),
            })

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
        if event_mode:
            def _event_checkpoint_callback(unit, done, total):
                save_event_unit(checkpoint_signature, unit)
                write_checkpoint_meta(checkpoint_meta_path, {
                    "signature": checkpoint_signature, "batch_name": batch_name,
                    "done": int(done), "total": int(total),
                    "complete": False, "status": "running", "checkpoint_type": "event_units_v1",
                    "updated_at": pd.Timestamp.now().isoformat(),
                })

            result = run_batch_event_monte_carlo(
                filtered, final_items, cost, seeds, float(initial_capital),
                event_windows=event_windows, warmup_trade_days=event_warmup_trade_days,
                progress_callback=lambda pct, txt: progress.progress(pct, text="五次事件回測｜" + txt),
                resume_units=event_resume_units,
                checkpoint_callback=_event_checkpoint_callback)
        else:
            result = run_batch_monte_carlo(
                filtered, final_items, cost, seeds, float(initial_capital),
                progress_callback=lambda pct, txt: progress.progress(min(pct * 0.35, 0.35), text="歷史回測｜" + txt))

        benchmark_df = pd.DataFrame()
        benchmark_info = None
        if benchmark_enabled_for_run:
            if benchmark_source == "上傳00631L CSV":
                uploaded_benchmark = _parse_benchmark_upload(benchmark_upload)
                if uploaded_benchmark is None:
                    raise ValueError("已選擇上傳00631L CSV，但尚未選擇檔案")
                benchmark_df, benchmark_info = load_benchmark(
                    start_date, end_date, uploaded=uploaded_benchmark)
            else:
                benchmark_df, benchmark_info = _load_benchmark_official(
                    str(start_date), str(end_date), str(_benchmark_cache_path()), False)
            benchmark_part = benchmark_df[(benchmark_df["date"] >= pd.Timestamp(start_date)) &
                                          (benchmark_df["date"] <= pd.Timestamp(end_date))]
            benchmark_curve = historical_buy_hold_curve(
                benchmark_part, float(initial_capital), float(benchmark_fee_rate))
            result["benchmark_metrics"] = benchmark_metrics(benchmark_curve, float(initial_capital))
            bm_annual = float(result["benchmark_metrics"].get("年化報酬率(%)", 0.0))
            bm_end = float(result["benchmark_metrics"].get("期末資產(元)", initial_capital))
            bm_dd = float(result["benchmark_metrics"].get("最大回撤率(%)", 0.0))
            compare = result["comparison"]
            compare["策略期末總權益(元)"] = (
                float(initial_capital) + pd.to_numeric(compare["總損益中位數"], errors="coerce")
            ).round(0)
            compare["正二期末資產(元)"] = round(bm_end, 0)
            compare["期末資產差(元)"] = (compare["策略期末總權益(元)"] - bm_end).round(0)
            compare["策略年化報酬率(%)"] = pd.to_numeric(compare["年化報酬率中位數(%)"], errors="coerce").round(2)
            compare["正二年化報酬率(%)"] = round(bm_annual, 2)
            compare["相對正二年化差(百分點)"] = (compare["策略年化報酬率(%)"] - bm_annual).round(2)
            compare["策略最大回撤率(%)"] = pd.to_numeric(compare["最大回撤率中位數(%)"], errors="coerce").round(2)
            compare["正二最大回撤率(%)"] = round(bm_dd, 2)
            compare["相對正二回撤改善(百分點)"] = (compare["策略最大回撤率(%)"] - bm_dd).round(2)
            compare["歷史期末總權益超越正二"] = compare["策略期末總權益(元)"] > bm_end
            compare["歷史年化超越正二"] = compare["相對正二年化差(百分點)"] > 0
            result["comparison"] = compare
            result["benchmark_data"] = benchmark_df
            result["benchmark_curve"] = benchmark_curve
            result["benchmark_info"] = benchmark_info.__dict__ if benchmark_info else {}

        if research_mode.startswith("多截止日"):
            if not selected_cutoffs:
                raise ValueError("多截止日模式至少需要選擇一個共同截止日")
            def _checkpoint_callback(chunk, done, total):
                append_checkpoint_rows(checkpoint_rows_path, chunk)
                write_checkpoint_meta(checkpoint_meta_path, {
                    "signature": checkpoint_signature,
                    "batch_name": batch_name,
                    "done": int(done), "total": int(total),
                    "complete": bool(done >= total),
                    "status": "complete" if done >= total else "running",
                    "updated_at": pd.Timestamp.now().isoformat(),
                })

            scenario = run_cutoff_scenarios(
                filtered, final_items, cost, float(initial_capital), selected_cutoffs,
                benchmark_df=benchmark_df if benchmark_enabled_for_run else None,
                benchmark_buy_fee_rate=float(benchmark_fee_rate),
                config=ScenarioConfig(paths_per_state=int(future_paths_per_state), seed=int(base_seed)),
                progress_callback=lambda pct, txt: progress.progress(0.35 + pct * 0.65, text=txt),
                resume_distribution=resume_distribution,
                checkpoint_callback=_checkpoint_callback, checkpoint_every=200)
            result["scenario_analysis"] = scenario
            write_checkpoint_meta(checkpoint_meta_path, {
                "signature": checkpoint_signature, "batch_name": batch_name,
                "done": int(len(scenario.get("distribution", []))),
                "total": int(len(scenario.get("distribution", []))),
                "complete": True, "status": "complete",
                "updated_at": pd.Timestamp.now().isoformat(),
            })
        result["run_settings"] = {
            "initial_capital": float(initial_capital),
            "use_margin_call_check": bool(use_margin_call_check),
            "safety_buffer_amount": safety_buffer_amount,
            "original_margin_amount": original_margin,
            "position_ui_mode": position_label,
            "position_compounding_ui": bool(position_compounding),
            "timeframe_mode": timeframe_mode,
            "research_mode": research_mode,
            "benchmark_enabled": bool(benchmark_enabled_for_run),
            "benchmark_source": benchmark_source if benchmark_enabled_for_run else "停用",
            "benchmark_buy_fee_rate": float(benchmark_fee_rate),
            "scenario_cutoff_dates": [str(pd.Timestamp(x).date()) for x in selected_cutoffs],
            "scenario_paths_per_state": int(future_paths_per_state),
            "expanded_strategy_count": len(final_items),
            "scenario_checkpoint_signature": checkpoint_signature,
            "scenario_resume_rows_loaded": int(len(resume_distribution)),
            "event_checkpoint_signature": checkpoint_signature if event_mode else "",
            "event_resume_units_loaded": int(event_resume_units_loaded),
            "event_checkpoint_total_units": int(result.get("event_checkpoint_total_units", 0)),
            "event_mode": bool(event_mode),
            "event_windows": event_windows,
            "event_warmup_trade_days": int(event_warmup_trade_days),
        }
        zip_bytes = _result_zip(batch_name, raw_json, result)
        stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"MTX_模擬回測_{stamp}_{_safe_name(batch_name)}.zip"
        st.session_state["v086_result"] = {
            "batch_name": batch_name, "display_name": display_name,
            "result": result, "zip": zip_bytes, "zip_name": zip_name,
            "start": "五次指定事件" if event_mode else str(start_date),
            "end": "事件區間模式" if event_mode else str(end_date),
            "requested_paths": int(path_count), "effective_paths": len(result["seeds"]),
            "initial_capital": float(initial_capital),
        }
        if auth:
            parent = _secret("GDRIVE_RESULTS_PARENT_FOLDER_ID", DEFAULT_GDRIVE_RESULTS_PARENT_FOLDER_ID)
            try:
                uploaded_info = upload_zip_result_to_drive(
                    auth_config=auth, parent_folder_id=parent,
                    result_folder_name=Path(zip_name).stem, zip_name=zip_name, zip_bytes=zip_bytes)
                st.session_state["v086_result"]["drive_url"] = uploaded_info.get("folder_url", "")
            except Exception as e:
                st.session_state["v086_result"]["upload_error"] = str(e)
        st.session_state["v086_run_status"] = "complete"
        # 結果已封裝完成後，檢查點即完成使命；不保留成下一次的「中斷進度」。
        if checkpoint_signature:
            if event_mode:
                clear_event_checkpoint(checkpoint_signature)
            else:
                clear_checkpoint(checkpoint_signature)
    except Exception as e:
        st.session_state["v086_run_status"] = "failed"
        if active_checkpoint_meta_path is not None:
            previous_meta = read_checkpoint_meta(active_checkpoint_meta_path)
            previous_meta.update({
                "signature": active_checkpoint_signature,
                "batch_name": active_batch_name,
                "complete": False, "status": "interrupted",
                "error": str(e), "updated_at": pd.Timestamp.now().isoformat(),
            })
            write_checkpoint_meta(active_checkpoint_meta_path, previous_meta)
        st.sidebar.error(f"回測失敗：{e}")
    finally:
        if progress is not None:
            progress.empty()

state = st.session_state.get("v086_result")
st.markdown('''<div class="hero"><div class="eyebrow">FUTURES STRATEGY RESEARCH</div>
<div class="title">台指期安全約束動態複利回測</div>
<div class="sub">歷史回測｜未來日K情境｜00631L正二基準｜SAR／BIAS／日K缺口／寶塔線｜長回測中斷續跑｜斷頭檢查</div></div>''', unsafe_allow_html=True)

with st.expander("？ 如何閱讀本頁結果", expanded=False):
    st.markdown("""
**標準回測**呈現實際歷史期間結果；若全部只用完整日K，只有一個確定結果，所以不顯示P25、P75與箱型分布。

**期末強制平倉損益**代表模擬終點仍持有的部位。未來本來就沒有真正終點，因此它不再被視為必須消除的問題；正式主排名改用同一終點的「策略期末總權益」直接比較「00631L期末市值」。

**多截止日＋未來情境**會在多個歷史截止日後接上六種由歷史資料抽樣的未來日K。正式排名先看期末總權益超越正二的比例，再看相對正二年化差及P25／P50／P75分布。尚未自然出場比例只作為持倉特性說明。

**正二相對欄位**會把策略與00631L的期末資產、年化報酬、最大回撤與差值並排顯示。回撤改善為正值，代表策略回撤比正二小。

**00631L正二**使用實際價格與整數股數買進持有；2026年1拆22依事件調整股數與報酬，停牌缺值不當成價格為0。

**v0.8.5起的新條件**可在策略JSON使用SAR翻多／翻空、乖離率、開盤跳空、完整缺口未回補及寶塔線翻紅／翻黑；SAR另可設為盤中自適應移動停損。

**長回測續跑**：未來情境每200筆保存一次；事件區間回測則每完成一個「策略×seed×事件」立即保存。若瀏覽器斷線或工作階段中止，使用相同設定重新按開始回測會自動略過已完成單元。完整完成並封裝結果後會立即清除檢查點。

**回撤煞車觀察欄位**會在資金曲線與批次結果中記錄逐日煞車倍率、觸發次數與煞車狀態交易日占比，可用來判斷較早啟動的煞車是精準閃避尾端，還是長期常駐造成的變相降風險。
""")

if not state:
    st.markdown('<div class="result-note">請在左側選擇策略、研究模式、週期與部位模式後開始回測。</div>', unsafe_allow_html=True)
else:
    result = state["result"]
    compare = result["comparison"]
    scenario = result.get("scenario_analysis") or {}
    deterministic = bool(result.get("deterministic_1d_fast_mode"))
    best = compare.iloc[0] if not compare.empty else None
    st.caption(f"{state['batch_name']}｜{state['start']}～{state['end']}｜實際{state['effective_paths']}條盤中路徑（原設定{state['requested_paths']}）")
    if result.get("event_mode"):
        st.info("本批只回測五次指定下跌事件；每個事件另取暖機資料形成指標，但事件外不進場，也不跨事件持倉。")

    badge_html = []
    if deterministic:
        badge_html.append('<span class="badge">⚡ 純日K單次確定結果</span>')
    validation = result.get("simulation_validation") or {}
    if validation.get("status") == "通過":
        badge_html.append(f'<span class="badge">✓ 模擬OHLCV還原通過（{validation.get("checked_seeds", 0)}條）</span>')
    elif validation.get("status") == "失敗":
        badge_html.append(f'<span class="badge">⚠ 模擬還原失敗（{validation.get("error_count", 0)}項）</span>')
    if result.get("run_settings", {}).get("use_margin_call_check"):
        badge_html.append('<span class="badge">✓ 斷頭檢查已啟用</span>')
    if result.get("benchmark_metrics"):
        badge_html.append('<span class="badge">✓ 00631L正二已納入</span>')
    if result.get("scenario_analysis"):
        badge_html.append('<span class="badge">✓ 多截止日未來情境已完成</span>')
    st.markdown('<div class="badge-row">' + ''.join(badge_html) + '</div>', unsafe_allow_html=True)

    if result.get("benchmark_metrics"):
        bm = result["benchmark_metrics"]
        info = result.get("benchmark_info") or {}
        st.subheader("00631L正二基準")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("正二年化報酬率", f"{float(bm.get('年化報酬率(%)', 0)):.2f}%",
                  help="使用相同初始資金，起始日以整數股數買進00631L後持有至期末的複合年化報酬率。")
        b2.metric("正二最大回撤率", f"{float(bm.get('最大回撤率(%)', 0)):.2f}%",
                  help="正二資產從歷史高點跌到後續低點的最大百分比跌幅。")
        b3.metric("正二期末資產", f"{float(bm.get('期末資產(元)', 0)):,.0f}",
                  help="包含未投入現金與持有00631L市值。分割前後總資產保持連續。")
        b4.metric("分割後持有股數", f"{int(bm.get('持有股數', 0)):,}",
                  help="若持有期間跨過2026年1拆22，股數會在恢復交易日自動乘22。")
        source_text = info.get("source", "")
        if source_text:
            st.caption(f"資料來源：{source_text}｜{info.get('start', '')}～{info.get('end', '')}｜{info.get('rows', 0):,}筆")

    if isinstance(scenario.get("comparison"), pd.DataFrame) and not scenario["comparison"].empty:
        formal_best = scenario["comparison"].iloc[0]
        st.markdown(f'<div class="best-banner">正式主排名第一：{formal_best["策略名稱"]}</div>', unsafe_allow_html=True)
        f1, f2, f3, f4, f5, f6 = st.columns(6)
        f1.metric("超越正二比例", f"{float(formal_best.get('期末總權益超越正二比例(%)', 0)):.1f}%",
                  help="在共同截止日、未來狀態與seed中，策略期末總權益高於同一路徑00631L期末市值的比例。")
        f2.metric("策略期末權益中位數", f"{float(formal_best.get('策略期末總權益中位數', 0)):,.0f}")
        f3.metric("正二期末資產中位數", f"{float(formal_best.get('正二期末資產中位數', 0)):,.0f}")
        f4.metric("期末資產差中位數", f"{float(formal_best.get('期末資產差中位數', 0)):,.0f}")
        f5.metric("年化：策略／正二", f"{float(formal_best.get('策略總權益年化中位數(%)', 0)):.2f}%／{float(formal_best.get('正二年化中位數(%)', 0)):.2f}%")
        f6.metric("回撤：策略／正二", f"{float(formal_best.get('策略最大回撤率中位數(%)', 0)):.2f}%／{float(formal_best.get('正二最大回撤率中位數(%)', 0)):.2f}%")
        st.caption("正式主排名採共同路徑期末總權益比較；未自然出場比例只說明策略在模擬終點是否仍持倉。")

    if best is not None:
        label_suffix = "" if deterministic else "中位數"
        st.markdown(f'<div class="best-banner">單一歷史路徑排序第一：{best["策略名稱"]}</div>', unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(f"年化報酬率{label_suffix}", f"{float(best.get('年化報酬率中位數(%)', 0)):.2f}%",
                  help="以初始資金與期末帳戶價值換算的複合年化報酬率；包含期末尚未自然出場部位。")
        c2.metric(f"報酬回撤比{label_suffix}", best.get("報酬回撤比中位數", "—"),
                  help="總損益除以最大回撤絕對值。越高代表每承受一元歷史回撤換得的獲利越多。")
        c3.metric(f"最大回撤{label_suffix}", f"{float(best.get('最大回撤中位數', 0)):,.0f}",
                  help="帳戶權益從歷史高點到後續低點的最大金額回落；不是從初始資金直接虧掉的金額。")
        c4.metric("扣除期末強平後損益", f"{float(best.get('扣除期末強制平倉後損益中位數', 0)):,.0f}",
                  help="排除回測結束時仍未自然出場部位的假設平倉損益，只保留已完成交易。")
        c5.metric("最大有效槓桿", f"{float(best.get('最大有效槓桿中位數(倍)', 0) or 0):.2f}倍",
                  help="進場時名目曝險相對帳戶可用權益的最高倍數。")

    st.subheader("單一歷史路徑策略比較")
    if deterministic:
        compare_cols = [c for c in [
            "策略名稱", "策略期末總權益(元)", "正二期末資產(元)", "期末資產差(元)",
            "策略年化報酬率(%)", "正二年化報酬率(%)", "相對正二年化差(百分點)",
            "策略最大回撤率(%)", "正二最大回撤率(%)", "相對正二回撤改善(百分點)",
            "歷史期末總權益超越正二", "總損益中位數", "扣除期末強制平倉後損益中位數",
            "期末強制平倉損益中位數", "最大有效槓桿中位數(倍)", "斷頭路徑數"
        ] if c in compare.columns]
    else:
        compare_cols = [c for c in [
            "策略名稱", "策略期末總權益(元)", "正二期末資產(元)", "期末資產差(元)",
            "策略年化報酬率(%)", "正二年化報酬率(%)", "相對正二年化差(百分點)",
            "策略最大回撤率(%)", "正二最大回撤率(%)", "相對正二回撤改善(百分點)",
            "歷史期末總權益超越正二", "總損益P25", "總損益中位數", "總損益P75",
            "扣除期末強制平倉後損益中位數", "期末強制平倉損益中位數",
            "最大有效槓桿中位數(倍)", "斷頭路徑數"
        ] if c in compare.columns]
    compare_view = compare[compare_cols].copy()
    rename_single = {
        "報酬回撤比中位數": "報酬回撤比", "總損益中位數": "總損益",
        "扣除期末強制平倉後損益中位數": "已實現損益",
        "最大回撤中位數": "最大回撤", "最大回撤率中位數(%)": "最大回撤率(%)",
        "年化報酬率中位數(%)": "年化報酬率(%)", "相對正二年化差(百分點)": "年化差(百分點)",
        "策略期末總權益(元)": "策略期末權益", "正二期末資產(元)": "正二期末資產",
        "期末資產差(元)": "期末資產差", "策略年化報酬率(%)": "策略年化(%)",
        "正二年化報酬率(%)": "正二年化(%)", "策略最大回撤率(%)": "策略回撤(%)",
        "正二最大回撤率(%)": "正二回撤(%)", "相對正二回撤改善(百分點)": "回撤改善(百分點)",
        "最大有效槓桿中位數(倍)": "最大有效槓桿(倍)",
        "獲利因子中位數": "獲利因子", "交易次數中位數": "交易次數",
        "期末強制平倉損益中位數": "期末強平損益", "歷史最低運作資金中位數": "歷史最低運作資金",
    }
    if deterministic:
        compare_view = compare_view.rename(columns=rename_single)

    def _highlight_best(row):
        return ["background-color:#E6F2ED;font-weight:750" if row.name == 0 else "" for _ in row]

    st.dataframe(compare_view.style.apply(_highlight_best, axis=1), use_container_width=True, hide_index=True)

    if not compare.empty:
        scatter = go.Figure()
        if "獲利路徑比例(%)" in compare.columns:
            size_values = compare["獲利路徑比例(%)"].fillna(100)
        else:
            size_values = pd.Series(100, index=compare.index)
        scatter.add_trace(go.Scatter(
            x=compare["最大回撤中位數"].abs(), y=compare["扣除期末強制平倉後損益中位數"],
            mode="markers+text", text=compare["策略名稱"], textposition="top center",
            marker=dict(size=12 + size_values / 10,
                        color=compare["報酬回撤比中位數"], colorscale="Teal", showscale=True,
                        colorbar=dict(title="報酬回撤比")),
            hovertemplate="%{text}<br>回撤絕對值=%{x:,.0f}<br>已完成損益=%{y:,.0f}<extra></extra>"))
        scatter.update_layout(title="已完成損益與最大回撤", xaxis_title="最大回撤絕對值（元）",
                              yaxis_title="扣除期末強平後損益（元）", height=430,
                              margin=dict(l=20, r=20, t=55, b=20), paper_bgcolor="white", plot_bgcolor="white")
        st.plotly_chart(scatter, use_container_width=True)

    dist = result["distribution"]
    if not deterministic and not dist.empty:
        fig = go.Figure()
        for name, grp in dist.groupby("策略名稱", sort=False):
            fig.add_trace(go.Box(y=grp["總損益(元)"], name=name, boxmean=True))
        fig.update_layout(title="各盤中隨機路徑總損益分布", yaxis_title="總損益（元）",
                          margin=dict(l=20, r=20, t=55, b=20), height=430,
                          paper_bgcolor="white", plot_bgcolor="white", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    if isinstance(scenario.get("comparison"), pd.DataFrame) and not scenario["comparison"].empty:
        st.subheader("正式主排名｜多截止日＋六種未來情境＋正二比較")
        st.caption(f"共同截止日：{', '.join(scenario.get('cutoff_dates', []))}｜每條未來延伸{scenario.get('future_days', '—')}個交易日｜排名口徑：{scenario.get('ranking_basis', '共同期末總權益')}")
        scenario_compare = scenario["comparison"].copy()
        main_cols = [c for c in [
            "策略名稱", "情境路徑數", "期末總權益超越正二比例(%)",
            "策略期末總權益中位數", "正二期末資產中位數", "期末資產差中位數", "期末資產差P10",
            "策略總權益年化中位數(%)", "正二年化中位數(%)", "相對正二年化差中位數(百分點)",
            "策略最大回撤率中位數(%)", "正二最大回撤率中位數(%)", "相對正二回撤改善中位數(百分點)",
            "策略最差最大回撤率(%)", "正二最差最大回撤率(%)", "斷頭路徑比例(%)",
            "尚未自然出場比例(%)", "已實現損益中位數", "已實現損益P10"
        ] if c in scenario_compare.columns]
        st.dataframe(scenario_compare[main_cols].style.apply(_highlight_best, axis=1), use_container_width=True, hide_index=True)
        sbest = scenario_compare.iloc[0]
        s1, s2, s3, s4, s5, s6 = st.columns(6)
        s1.metric("情境排名第一", str(sbest["策略名稱"]))
        s2.metric("超越正二比例", f"{float(sbest.get('期末總權益超越正二比例(%)', 0)):.1f}%")
        s3.metric("策略／正二期末資產", f"{float(sbest.get('策略期末總權益中位數', 0)):,.0f}／{float(sbest.get('正二期末資產中位數', 0)):,.0f}")
        s4.metric("策略／正二年化", f"{float(sbest.get('策略總權益年化中位數(%)', 0)):.2f}%／{float(sbest.get('正二年化中位數(%)', 0)):.2f}%")
        s5.metric("策略／正二回撤", f"{float(sbest.get('策略最大回撤率中位數(%)', 0)):.2f}%／{float(sbest.get('正二最大回撤率中位數(%)', 0)):.2f}%")
        s6.metric("未自然出場比例", f"{float(sbest.get('尚未自然出場比例(%)', 0)):.1f}%",
                  help="只表示模擬終點仍有持倉，不再作為排名扣分或必須消除的問題。")

        state_summary = scenario.get("state_summary")
        if isinstance(state_summary, pd.DataFrame) and not state_summary.empty:
            with st.expander("各市場狀態勝負", expanded=True):
                st.dataframe(state_summary, use_container_width=True, hide_index=True)
        with st.expander("完整情境路徑明細", expanded=False):
            st.dataframe(scenario["distribution"], use_container_width=True, hide_index=True)

    event_dist = result.get("event_distribution")
    if isinstance(event_dist, pd.DataFrame) and not event_dist.empty:
        st.subheader("五次事件逐策略結果")
        event_summary = event_dist.groupby(["策略名稱", "事件"], as_index=False).agg(
            總損益P25=("總損益(元)", lambda x: pd.to_numeric(x, errors="coerce").quantile(.25)),
            總損益P50=("總損益(元)", lambda x: pd.to_numeric(x, errors="coerce").quantile(.50)),
            總損益P75=("總損益(元)", lambda x: pd.to_numeric(x, errors="coerce").quantile(.75)),
            最大回撤率P50=("最大回撤率(%)", lambda x: pd.to_numeric(x, errors="coerce").quantile(.50)),
            交易次數P50=("交易次數", lambda x: pd.to_numeric(x, errors="coerce").quantile(.50)),
        )
        st.dataframe(event_summary, use_container_width=True, hide_index=True)

    if validation.get("status") == "失敗" and validation.get("errors"):
        with st.expander("模擬還原錯誤", expanded=True):
            st.code("\n".join(validation["errors"]))

    st.subheader("代表路徑明細")
    for name in compare["策略名稱"].tolist():
        rep = result["representatives"][name]
        m = rep["metrics"]
        title = name if deterministic else f"{name}｜代表seed {rep['seed']}"
        with st.expander(title, expanded=(name == compare.iloc[0]["策略名稱"])):
            st.caption(f"部位口徑：{_position_basis_text(rep['config'])}")
            a, b, c, d, e, f = st.columns(6)
            a.metric("總損益", f"{float(m.get('總損益(元)', 0)):,.0f}")
            b.metric("已完成損益", f"{float(m.get('扣除期末強制平倉後損益(元)', 0)):,.0f}",
                     help="扣除回測最後一天仍未自然出場部位的假設平倉損益。")
            c.metric("最大回撤率", f"{float(m.get('策略標準最大回撤率(%)', m.get('最大回撤(%)', 0))):.2f}%")
            d.metric("最大有效槓桿", f"{float(m.get('最大有效槓桿(倍)', 0) or 0):.2f}倍")
            e.metric("期末強平損益", f"{float(m.get('期末強制平倉損益(元)', 0)):,.0f}")
            f.metric("斷頭次數", int(m.get("斷頭次數", 0)))
            g, h, i = st.columns(3)
            g.metric("煞車觸發次數", int(m.get("回撤煞車觸發次數", 0)))
            h.metric("煞車狀態交易日占比", f"{float(m.get('煞車狀態交易日占比(%)', 0) or 0):.2f}%")
            i.metric("平均每日煞車倍率", f"{float(m.get('平均每日回撤煞車倍率', 1) or 1):.4f}")
            eq = rep["equity"].copy()
            if not eq.empty:
                st.plotly_chart(_equity_figure(eq, float(state.get("initial_capital", 500000))), use_container_width=True)

            override_df = _exit_override_table(rep["config"])
            if not override_df.empty:
                st.markdown("**多空出場覆寫差異**")
                st.dataframe(override_df, use_container_width=True, hide_index=True)

            trades = rep["trades"].copy()
            trade_columns = {
                "entry_date": "進場時間", "exit_date": "出場時間", "direction": "方向",
                "entry_price": "進場價", "exit_price": "出場價",
                "large_quantity": "大台口數", "small_quantity": "小台口數", "micro_quantity": "微台口數",
                "position_action": "口數變化", "position_compounding": "複利啟用",
                "available_equity_at_entry": "進場前部位計算權益", "effective_leverage": "有效槓桿",
                "margin_utilization_pct": "保證金占用率(%)", "safe_capital_balance": "安全資金餘額",
                "base_risk_fraction": "原始風險率", "effective_risk_fraction": "煞車後風險率",
                "drawdown_brake_multiplier": "回撤煞車倍率", "realized_equity_drawdown_pct": "已實現權益回撤(%)",
                "pnl_amount": "損益(元)", "holding_bars": "持有K棒", "exit_reason": "出場原因"}
            visible = [c for c in trade_columns if c in trades.columns]
            st.dataframe(trades[visible].rename(columns=trade_columns), use_container_width=True, hide_index=True)

    with st.sidebar:
        st.markdown('<div class="section">結果</div>', unsafe_allow_html=True)
        if state.get("drive_url"):
            st.success("已上傳Google Drive")
        elif state.get("upload_error"):
            st.warning("雲端上傳失敗，仍可下載ZIP")
        st.download_button("下載完整結果ZIP", state["zip"], file_name=state["zip_name"],
                           mime="application/zip", use_container_width=True)
