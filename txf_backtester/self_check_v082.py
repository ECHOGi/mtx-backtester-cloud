# -*- coding: utf-8 -*-
"""v0.8.2 自動契約換算專項自檢。"""
from types import SimpleNamespace

from backtester import (_contract_mix_from_micro_units,
                        _safe_capital_position_spec,
                        CostModel)


def params(**overrides):
    base = dict(
        position_micro_point_value=10.0,
        position_small_point_value=50.0,
        position_large_point_value=200.0,
        position_micro_margin=32000.0,
        position_small_margin=159000.0,
        position_large_margin=636000.0,
        position_micro_maintenance_margin=24400.0,
        position_small_maintenance_margin=122000.0,
        position_large_maintenance_margin=488000.0,
        position_micro_fee=12.0,
        position_small_fee=20.0,
        position_large_fee=50.0,
        position_contract_mix_mode="min_contract_count",
        position_max_contract_point_value=200.0,
        position_sizing_capital=500000.0,
        position_compounding=True,
        position_safe_capital_per_micro_unit=100000.0,
        position_min_cash_buffer=0.0,
        position_drawdown_reserve_fraction=0.0,
        position_gap_stress_points=0.0,
        position_stress_multiple=4.0,
        position_use_stress_capital_check=False,
        position_max_micro_units=0,
        position_max_small_contracts=0,
        position_sizing_mode="dynamic_safe_capital",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def main():
    passed = []

    s = _contract_mix_from_micro_units(200, params())
    assert (s["large_qty"], s["small_qty"], s["micro_qty"]) == (10, 0, 0), s
    assert s["point_value_total"] == 2000.0
    passed.append("200_micro_units_to_10_tx")

    s = _contract_mix_from_micro_units(203, params())
    assert (s["large_qty"], s["small_qty"], s["micro_qty"]) == (10, 0, 3), s
    assert s["fee_per_side_total"] == 10 * 50 + 3 * 12
    passed.append("203_units_minimum_13_contracts")

    s = _contract_mix_from_micro_units(
        203, params(position_max_contract_point_value=50.0))
    assert (s["large_qty"], s["small_qty"], s["micro_qty"]) == (0, 40, 3), s
    passed.append("risk_cap_uses_mtx_and_tmf")

    # 0代表不使用人為口數上限；21個微台等值應由安全資金自然決定，
    # 並換算為1大台+1微台，而不是被舊預設10單位截斷。
    p = params(position_sizing_capital=2_100_000.0,
               position_safe_capital_per_micro_unit=100_000.0)
    s = _safe_capital_position_spec(None, p, realized=0.0,
                                    cost=CostModel())
    assert s is not None, s
    assert s["micro_units"] == 21, s
    assert (s["large_qty"], s["small_qty"], s["micro_qty"]) == (1, 0, 1), s
    passed.append("zero_cap_means_safety_limited_not_fixed_cap")

    print(f"PASS {len(passed)} v0.8.2 cases")
    for name in passed:
        print(f"- {name}")


if __name__ == "__main__":
    main()
