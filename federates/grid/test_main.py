"""Unit tests for the grid federate helpers."""

import pandapower as pp

from main import sanitize_net_for_power_flow


def test_sanitize_net_for_power_flow_adds_missing_zip_columns() -> None:
    """Missing load-model columns are added and imported powers are cleared."""
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, bus)
    pp.create_load(net, bus, p_mw=0.123, q_mvar=0.045)

    # Simulate an older/external JSON payload missing newer pandapower columns.
    net.load = net.load.drop(
        columns=[
            "const_z_p_percent",
            "const_i_p_percent",
            "const_z_q_percent",
            "const_i_q_percent",
        ]
    )

    sanitize_net_for_power_flow(net)

    assert net.load.at[0, "p_mw"] == 0.0
    assert net.load.at[0, "q_mvar"] == 0.0
    assert net.load.at[0, "const_z_p_percent"] == 0.0
    assert net.load.at[0, "const_i_p_percent"] == 0.0
    assert net.load.at[0, "const_z_q_percent"] == 0.0
    assert net.load.at[0, "const_i_q_percent"] == 0.0


def test_sanitize_net_for_power_flow_keeps_existing_columns() -> None:
    """Existing load-model values are preserved while base powers are cleared."""
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, bus)
    pp.create_load(net, bus, p_mw=0.21, q_mvar=0.08)
    net.load.at[0, "const_z_p_percent"] = 25.0

    sanitize_net_for_power_flow(net)

    assert net.load.at[0, "p_mw"] == 0.0
    assert net.load.at[0, "q_mvar"] == 0.0
    assert net.load.at[0, "const_z_p_percent"] == 25.0
    assert net.load.at[0, "scaling"] == 1.0
    assert bool(net.load.at[0, "in_service"]) is True