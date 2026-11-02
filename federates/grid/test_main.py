"""Unit tests for the grid federate's pure helpers."""

import pandapower as pp
import pytest

import main
from main import (
    GridFederate,
    _GridKeys,
    _parse_load_index,
    load_net_from_metadata,
    sanitize_net_for_power_flow,
)


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


def test_sanitize_resets_load_scaling_to_one() -> None:
    net = _net_with_two_loads()
    net.load["scaling"] = [0.3, 0.0]
    net.load["p_mw"] = [0.2, 0.1]
    net.load["q_mvar"] = [0.05, 0.02]

    sanitize_net_for_power_flow(net)

    assert (net.load["scaling"] == 1.0).all()
    assert (net.load["p_mw"] == 0.0).all()
    assert (net.load["q_mvar"] == 0.0).all()


def test_sanitize_zeroes_static_sgens_and_keeps_in_service() -> None:
    net = _net_with_one_load()
    pp.create_sgen(net, 0, p_mw=1.0, q_mvar=0.2, in_service=False)

    sanitize_net_for_power_flow(net)

    assert net.sgen.at[0, "p_mw"] == 0.0
    assert net.sgen.at[0, "q_mvar"] == 0.0
    assert bool(net.sgen.at[0, "in_service"]) is False


def test_sanitize_zeroes_sgens_in_a_net_without_loads() -> None:
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=20.0)
    pp.create_ext_grid(net, bus)
    pp.create_sgen(net, bus, p_mw=1.0, q_mvar=0.2)

    sanitize_net_for_power_flow(net)

    assert net.load.empty
    assert net.sgen.at[0, "p_mw"] == 0.0
    assert net.sgen.at[0, "q_mvar"] == 0.0


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


class _FakeMetadataManager:
    """Stands in for the CST metadata manager and records what was read."""

    def __init__(self, documents: dict[str, dict]) -> None:
        self.documents = documents
        self.requested: list[tuple[str, str]] = []

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def read(self, collection: str, name: str) -> dict | None:
        self.requested.append((collection, name))
        return self.documents.get(name)


def test_load_net_from_metadata_reads_the_document_named_by_the_net_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeMetadataManager(
        {"lv-template": {"net_json": pp.to_json(_net_with_two_loads())}}
    )
    monkeypatch.setattr(main, "create_metadata_manager", lambda **_: manager)

    net = load_net_from_metadata("lv-template")

    assert manager.requested == [("custom_metadata", "lv-template")]
    assert len(net.load) == 2


def test_load_net_from_metadata_missing_document_names_the_net_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeMetadataManager({})
    monkeypatch.setattr(main, "create_metadata_manager", lambda **_: manager)

    with pytest.raises(FileNotFoundError, match="lv-template"):
        load_net_from_metadata("lv-template")


# ---------------------------------------------------------------------------
# Coupling to a parent grid
# ---------------------------------------------------------------------------

_V_IN = "mv-grid_0/load_3/voltage"
_P_OUT = "mv-grid_0/lv-grid_3/load_3/active_power"
_Q_OUT = "mv-grid_0/lv-grid_3/load_3/reactive_power"
_HOUSE_P = "mv-grid_0/lv-grid_3/house_0/load_0/active_power"
_HOUSE_Q = "mv-grid_0/lv-grid_3/house_0/load_0/reactive_power"
_OWN_V = "mv-grid_0/lv-grid_3/load_0/voltage"


def _lv_net_behind_trafo() -> pp.pandapowerNet:
    """10 kV slack, a 10/0.4 kV trafo and two 0.4 kV loads (indices 0 and 1)."""
    net = pp.create_empty_network()
    hv = pp.create_bus(net, vn_kv=10.0)
    lv = pp.create_bus(net, vn_kv=0.4)
    far = pp.create_bus(net, vn_kv=0.4)
    pp.create_ext_grid(net, hv, vm_pu=1.0)
    pp.create_transformer(net, hv, lv, std_type="0.25 MVA 10/0.4 kV")
    pp.create_line(net, lv, far, length_km=0.1, std_type="NAYY 4x50 SE")
    pp.create_load(net, lv, p_mw=0.0, q_mvar=0.0)
    pp.create_load(net, far, p_mw=0.0, q_mvar=0.0)
    sanitize_net_for_power_flow(net)
    return net


def _coupled_federate(
    net: pp.pandapowerNet, voltage: float | None = None
) -> GridFederate:
    federate = GridFederate("mv-grid_0/lv-grid_3", net)
    federate.data_from_federation["inputs"] = {
        _V_IN: voltage,
        _HOUSE_P: 20000.0,
        _HOUSE_Q: 5000.0,
        "mv-grid_0/lv-grid_3/house_1/load_1/active_power": 10000.0,
    }
    federate.data_to_federation["publications"] = {
        _P_OUT: None,
        _Q_OUT: None,
        _OWN_V: None,
        "mv-grid_0/lv-grid_3/load_1/voltage": None,
    }
    return federate


def test_grid_keys_classifies_a_mixed_key_set() -> None:
    keys = _GridKeys.classify(
        "lv",
        [_V_IN, _HOUSE_P, _HOUSE_Q, "lv/house_1/load_1/active_power", "lv/other"],
        [_P_OUT, _Q_OUT, _OWN_V],
    )

    assert keys.upstream_voltage == _V_IN
    assert keys.upstream_p == _P_OUT
    assert keys.upstream_q == _Q_OUT
    assert keys.voltage_pubs == {_OWN_V: 0}
    assert keys.load_inputs == {
        _HOUSE_P: (0, "p_mw"),
        _HOUSE_Q: (0, "q_mvar"),
        "lv/house_1/load_1/active_power": (1, "p_mw"),
    }
    assert keys.has_upstream


def test_grid_keys_of_a_root_grid_have_no_upstream() -> None:
    keys = _GridKeys.classify("lv", ["lv/h/load_0/active_power"], ["lv/load_0/voltage"])

    assert not keys.has_upstream
    assert keys.upstream_p is None
    assert keys.upstream_q is None


def test_grid_keys_reject_two_upstream_voltage_inputs() -> None:
    with pytest.raises(ValueError, match="lv-x.*load_3/voltage.*load_4/voltage"):
        _GridKeys.classify(
            "lv-x",
            ["mv/load_3/voltage", "mv/load_4/voltage"],
            [_P_OUT, _Q_OUT],
        )


def test_grid_keys_reject_two_upstream_power_publications() -> None:
    with pytest.raises(ValueError, match="active_power publication"):
        _GridKeys.classify("lv", [_V_IN], [_P_OUT, "x/load_5/active_power", _Q_OUT])


@pytest.mark.parametrize(
    "input_keys, pub_keys",
    [
        ([_V_IN], [_OWN_V]),  # voltage without a reply
        ([], [_P_OUT, _Q_OUT]),  # reply without a voltage
        ([_V_IN], [_P_OUT]),  # no reactive_power
        ([_V_IN], [_Q_OUT]),  # no active_power
    ],
)
def test_grid_keys_reject_a_partial_upstream(
    input_keys: list[str], pub_keys: list[str]
) -> None:
    with pytest.raises(ValueError, match="lv.*partial upstream"):
        _GridKeys.classify("lv", input_keys, pub_keys)


def test_upstream_voltage_drives_the_slack_and_the_bus_voltages() -> None:
    nominal = _coupled_federate(_lv_net_behind_trafo(), voltage=1.0)
    nominal.update_internal_model()
    raised_net = _lv_net_behind_trafo()
    raised = _coupled_federate(raised_net, voltage=1.03)

    raised.update_internal_model()

    assert raised_net.ext_grid.at[0, "vm_pu"] == pytest.approx(1.03)
    assert raised.data_to_federation["publications"][_OWN_V] > (
        nominal.data_to_federation["publications"][_OWN_V] + 0.02
    )


def test_upstream_power_is_the_slack_draw_including_losses() -> None:
    net = _lv_net_behind_trafo()
    federate = _coupled_federate(net, voltage=1.0)

    federate.update_internal_model()

    publications = federate.data_to_federation["publications"]
    assert publications[_P_OUT] == pytest.approx(net.res_ext_grid.at[0, "p_mw"] * 1e6)
    assert publications[_Q_OUT] == pytest.approx(net.res_ext_grid.at[0, "q_mvar"] * 1e6)
    # Drawn from the parent (positive), and more than the children consume.
    assert publications[_P_OUT] > 30000.0


@pytest.mark.parametrize("voltage", [None, -1e49])
def test_missing_upstream_voltage_keeps_the_nets_setpoint(
    voltage: float | None,
) -> None:
    net = _lv_net_behind_trafo()
    net.ext_grid.at[0, "vm_pu"] = 1.02

    _coupled_federate(net, voltage=voltage).update_internal_model()

    assert net.ext_grid.at[0, "vm_pu"] == pytest.approx(1.02)


def test_upstream_voltage_key_never_drives_a_load() -> None:
    """``.../load_3/voltage`` names the MV load, not LV load 3 (which is absent)."""
    net = _lv_net_behind_trafo()
    federate = _coupled_federate(net, voltage=1.01)

    federate.update_internal_model()

    assert _V_IN not in federate.grid_keys.load_inputs
    assert list(net.load.index) == [0, 1]
    assert net.load["p_mw"].tolist() == pytest.approx([0.02, 0.01])
    assert net.load["q_mvar"].tolist() == pytest.approx([0.005, 0.0])


def test_upstream_requires_exactly_one_ext_grid() -> None:
    net = _lv_net_behind_trafo()
    pp.create_ext_grid(net, int(net.trafo.at[0, "hv_bus"]))

    with pytest.raises(
        ValueError, match="mv-grid_0/lv-grid_3.*exactly one ext_grid.*2"
    ):
        _coupled_federate(net, voltage=1.0).update_internal_model()


def test_root_grid_publishes_no_upstream_power() -> None:
    net = _lv_net_behind_trafo()
    federate = GridFederate("root", net)
    federate.data_from_federation["inputs"] = {"root/h/load_0/active_power": 1000.0}
    federate.data_to_federation["publications"] = {"root/load_0/voltage": None}

    federate.update_internal_model()

    assert list(federate.data_to_federation["publications"]) == ["root/load_0/voltage"]
    assert net.ext_grid.at[0, "vm_pu"] == 1.0
