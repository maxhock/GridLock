"""Tests for the reinforcing grid federate and its rules."""

from unittest.mock import MagicMock

import pandapower as pp
import pytest

import main
from reinforce import (
    Measure,
    Settings,
    State,
    UnresolvedViolation,
    _loop,
    as_records,
    needs_reinforcement,
    reinforce,
    voltage_violations,
)

SETTINGS = Settings(
    max_loading_percent=100.0,
    voltage_band="split",
    earthwork="rural",
    max_iterations=20,
)


def lv_net(
    feeder_buses: int = 3,
    length_km: float = 0.1,
    p_mw: float = 0.05,
    std_type: str = "NAYY 4x50 SE",
    trafo: str = "0.63 MVA 20/0.4 kV",
) -> pp.pandapowerNet:
    """An MV slack, one MV/LV station and a single LV feeder with a load at its end.

    With no feeder buses the load sits on the station busbar.
    """
    net = pp.create_empty_network()
    mv = pp.create_bus(net, 20.0)
    busbar = pp.create_bus(net, 0.4)
    pp.create_ext_grid(net, mv)
    pp.create_transformer(net, mv, busbar, trafo)
    previous = busbar
    for _ in range(feeder_buses):
        bus = pp.create_bus(net, 0.4)
        pp.create_line(net, previous, bus, length_km, std_type)
        previous = bus
    pp.create_load(net, previous, p_mw=p_mw)
    pp.runpp(net)
    return net


def run(net: pp.pandapowerNet, settings: Settings = SETTINGS) -> tuple[list, State]:
    state = State()
    measures = reinforce(net, settings, state, lambda: pp.runpp(net), time=3600)
    return measures, state


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_parse_reads_every_key() -> None:
    spec = "max_loading_percent=80,voltage_band=flat,earthwork=urban,max_iterations=5"
    assert Settings.parse(spec) == Settings(80.0, "flat", "urban", 5)


@pytest.mark.parametrize(
    "spec",
    [
        "max_loading_percent=80,voltage_band=flat,earthwork=urban",
        "max_loading_percent=80,voltage_band=wide,earthwork=urban,max_iterations=5",
        "max_loading_percent=80,voltage_band=flat,earthwork=moon,max_iterations=5",
        "max_loading_percent=0,voltage_band=flat,earthwork=urban,max_iterations=5",
        "max_loading_percent=80,voltage_band=flat,earthwork=urban,max_iterations=5,x=1",
    ],
)
def test_settings_parse_refuses_incomplete_or_unknown(spec: str) -> None:
    with pytest.raises(ValueError):
        Settings.parse(spec)


# ---------------------------------------------------------------------------
# Overloading
# ---------------------------------------------------------------------------


def test_net_within_limits_is_left_alone() -> None:
    net = lv_net(feeder_buses=1, length_km=0.05)
    measures, state = run(net)
    assert measures == [] and state.cost_keur == 0.0


def test_single_cable_slightly_overloaded_gets_one_parallel_cable() -> None:
    net = lv_net(length_km=0.01, p_mw=0.15)
    assert net.res_line.loading_percent.max() > 100

    measures, state = run(net)

    assert {m.action for m in measures} == {"parallel_same_type"}
    assert net.line.parallel.tolist() == [2, 2, 2]
    assert net.line.std_type.unique().tolist() == ["NAYY 4x50 SE"]
    assert not needs_reinforcement(net, SETTINGS)
    # Each 10 m line: trench and cable at 60 kEUR/km (rural, cable included).
    assert state.cost_keur == pytest.approx(3 * 0.6)


def test_heavily_overloaded_line_is_replaced_by_standard_lines() -> None:
    net = lv_net(feeder_buses=1, length_km=0.01, p_mw=0.25)
    assert net.res_line.loading_percent.max() > 200

    measures, _ = run(net)

    line = [m for m in measures if m.element == "line"]
    assert line[0].action == "replace_by_standard"
    assert net.line.at[0, "std_type"] == "NAYY 4x150 SE"
    assert not needs_reinforcement(net, SETTINGS)


def test_overloaded_standard_line_gets_parallel_standard_lines() -> None:
    net = lv_net(feeder_buses=1, length_km=0.01, p_mw=0.35, std_type="NAYY 4x150 SE")
    relative = net.res_line.at[0, "loading_percent"] / 100

    measures, state = run(net)

    assert measures[0].action == "parallel_standard"
    assert net.line.at[0, "parallel"] == -(-relative // 1)
    # Earthwork once, then one cable per added line.
    assert state.cost_keur == pytest.approx(0.01 * (51 + 9 * measures[0].added))


def test_slightly_overloaded_trafo_gets_a_parallel_one() -> None:
    net = lv_net(feeder_buses=0, p_mw=0.3, trafo="0.25 MVA 20/0.4 kV")
    assert 100 < net.res_trafo.at[0, "loading_percent"] < 200

    measures, state = run(net)

    trafo = [m for m in measures if m.element == "trafo"]
    assert trafo[0].action == "parallel_same_type"
    assert net.trafo.at[0, "parallel"] == 2
    assert trafo[0].cost_keur == 10.0


def test_heavily_overloaded_trafo_becomes_standard() -> None:
    net = lv_net(feeder_buses=0, p_mw=0.5, trafo="0.25 MVA 20/0.4 kV")

    measures, _ = run(net)

    trafo = [m for m in measures if m.element == "trafo"]
    assert trafo[0].action == "replace_by_standard"
    assert net.trafo.at[0, "std_type"] == "0.63 MVA 20/0.4 kV"
    assert net.trafo.at[0, "parallel"] == trafo[0].added == 1
    assert not needs_reinforcement(net, SETTINGS)


# ---------------------------------------------------------------------------
# Voltage
# ---------------------------------------------------------------------------


def test_lv_limits_are_relative_to_the_station_busbar() -> None:
    net = lv_net(feeder_buses=2, length_km=0.2, p_mw=0.04)
    violations = voltage_violations(net, SETTINGS)
    busbar_vm = net.res_bus.at[1, "vm_pu"]
    far_vm = net.res_bus.at[3, "vm_pu"]
    # Within the flat +/-10 % band, but more than 6.5 % below the busbar.
    assert far_vm > 0.9 and busbar_vm - far_vm > 0.065
    assert 3 in violations.index
    assert voltage_violations(net, Settings(100.0, "flat", "rural", 20)).empty


def test_mv_buses_are_held_to_their_busbar_not_to_one_pu() -> None:
    # A 25 MVA HV/MV transformer at ~80 % drops the busbar well below 0.985 pu;
    # the short feeder behind it adds little, so no cable is at fault.
    net = pp.create_empty_network()
    hv, busbar, mv = (pp.create_bus(net, kv) for kv in (110.0, 20.0, 20.0))
    pp.create_ext_grid(net, hv)
    pp.create_transformer(net, hv, busbar, "25 MVA 110/20 kV")
    pp.create_line(net, busbar, mv, 0.2, "NA2XS2Y 1x240 RM/25 12/20 kV")
    pp.create_load(net, mv, p_mw=6.0, q_mvar=2.0)
    pp.create_load(net, busbar, p_mw=14.0, q_mvar=4.0)
    pp.runpp(net)
    assert net.res_bus.at[mv, "vm_pu"] < 1 - 0.015

    assert voltage_violations(net, SETTINGS).empty


def test_long_feeder_is_split_at_two_thirds() -> None:
    net = lv_net(feeder_buses=4, length_km=0.15, p_mw=0.06)

    measures, _ = run(net)

    split = [m for m in measures if m.action == "split_feeder"]
    assert len(split) == 1
    line = net.line.loc[split[0].index]
    # Reattached to the station busbar, over the path's length, as standard.
    assert 1 in (line.from_bus, line.to_bus)
    # 0.6 km to the far end; the first bus past 0.4 km sits at 0.45 km.
    assert line.length_km == pytest.approx(0.45)
    assert line.std_type == "NAYY 4x150 SE"
    assert not needs_reinforcement(net, SETTINGS)


def test_short_feeder_reinforces_its_path() -> None:
    # Two thirds of a one-line feeder is its head, which cannot be split off.
    net = lv_net(feeder_buses=1, length_km=0.4, p_mw=0.04)
    assert not voltage_violations(net, SETTINGS).empty

    measures, _ = run(net)

    assert measures and all(m.action != "split_feeder" for m in measures)
    assert not needs_reinforcement(net, SETTINGS)


def test_loop_refuses_to_run_forever() -> None:
    net = lv_net(feeder_buses=1, length_km=0.05)
    fake = Measure(0, "line", 0, "", "x", "overload", 1.5, 1, "t", 0.0)
    with pytest.raises(UnresolvedViolation, match="after 3 iterations"):
        _loop(
            net,
            Settings(100.0, "split", "rural", 3),
            0,
            "Overloading",
            lambda: True,
            lambda: [fake],
            lambda: None,
        )


# ---------------------------------------------------------------------------
# Federate
# ---------------------------------------------------------------------------

PUBS = [
    "lv/load_0/voltage",
    "lv/active_power",
    "lv/reactive_power",
    *(f"lv/{suffix}" for suffix in main.METRIC_SUFFIXES),
]


def federate(net: pp.pandapowerNet) -> main.ReinforcingGridFederate:
    fed = main.ReinforcingGridFederate("lv", net, SETTINGS)
    fed.data_from_federation = {"inputs": {"lv/house_0/load_0/active_power": 0.0}}
    fed.data_to_federation = {"publications": dict.fromkeys(PUBS)}
    fed.granted_time = 3600
    return fed


def test_federate_publishes_the_reinforced_voltages_and_the_seen_loading() -> None:
    net = lv_net(length_km=0.01, p_mw=0.0)
    fed = federate(net)
    fed.data_from_federation["inputs"]["lv/house_0/load_0/active_power"] = 150e3

    fed.update_internal_model()

    pubs = fed.data_to_federation["publications"]
    # The loading metric is what the step's load found, before any measure;
    # the voltages are those of the grid as it now stands.
    assert pubs["lv/max_line_loading"] > 100
    assert net.res_line.loading_percent.max() <= 100
    assert fed.state.cost_keur > 0
    assert pubs["lv/reinforcement_cost"] == pytest.approx(fed.state.cost_keur)
    assert pubs["lv/load_0/voltage"] == pytest.approx(net.res_bus.at[4, "vm_pu"])


def test_federate_refuses_missing_metric_key() -> None:
    fed = federate(lv_net())
    del fed.data_to_federation["publications"]["lv/min_vm"]
    with pytest.raises(ValueError, match="'min_vm'"):
        fed.update_internal_model()


def test_destroy_stores_the_report_before_leaving(mocker) -> None:
    fed = federate(lv_net(length_km=0.01, p_mw=0.0))
    fed.data_from_federation["inputs"]["lv/house_0/load_0/active_power"] = 150e3
    fed.update_internal_model()
    fed.scenario_name = "Run_20261116_120000"
    fed.metadata_manager = MagicMock()
    parent = mocker.patch.object(main.GridFederate, "destroy_federate")

    fed.destroy_federate()

    collection, key, doc = fed.metadata_manager.write.call_args.args
    assert (collection, key) == (
        "custom_metadata",
        "Run_20261116_120000__lv__reinforced",
    )
    assert fed.metadata_manager.write.call_args.kwargs == {"overwrite": False}
    assert doc["scenario"] == "Run_20261116_120000"
    assert doc["total_cost_keur"] == pytest.approx(fed.state.cost_keur)
    assert len(doc["measures"]) == len(fed.state.measures)
    reread = pp.from_json_string(doc["net_json"])
    assert reread.line.parallel.tolist() == net_parallel(fed.net)
    parent.assert_called_once()


def test_destroy_fails_loudly_when_the_report_is_not_stored(mocker) -> None:
    fed = federate(lv_net(length_km=0.01, p_mw=0.0))
    fed.scenario_name = "Run_20261116_120000"
    fed.metadata_manager = MagicMock()
    fed.metadata_manager.write.return_value = False
    parent = mocker.patch.object(main.GridFederate, "destroy_federate")

    with pytest.raises(RuntimeError, match="Run_20261116_120000__lv__reinforced"):
        fed.destroy_federate()
    parent.assert_not_called()


def test_a_second_run_keeps_the_first_runs_report(tmp_path, mocker) -> None:
    from cosim_toolbox.dbms import create_metadata_manager

    mocker.patch.object(main.GridFederate, "destroy_federate")
    manager = create_metadata_manager(backend="json", location=str(tmp_path))
    manager.connect()
    for scenario in ("Run_20261116_120000", "Run_20261116_130000"):
        fed = federate(lv_net(length_km=0.01, p_mw=0.0))
        fed.scenario_name = scenario
        fed.metadata_manager = manager
        fed.destroy_federate()

    for scenario in ("Run_20261116_120000", "Run_20261116_130000"):
        stored = manager.read("custom_metadata", main.report_key(scenario, "lv"))
        assert stored["scenario"] == scenario


def net_parallel(net: pp.pandapowerNet) -> list[int]:
    return net.line.parallel.tolist()


def test_report_round_trips_through_the_json_metadata_store(tmp_path) -> None:
    # The write happens as the run ends; a document CST cannot store would
    # lose the whole report then, so it goes through the real JSON backend.
    from cosim_toolbox.dbms import create_metadata_manager

    fed = federate(lv_net(feeder_buses=4, length_km=0.15, p_mw=0.0))
    fed.data_from_federation["inputs"]["lv/house_0/load_0/active_power"] = 60e3
    fed.update_internal_model()
    fed.scenario_name = "Run_20261116_120000"
    manager = create_metadata_manager(backend="json", location=str(tmp_path))
    manager.connect()
    key = main.report_key("Run_20261116_120000", "lv")

    manager.write("custom_metadata", key, fed.report(), True)
    stored = manager.read("custom_metadata", key)

    assert stored["measures"] == as_records(fed.state.measures)
    assert stored["violations"] == as_records(fed.state.violations)
    assert pp.from_json_string(stored["net_json"]).line.length_km.sum() > 0


def test_every_violation_is_recorded_not_only_the_one_a_measure_answers() -> None:
    net = lv_net(feeder_buses=4, length_km=0.15, p_mw=0.06)
    out_of_band = set(voltage_violations(net, SETTINGS).index)
    assert len(out_of_band) > 1

    measures, state = run(net)

    assert len([m for m in measures if m.reason == "voltage"]) < len(out_of_band)
    recorded = {v.index for v in state.violations if v.element == "bus"}
    assert recorded == out_of_band
    assert all(v.time == 3600 and v.value < v.limit for v in state.violations)
