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
