"""Unit tests for the grid federate's pure helpers."""

import pandapower as pp

from main import _parse_load_index, sanitize_net_for_power_flow


def _net_with_one_load() -> pp.pandapowerNet:
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, bus)
    pp.create_load(net, bus, p_mw=0.123, q_mvar=0.045)
    return net


def test_sanitize_adds_missing_zip_columns() -> None:
    net = _net_with_one_load()
    net.load = net.load.drop(
        columns=[
            "const_z_p_percent",
            "const_i_p_percent",
            "const_z_q_percent",
            "const_i_q_percent",
        ]
    )

    sanitize_net_for_power_flow(net)

    assert net.load.at[0, "const_z_p_percent"] == 0.0
    assert net.load.at[0, "const_i_p_percent"] == 0.0
    assert net.load.at[0, "const_z_q_percent"] == 0.0
    assert net.load.at[0, "const_i_q_percent"] == 0.0


def test_sanitize_clears_imported_power() -> None:
    net = _net_with_one_load()

    sanitize_net_for_power_flow(net)

    assert net.load.at[0, "p_mw"] == 0.0
    assert net.load.at[0, "q_mvar"] == 0.0


def test_sanitize_keeps_existing_zip_columns() -> None:
    net = _net_with_one_load()
    net.load.at[0, "const_z_p_percent"] = 25.0

    sanitize_net_for_power_flow(net)

    assert net.load.at[0, "const_z_p_percent"] == 25.0


def test_sanitize_sets_defaults_on_added_columns() -> None:
    net = _net_with_one_load()
    net.load = net.load.drop(columns=["scaling", "in_service"])

    sanitize_net_for_power_flow(net)

    assert net.load.at[0, "scaling"] == 1.0
    assert bool(net.load.at[0, "in_service"]) is True


def test_sanitize_empty_net_is_a_no_op() -> None:
    net = pp.create_empty_network()

    result = sanitize_net_for_power_flow(net)

    assert result is net
    assert net.load.empty


def test_parse_load_index_matches_expected_key_shape() -> None:
    assert _parse_load_index("lv-grid/load_3/active_power") == 3
    assert _parse_load_index("lv-grid/load_0/reactive_power") == 0
    assert _parse_load_index("lv-grid/load_12/voltage") == 12


def test_parse_load_index_returns_none_without_a_match() -> None:
    assert _parse_load_index("lv-grid/active_power") is None
    assert _parse_load_index("lv-grid/house_4/active_power") is None
