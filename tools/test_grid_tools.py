"""Tests for the grid-generator scripts in `tools/`.

Not part of `test.sh`/CI (tools/ has no image of its own); run them in the grid
image, which has the pinned pandapower, plus the xlsx writers:
    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -e PYTHONUSERBASE=/tmp/pyuser -e PATH=/tmp/pyuser/bin:/usr/local/bin:/usr/bin:/bin \
        -v "$PWD":/repo -w /repo grid-test sh -c \
        'pip install -q --user openpyxl==3.1.5 xlsxwriter==3.2.9 && \
         python -m pytest -p no:cacheprovider tools/test_grid_tools.py'
"""

from __future__ import annotations

from pathlib import Path

import pandapower as pp
import pytest

from grid_contract import ContractViolation, check_attach, check_attach_all
from make_lv_variant import make_variant, smoke_test
from make_synthetic_mv import build_net
from mvlv_merge import build_merged_net, power_into_part

INPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "input"
STOCK_LV = ("kerber_landnetz_freileitung_1", "kerber_landnetz_kabel_1")


def _load(name: str) -> pp.pandapowerNet:
    return pp.from_excel(str(INPUT_DIR / f"{name}.xlsx"))


@pytest.mark.parametrize("name", STOCK_LV)
def test_variant_rerates_slack_and_trafo_only(name: str) -> None:
    stock = _load(name)
    variant = _load(name)
    make_variant(variant, 20.0)
    slack_bus = variant.ext_grid.bus.iloc[0]
    assert variant.bus.at[slack_bus, "vn_kv"] == 20.0
    assert (variant.trafo.vn_hv_kv == 20.0).all()
    assert variant.trafo.sn_mva.equals(stock.trafo.sn_mva)
    assert variant.bus.index.equals(stock.bus.index)
    assert variant.load.index.equals(stock.load.index)
    assert variant.line.index.equals(stock.line.index)
    assert variant.load.bus.equals(stock.load.bus)
    smoke_test(variant)


@pytest.mark.parametrize("name", STOCK_LV)
def test_committed_variant_matches_a_fresh_one(name: str) -> None:
    fresh = _load(name)
    make_variant(fresh, 20.0)
    committed = _load(f"{name}_20kv")
    assert committed.bus.vn_kv.equals(fresh.bus.vn_kv)
    assert committed.trafo.vn_hv_kv.equals(fresh.trafo.vn_hv_kv)
    assert committed.load.index.equals(fresh.load.index)


def test_variant_with_std_type_keeps_the_std_types_rating() -> None:
    net = _load("kerber_landnetz_freileitung_1")
    make_variant(net, 20.6, "0.25 MVA 20/0.4 kV")
    assert net.bus.at[net.ext_grid.bus.iloc[0], "vn_kv"] == 20.6
    assert net.trafo.vn_hv_kv.iloc[0] == 20.0
    smoke_test(net)


def test_variant_rejects_unknown_std_type() -> None:
    with pytest.raises(ValueError, match="unknown"):
        make_variant(_load("kerber_landnetz_kabel_1"), 20.0, "no such trafo")


def test_variant_needs_exactly_one_ext_grid() -> None:
    net = _load("kerber_landnetz_kabel_1")
    pp.create_ext_grid(net, 1)
    with pytest.raises(ValueError, match="exactly one ext_grid"):
        make_variant(net, 20.0)


def test_synthetic_mv_has_six_loads_on_20kv_buses() -> None:
    net = build_net()
    assert list(net.load.index) == [0, 1, 2, 3, 4, 5]
    assert (net.bus.loc[net.load.bus, "vn_kv"] == 20.0).all()
    assert net.load.bus.is_unique
    assert len(net.sgen) == 1
    pp.runpp(net)
    assert net.converged


def test_committed_synthetic_mv_matches_the_generator() -> None:
    committed = pp.from_json(str(INPUT_DIR / "mv_synthetic_20kv.json"))
    assert committed.load.bus.equals(build_net().load.bus)


def test_check_attach_accepts_matching_voltage_bases() -> None:
    mv = build_net()
    lv = _load("kerber_landnetz_freileitung_1_20kv")
    assert check_attach_all(mv, lv) == 6


def test_check_attach_rejects_voltage_mismatch() -> None:
    with pytest.raises(ContractViolation, match="bases must match"):
        check_attach(build_net(), 0, _load("kerber_landnetz_freileitung_1"))


def test_check_attach_rejects_two_ext_grids() -> None:
    lv = _load("kerber_landnetz_freileitung_1_20kv")
    pp.create_ext_grid(lv, 1)
    with pytest.raises(ContractViolation, match="exactly one ext_grid"):
        check_attach(build_net(), 0, lv)


def test_check_attach_rejects_unknown_mv_load() -> None:
    with pytest.raises(ContractViolation, match="no load"):
        check_attach(build_net(), 99, _load("kerber_landnetz_freileitung_1_20kv"))


LV_FREILEITUNG = "kerber_landnetz_freileitung_1_20kv"
LV_KABEL = "kerber_landnetz_kabel_1_20kv"


def _merged_like_the_parity_experiment() -> tuple:
    mv = build_net()
    lvs = {1: _load(LV_FREILEITUNG), 3: _load(LV_FREILEITUNG), 5: _load(LV_KABEL)}
    return mv, lvs, build_merged_net(mv, lvs)


def test_merged_net_has_one_slack_and_the_expected_element_counts() -> None:
    mv, lvs, merged = _merged_like_the_parity_experiment()
    net = merged.net
    assert len(net.ext_grid) == 1
    # Each LV slack bus is fused into its MV load bus, so it adds no bus.
    assert len(net.bus) == len(mv.bus) + sum(len(lv.bus) - 1 for lv in lvs.values())
    # The three MV loads an LV net stands in for are gone, the LV loads are in.
    assert len(net.load) == len(mv.load) - 3 + sum(len(lv.load) for lv in lvs.values())
    assert merged.mv_load_ids == [0, 2, 4]
    assert sorted(merged.parts) == [1, 3, 5]
    assert len(net.trafo) == len(mv.trafo) + 3


def test_merged_net_is_one_connected_converging_system() -> None:
    _, _, merged = _merged_like_the_parity_experiment()
    pp.runpp(merged.net)
    assert merged.net.converged
    assert merged.net.res_bus.vm_pu.notna().all()
    # The fused LV trafo hangs on the MV bus, not on a dangling copy of it.
    for part in merged.parts.values():
        assert (
            merged.net.trafo.loc[list(part.trafo_ids), "hv_bus"] == part.fused_bus
        ).all()


def test_merging_leaves_its_inputs_untouched() -> None:
    mv, lvs, _ = _merged_like_the_parity_experiment()
    assert len(mv.load) == 6
    assert all(len(lv.ext_grid) == 1 for lv in lvs.values())


def test_power_into_part_is_the_lv_load_plus_losses() -> None:
    mv = build_net()
    lv = _load(LV_FREILEITUNG)
    merged = build_merged_net(mv, {1: lv})
    lv_loads = list(merged.parts[1].load_ids.values())
    merged.net.load.loc[lv_loads, "p_mw"] = 0.006
    pp.runpp(merged.net)

    p_w, _ = power_into_part(merged, 1)
    assert p_w > 0.006 * len(lv_loads) * 1e6  # the demand plus trafo/line losses

    # Same answer from the LV net alone at the voltage the MV bus ended up at.
    lv.load["p_mw"] = 0.006
    lv.ext_grid["vm_pu"] = merged.net.res_bus.at[merged.parts[1].fused_bus, "vm_pu"]
    pp.runpp(lv)
    assert p_w == pytest.approx(lv.res_ext_grid.p_mw.iat[0] * 1e6, rel=1e-6)


def test_power_into_part_counts_lv_loads_on_the_slack_bus() -> None:
    # A one-bus LV net is a valid attachment: its load hangs on the slack bus,
    # which fusion merges into the MV bus, so no LV branch carries its power.
    mv = build_net()
    lv = pp.create_empty_network()
    slack = pp.create_bus(lv, vn_kv=float(mv.bus.vn_kv.at[mv.load.bus.at[1]]))
    pp.create_ext_grid(lv, slack)
    pp.create_load(lv, slack, p_mw=0.0)
    merged = build_merged_net(mv, {1: lv})
    (lv_load,) = merged.parts[1].load_ids.values()
    merged.net.load.loc[lv_load, ["p_mw", "q_mvar"]] = [0.006, 0.002]
    pp.runpp(merged.net)

    p_w, q_var = power_into_part(merged, 1)
    assert p_w == pytest.approx(6000.0)
    assert q_var == pytest.approx(2000.0)


def test_power_into_part_counts_lv_shunts_on_the_slack_bus() -> None:
    mv = build_net()
    lv = pp.create_empty_network()
    slack = pp.create_bus(lv, vn_kv=float(mv.bus.vn_kv.at[mv.load.bus.at[1]]))
    pp.create_ext_grid(lv, slack)
    pp.create_shunt(lv, slack, q_mvar=0.003, p_mw=0.001)
    merged = build_merged_net(mv, {1: lv})
    pp.runpp(merged.net)

    (shunt,) = merged.parts[1].shunt_ids
    p_w, q_var = power_into_part(merged, 1)
    assert p_w == pytest.approx(merged.net.res_shunt.at[shunt, "p_mw"] * 1e6)
    assert q_var == pytest.approx(merged.net.res_shunt.at[shunt, "q_mvar"] * 1e6)
    assert p_w > 0


def test_merge_rejects_an_mv_load_that_does_not_exist() -> None:
    with pytest.raises(ValueError, match="not available"):
        build_merged_net(build_net(), {99: _load(LV_FREILEITUNG)})
