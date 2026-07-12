# -*- coding: utf-8 -*-
"""批次策略 JSON 解析與 v0.8.0 部位模式覆寫。"""
from __future__ import annotations

import copy
import itertools
import json


def _set_nested(cfg: dict, path: str, value):
    parts = str(path).split(".")
    cur = cfg
    for key in parts[:-1]:
        if not isinstance(cur.get(key), dict):
            cur[key] = {}
        cur = cur[key]
    cur[parts[-1]] = value


def expand_sweeps(obj: dict) -> list[dict]:
    raw = obj.get("sweeps") if isinstance(obj.get("sweeps"), list) else obj.get("sweep")
    if not raw:
        return []
    groups = raw if isinstance(raw, list) else [raw]
    items = []
    for sw in groups:
        base = copy.deepcopy(sw.get("base") or sw.get("config") or {})
        params = sw.get("params") or {}
        if not params and sw.get("param"):
            params = {sw["param"]: sw.get("values") or []}
        paths = list(params)
        values = [params[p] for p in paths]
        for combo in itertools.product(*values):
            cfg = copy.deepcopy(base)
            mapping = dict(zip(paths, combo))
            for path, value in mapping.items():
                _set_nested(cfg, path, value)
            template = sw.get("name_template")
            if template:
                name = str(template)
                for path, value in mapping.items():
                    name = name.replace("{" + path + "}", str(value))
            else:
                suffix = "_".join(f"{p.split('.')[-1]}={v}" for p, v in mapping.items())
                name = f"{sw.get('name_prefix', '')}{suffix}"
            cfg["name"] = name
            items.append(cfg)
    return items


def parse_strategy_batch(text: str, symbol: str = "MTX", max_strategies: int = 50):
    obj = json.loads(text)
    batch_name = "MTX批次回測"
    if isinstance(obj, list):
        raw_items = obj
        batch_meta = {}
    elif isinstance(obj, dict):
        batch_name = str(obj.get("batch_name") or obj.get("name") or batch_name)
        raw_items = obj.get("strategies") or obj.get("items")
        sweep_items = expand_sweeps(obj)
        if raw_items is None:
            raw_items = [] if sweep_items else [obj]
        raw_items = list(raw_items) + sweep_items
        batch_meta = {k: v for k, v in obj.items() if k not in {"strategies", "items", "sweep", "sweeps"}}
    else:
        raise ValueError("批次策略 JSON 必須是策略陣列或包含 strategies 的物件")
    if not raw_items:
        raise ValueError("批次策略 JSON 沒有策略")
    if len(raw_items) > max_strategies:
        raise ValueError(f"一次最多 {max_strategies} 組策略，目前 {len(raw_items)} 組")
    out = []
    for i, item in enumerate(raw_items, 1):
        if isinstance(item.get("config"), dict):
            cfg = copy.deepcopy(item["config"])
            name = item.get("name") or item.get("label") or cfg.get("name")
        elif isinstance(item.get("strategy_config"), dict):
            cfg = copy.deepcopy(item["strategy_config"])
            name = item.get("name") or item.get("label") or cfg.get("name")
        else:
            cfg = copy.deepcopy(item)
            name = cfg.get("name") or item.get("label")
        name = str(name or f"策略{i:02d}")
        cfg["name"] = name
        cfg["symbol"] = symbol
        out.append((name, cfg))
    return batch_name, out, batch_meta


def apply_position_mode(cfg: dict, mode: str, initial_capital: float,
                        safe_capital_per_small: float = 500000.0,
                        max_small_contracts: int = 10,
                        position_compounding: bool = False) -> dict:
    """UI/CLI 部位覆寫。

    ``json`` 保留策略的部位方法與風險參數，但介面輸入的初始資金仍是
    本次實際帳戶資金，因此同步覆寫 sizing capital，避免績效分母與口數計算
    使用兩套不同本金。

    v0.7.0 策略可能把部位欄位放在 ``position_policy``，而非 ``exit``。
    因此所有強制對照模式必須同時覆寫兩處，否則畫面選「固定1口」仍可能被
    頂層的核心／加碼政策重新開啟。
    """
    out = copy.deepcopy(cfg)
    ex = out.setdefault("exit", {})
    policy = out.setdefault("position_policy", {})

    def set_both(key, value):
        ex[key] = value
        policy[key] = value

    set_both("position_sizing_capital", float(initial_capital))
    if mode == "json":
        return out

    set_both("position_max_small_contracts", int(max_small_contracts))
    set_both("position_max_micro_units", int(max_small_contracts) * 5)
    set_both("use_dynamic_position_sizing", False)
    set_both("use_regime_position_sizing", False)
    set_both("use_safe_capital_position_sizing", False)

    if mode == "fixed":
        set_both("position_sizing_mode", "fixed")
        set_both("position_compounding", False)
    elif mode in {"dynamic_safe_capital", "dynamic_safe_capital_capped"}:
        set_both("position_sizing_mode", mode)
        set_both("position_compounding", bool(position_compounding))
        set_both("use_safe_capital_position_sizing", True)
        set_both("use_account_margin_model", True)
        set_both("position_safe_capital_per_micro_unit", float(safe_capital_per_small) / 5.0)
        if mode == "dynamic_safe_capital":
            # 非封頂模式仍保留極高的技術保護，避免異常資料造成無限口數。
            set_both("position_max_micro_units", max(int(max_small_contracts) * 5, 1000))
            set_both("position_max_small_contracts", max(int(max_small_contracts), 200))
    else:
        raise ValueError(f"未知部位模式：{mode}")
    return out
