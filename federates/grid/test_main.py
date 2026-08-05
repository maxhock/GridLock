"""Unit tests for the grid federate's pure helpers."""

import pandapower as pp
import pytest

from main import GridFederate, _parse_load_index, sanitize_net_for_power_flow


def _net_with_one_load() -> pp.pandapowerNet:
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, bus)
    pp.create_load(net, bus, p_mw=0.123, q_mvar=0.045)
    return net


def _net_with_two_loads() -> pp.pandapowerNet:
    """A tiny but real network: ext grid feeding two buses, one load each."""
    net = pp.create_empty_network()
    slack_bus = pp.create_bus(net, vn_kv=0.4)
    bus_0 = pp.create_bus(net, vn_kv=0.4)
    bus_1 = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, slack_bus)
    pp.create_line(net, slack_bus, bus_0, length_km=0.1, std_type="NAYY 4x50 SE")
    pp.create_line(net, slack_bus, bus_1, length_km=0.1, std_type="NAYY 4x50 SE")
    pp.create_load(net, bus_0, p_mw=0.0, q_mvar=0.0)  # load index 0
    pp.create_load(net, bus_1, p_mw=0.0, q_mvar=0.0)  # load index 1
    sanitize_net_for_power_flow(net)
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


def test_update_internal_model_filters_helics_sentinel_values() -> None:
    """HELICS reports "no data received yet" as a huge sentinel, not absence."""
    net = _net_with_two_loads()
    federate = GridFederate("test-grid", net)
    federate.data_from_federation["inputs"] = {
        "test-grid/load_0/active_power": -1e49,
        "test-grid/load_1/active_power": 100.0,
    }
    federate.data_to_federation["publications"] = {
        "test-grid/load_0/voltage": None,
        "test-grid/load_1/voltage": None,
    }

    federate.update_internal_model()

    assert net.load.at[0, "p_mw"] == 0.0
    assert net.load.at[1, "p_mw"] == pytest.approx(100.0 / 1e6)


def test_update_internal_model_publishes_voltage_per_load_bus() -> None:
    net = _net_with_two_loads()
    federate = GridFederate("test-grid", net)
    federate.data_from_federation["inputs"] = {
        "test-grid/load_0/active_power": 500.0,
    }
    federate.data_to_federation["publications"] = {
        "test-grid/load_0/voltage": None,
        "test-grid/load_1/voltage": None,
    }

    federate.update_internal_model()

    bus_0 = int(net.load.at[0, "bus"])
    bus_1 = int(net.load.at[1, "bus"])
    assert federate.data_to_federation["publications"][
        "test-grid/load_0/voltage"
    ] == pytest.approx(float(net.res_bus.at[bus_0, "vm_pu"]))
    assert federate.data_to_federation["publications"][
        "test-grid/load_1/voltage"
    ] == pytest.approx(float(net.res_bus.at[bus_1, "vm_pu"]))


def test_update_internal_model_raises_runtime_error_on_non_convergence() -> None:
    """A load with no path to a slack bus cannot solve - must fail loudly.

    AGENTS.md: swallowing a diverged power flow would leave the previous
    step's voltages published as if current, so this must raise, not warn.
    """
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=0.4)
    pp.create_load(net, bus, p_mw=1.0, q_mvar=0.0)  # no ext_grid: no slack bus
    sanitize_net_for_power_flow(net)
    federate = GridFederate("test-grid", net)

    with pytest.raises(RuntimeError, match="did not converge"):
        federate.update_internal_model()
