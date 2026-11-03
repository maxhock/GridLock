"""End-to-end test of the tree-mode ETL: extract -> transform -> load.

The whole federation is generated against a JSON CST metadata store in a temp
directory, seeded the way the infdb stage would seed it, and the result is
pinned: federate names, commands, and the HELICS keys each federate publishes
and subscribes to.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from extract import extract
from load import load
from transform import transform

GRID_ID = "local-grid"
NUM_LOADS = 8

# Same shape as config/experiment-local-grid.yml. The two paths are the only
# differences: the layout file is a stub (infdb, not composegen, reads it) and
# the meta store is redirected into the temp directory.
EXPERIMENT_YAML = """
general:
  name: "TestGrid"
  start_time: 0
  end_time: 86400
  time_step: 3600
  duration: 86400
  use_meta_db: "json"
  use_data_db: "postgres"
  meta_store_path: "{meta_store}"
federation:
  id: "local-grid"
  name: "Local Pandapower Grid"
  class: "grid"
  config:
    layout: "layout.xlsx"
  sub_federates:
    - id: "loadhouse_0"
      name: "Load profile house"
      class: "load"
      config:
        placement: "fill"
        electrical_load: "load_profile.csv"
    - id: "house_0"
      name: "sim house"
      class: "house"
      config:
        placement: [4,6]
        exogenous_data: "house_data.csv"
        model:
          class: "1R1C"
          resistance: "5 Ohm"
          capacitance: "8 F"
      sub_federates:
        - id: "battery_0"
          name: "BYD Battery"
          class: "battery"
          config:
            placement: 0
            capacity: "5kWh"
            power: "5 kW"
        - id: "pv_1"
          name: "Sunwell PV"
          class: "pv"
          config:
            capacity: "5 5 kW"
            max_production: "renewables_ninja2019_munich.csv"
        - id: "hems_0"
          name: "Smart HEMS"
          class: "hems"
          config:
            control_strategy: "mpc"
            accessible: ["battery_0", "pv_1"]
"""


def _table(columns: list[str], rows: list[list]) -> dict:
    """A pandapower DataFrame in its `orient="split"` JSON serialisation."""
    return {
        "_module": "pandas.core.frame",
        "_class": "DataFrame",
        "_object": json.dumps(
            {"columns": columns, "index": list(range(len(rows))), "data": rows}
        ),
    }


def _net_json(num_loads: int, vn_kv: float = 0.4, ext_grids: int = 1) -> str:
    """A pandapower net reduced to the tables composegen reads from it.

    pandapower is not installed in the composegen image, and `_read_net_tables`
    only looks at the `load`, `bus` and `ext_grid` tables of `net["_object"]`,
    so the serialisation is built by hand in pandapower's own format. Bus 0 is
    the slack bus; load `i` sits on bus `i + 1`. Every bus has ``vn_kv``.
    """
    tables = {
        "load": _table(
            ["name", "bus"], [[f"load_{i}", i + 1] for i in range(num_loads)]
        ),
        "bus": _table(["vn_kv"], [[vn_kv] for _ in range(num_loads + 1)]),
        "ext_grid": _table(["bus"], [[0] for _ in range(ext_grids)]),
    }
    return json.dumps({"_object": tables})


def _write_timeseries(path: Path, seconds: int, step: int) -> None:
    """Write a CSV with a `timestamp` column covering ``seconds`` seconds."""
    timestamps = list(range(0, seconds + step, step))
    pd.DataFrame({"timestamp": timestamps, "power": [1.0] * len(timestamps)}).to_csv(
        path, index=False
    )


@dataclass
class Generated:
    """What one composegen run left behind."""

    scenario: str
    federates: dict[str, dict]
    compose: str
    meta_store: Path

    def command(self, federate: str) -> str:
        return self.federates[federate]["command"]

    def pubs(self, federate: str) -> set[str]:
        return self._keys(federate, "publications")

    def subs(self, federate: str) -> set[str]:
        return self._keys(federate, "subscriptions")

    def _keys(self, federate: str, kind: str) -> set[str]:
        helics = self.federates[federate]["HELICS_config"]
        return {entry["key"] for entry in helics.get(kind, [])}


def _generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    experiment_yaml: str,
    nets: dict[str, str],
    layouts: Iterable[str],
) -> Generated:
    """Run extract -> transform -> load for ``experiment_yaml`` in ``tmp_path``.

    ``nets`` maps each ``custom_metadata`` key the infdb stage would have
    written to that net's JSON (see ``_net_json``); ``layouts`` are the stub layout
    files extract checks for under the data input path.
    """
    data_dir = tmp_path / "input"
    data_dir.mkdir()
    meta_store = tmp_path / "meta_store"

    # The HEMS asks for a 24-step forecast beyond the 24 hour run.
    _write_timeseries(data_dir / "load_profile.csv", 86400, 3600)
    _write_timeseries(data_dir / "house_data.csv", 2 * 86400, 3600)
    for layout in layouts:
        if layout.endswith(".xlsx"):
            pd.DataFrame({"bus": [0]}).to_excel(data_dir / layout, index=False)
        else:
            (data_dir / layout).write_text("{}")

    # What the infdb stage writes for a local layout.
    (meta_store / "custom_metadata").mkdir(parents=True)
    for net_key, net_json in nets.items():
        seed = {"grid_id": net_key, "net_json": net_json}
        (meta_store / "custom_metadata" / f"{net_key}.json").write_text(
            json.dumps(seed)
        )

    config_path = tmp_path / "experiment.yml"
    config_path.write_text(experiment_yaml.format(meta_store=meta_store))

    # DockerRunner.define_yaml writes ./generated/docker-compose.yaml.
    monkeypatch.chdir(tmp_path)

    extracted = extract(
        config_path=config_path, data_input_path=data_dir, output_path=tmp_path
    )
    load(transform(extracted))

    (federation_file,) = (meta_store / "federations").glob("*.json")
    (scenario_file,) = (meta_store / "scenarios").glob("*.json")

    return Generated(
        scenario=scenario_file.stem,
        federates=json.loads(federation_file.read_text())["federation"],
        compose=(tmp_path / "generated" / "docker-compose.yaml").read_text(),
        meta_store=meta_store,
    )


@pytest.fixture
def generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generated:
    """Generate the local-grid federation in ``tmp_path``."""
    return _generate(
        tmp_path,
        monkeypatch,
        EXPERIMENT_YAML,
        nets={GRID_ID: _net_json(NUM_LOADS)},
        layouts=["layout.xlsx"],
    )


def _grid_keys(loads: list[int], publisher: str) -> set[str]:
    """Power keys a federate publishes for each of the grid's loads."""
    return {
        f"{GRID_ID}/{publisher}/load_{idx}/{quantity}"
        for idx in loads
        for quantity in ("active_power", "reactive_power")
    }


def _voltage_keys(loads: Iterable[int]) -> set[str]:
    return {f"{GRID_ID}/load_{idx}/voltage" for idx in loads}


def test_federate_names(generated: Generated) -> None:
    # `house_0` at placement [4, 6] becomes one house per load, named after it.
    assert set(generated.federates) == {
        "local-grid",
        "local-grid.loadhouse_0",
        "local-grid.house_4",
        "local-grid.house_4.hems_0",
        "local-grid.house_6",
        "local-grid.house_6.hems_0",
    }


def test_federate_commands(generated: Generated) -> None:
    scenario = generated.scenario
    assert {name: generated.command(name) for name in generated.federates} == {
        "local-grid": (
            f"python3 main.py --scenario {scenario} "
            "--federate_name local-grid --net_key local-grid"
        ),
        "local-grid.loadhouse_0": (
            f"python3 main.py --scenario {scenario} "
            "--federate_name local-grid.loadhouse_0 "
            "--timeseries /data/input/load_profile.csv"
        ),
        "local-grid.house_4": (
            f"python3 house/main.py --scenario {scenario} "
            "--federate_name local-grid.house_4"
        ),
        "local-grid.house_6": (
            f"python3 house/main.py --scenario {scenario} "
            "--federate_name local-grid.house_6"
        ),
        "local-grid.house_4.hems_0": (
            f"python3 controller/main.py --scenario {scenario} "
            "--federate_name local-grid.house_4.hems_0 "
            "--house_federate local-grid.house_4 --horizon 24"
        ),
        "local-grid.house_6.hems_0": (
            f"python3 controller/main.py --scenario {scenario} "
            "--federate_name local-grid.house_6.hems_0 "
            "--house_federate local-grid.house_6 --horizon 24"
        ),
    }


def test_grid_keys(generated: Generated) -> None:
    # The grid publishes a voltage for every load, driven or not (load_4 and
    # load_6 belong to the houses, the rest to the load player), and
    # subscribes to the power of exactly the loads that have a federate.
    assert generated.pubs("local-grid") == _voltage_keys(range(NUM_LOADS))
    assert generated.subs("local-grid") == (
        _grid_keys([0, 1, 2, 3, 5, 7], "loadhouse_0")
        | _grid_keys([4], "house_4")
        | _grid_keys([6], "house_6")
    )


def test_load_player_keys(generated: Generated) -> None:
    # One federate replays one profile on every load `fill` resolved to.
    fed = "local-grid.loadhouse_0"
    assert generated.pubs(fed) == _grid_keys([0, 1, 2, 3, 5, 7], "loadhouse_0")
    assert generated.subs(fed) == _voltage_keys([0, 1, 2, 3, 5, 7])


@pytest.mark.parametrize("load_idx", [4, 6])
def test_house_and_hems_keys(generated: Generated, load_idx: int) -> None:
    house = f"house_{load_idx}"
    house_fed = f"{GRID_ID}.{house}"
    hems_fed = f"{house_fed}.hems_0"

    assert generated.pubs(house_fed) == _grid_keys([load_idx], house) | {
        f"{GRID_ID}/{house}/state"
    }
    assert generated.subs(house_fed) == _voltage_keys([load_idx]) | {
        f"{GRID_ID}/{house}/hems_0/control"
    }
    assert generated.pubs(hems_fed) == {f"{GRID_ID}/{house}/hems_0/control"}
    assert generated.subs(hems_fed) == {f"{GRID_ID}/{house}/state"}


def test_only_the_grid_waits_for_current_time(generated: Generated) -> None:
    waiting = {
        name
        for name, fed in generated.federates.items()
        if fed["HELICS_config"].get("wait_for_current_time_update")
    }
    assert waiting == {GRID_ID}


def test_compose_has_a_service_per_federate(generated: Generated) -> None:
    services = {
        line[2:-1]
        for line in generated.compose.splitlines()
        if line.startswith("  ") and not line.startswith("   ") and line.endswith(":")
    }
    assert services == {"helics"} | set(generated.federates)
    # The broker is sized for the federates (it adds 2 for itself).
    assert "helics_broker --ipv4 -f 6 " in generated.compose


@pytest.mark.parametrize("house", ["house_4", "house_6"])
def test_house_exogenous_data_is_stored_for_the_house(
    generated: Generated, house: str
) -> None:
    doc = json.loads(
        (
            generated.meta_store / "custom_metadata" / f"{GRID_ID}.{house}.json"
        ).read_text()
    )
    assert doc["source_file"] == "house_data.csv"
    assert doc["exogenous_data_csv"].startswith("timestamp,power")


# ---------------------------------------------------------------------------
# MV grid with LV grids on its loads (issue #65)
# ---------------------------------------------------------------------------

MV_ID = "mv-grid_0"
MV_LOADS = 6
LV_LOADS = 8

# MV loads 1 and 3 each carry an LV grid (one clone per load), load 5 a unique
# feeder, and the MV-level player fills the rest: 0, 2 and 4. The house and the
# load player under `lv-grid_0` are replicated into every clone.
MV_LV_YAML = """
general:
  name: "TestMvLv"
  start_time: 0
  end_time: 86400
  time_step: 3600
  duration: 86400
  use_meta_db: "json"
  use_data_db: "postgres"
  meta_store_path: "{meta_store}"
federation:
  id: "mv-grid_0"
  name: "MV grid"
  class: "grid"
  config:
    layout: "mv.json"
  sub_federates:
    - id: "mvload_0"
      name: "MV load player"
      class: "load"
      config:
        placement: {mv_load_placement}
        electrical_load: "load_profile.csv"
    - id: "lv-grid_0"
      name: "LV grid"
      class: "grid"
      config:
        layout: "lv.json"
        placement: {lv_placement}
      sub_federates:
        - id: "lvload_0"
          name: "LV load player"
          class: "load"
          config:
            placement: "fill"
            electrical_load: "load_profile.csv"
        - id: "house_0"
          name: "sim house"
          class: "house"
          config:
            placement: [4, 6]
            exogenous_data: "house_data.csv"
            model:
              class: "1R1C"
              resistance: "5 Ohm"
              capacitance: "8 F"
          sub_federates:
            - id: "hems_0"
              name: "Smart HEMS"
              class: "hems"
              config:
                control_strategy: "mpc"
    - id: "lv-feeder_0"
      name: "LV feeder"
      class: "grid"
      config:
        layout: "feeder.json"
        placement: 5
      sub_federates:
        - id: "feederload_0"
          name: "Feeder load player"
          class: "load"
          config:
            placement: "fill"
            electrical_load: "load_profile.csv"
"""

# MV and LV are both 20 kV: the LV nets are the 20 kV variants, so the slack
# of an LV grid has the voltage base of the MV bus it hangs off.
MV_KV = 20.0
LV_TEMPLATE = f"{MV_ID}.lv-grid_0"
MV_LV_NETS = {
    MV_ID: _net_json(MV_LOADS, vn_kv=MV_KV),
    LV_TEMPLATE: _net_json(LV_LOADS, vn_kv=MV_KV),
    f"{MV_ID}.lv-feeder_0": _net_json(LV_LOADS, vn_kv=MV_KV),
}
MV_LV_LAYOUTS = ["mv.json", "lv.json", "feeder.json"]


def _mv_lv_yaml(mv_load_placement: str = '"fill"', lv_placement: str = "[1, 3]") -> str:
    # `.format` is applied later to fill in the meta store, so the braces that
    # belong to the YAML flow lists are not touched: only these two keys are.
    return MV_LV_YAML.replace("{mv_load_placement}", mv_load_placement).replace(
        "{lv_placement}", lv_placement
    )


@pytest.fixture
def mv_lv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generated:
    return _generate(tmp_path, monkeypatch, _mv_lv_yaml(), MV_LV_NETS, MV_LV_LAYOUTS)


def _lv_clone_federates(clone: str) -> set[str]:
    """Everything one clone of ``lv-grid_0`` brings with it."""
    fed = f"{MV_ID}.{clone}"
    return {
        fed,
        f"{fed}.lvload_0",
        f"{fed}.house_4",
        f"{fed}.house_4.hems_0",
        f"{fed}.house_6",
        f"{fed}.house_6.hems_0",
    }


def _keys(fed: str, loads: Iterable[int], quantities: Iterable[str]) -> set[str]:
    """HELICS keys ``<fed>/load_<idx>/<quantity>`` for a dotted federate name."""
    path = fed.replace(".", "/")
    return {f"{path}/load_{idx}/{q}" for idx in loads for q in quantities}


def _power(fed: str, loads: Iterable[int]) -> set[str]:
    return _keys(fed, loads, ("active_power", "reactive_power"))


def test_mv_lv_federate_set(mv_lv: Generated) -> None:
    # `lv-grid_0` at [1, 3] is cloned per MV load, subtree included; the house
    # inside is split per LV load as it is in any other grid.
    assert set(mv_lv.federates) == (
        {MV_ID, f"{MV_ID}.mvload_0"}
        | _lv_clone_federates("lv-grid_1")
        | _lv_clone_federates("lv-grid_3")
        | {f"{MV_ID}.lv-feeder_5", f"{MV_ID}.lv-feeder_5.feederload_0"}
    )


def test_mv_lv_grid_commands_point_clones_at_the_template_net(
    mv_lv: Generated,
) -> None:
    scenario = mv_lv.scenario

    def grid_command(fed: str, net_key: str) -> str:
        return (
            f"python3 main.py --scenario {scenario} "
            f"--federate_name {fed} --net_key {net_key}"
        )

    assert mv_lv.command(MV_ID) == grid_command(MV_ID, MV_ID)
    for clone in ("lv-grid_1", "lv-grid_3"):
        assert mv_lv.command(f"{MV_ID}.{clone}") == grid_command(
            f"{MV_ID}.{clone}", f"{MV_ID}.lv-grid_0"
        )
    assert mv_lv.command(f"{MV_ID}.lv-feeder_5") == grid_command(
        f"{MV_ID}.lv-feeder_5", f"{MV_ID}.lv-feeder_0"
    )


def test_mv_lv_only_the_root_grid_waits_for_current_time(mv_lv: Generated) -> None:
    waiting = {
        name
        for name, fed in mv_lv.federates.items()
        if fed["HELICS_config"].get("wait_for_current_time_update")
    }
    assert waiting == {MV_ID}


def test_mv_lv_mv_grid_keys(mv_lv: Generated) -> None:
    # The MV grid publishes a voltage on every load and reads power from what
    # sits on it: its own player on 0, 2, 4 and one LV federate each on 1, 3, 5.
    assert mv_lv.pubs(MV_ID) == _keys(MV_ID, range(MV_LOADS), ["voltage"])
    assert mv_lv.subs(MV_ID) == (
        _power(f"{MV_ID}.mvload_0", [0, 2, 4])
        | _power(f"{MV_ID}.lv-grid_1", [1])
        | _power(f"{MV_ID}.lv-grid_3", [3])
        | _power(f"{MV_ID}.lv-feeder_5", [5])
    )


def test_mv_lv_fill_player_holds_exactly_the_unclaimed_mv_loads(
    mv_lv: Generated,
) -> None:
    fed = f"{MV_ID}.mvload_0"
    assert mv_lv.pubs(fed) == _power(fed, [0, 2, 4])
    assert mv_lv.subs(fed) == _keys(MV_ID, [0, 2, 4], ["voltage"])


@pytest.mark.parametrize("idx", [1, 3])
def test_mv_lv_clone_is_coupled_to_its_parent_load(mv_lv: Generated, idx: int) -> None:
    clone = f"{MV_ID}.lv-grid_{idx}"
    house_4, house_6 = f"{clone}.house_4", f"{clone}.house_6"
    lvload = f"{clone}.lvload_0"

    # Upstream: power out on the parent's load, voltage in from it.
    # Downstream: a voltage per LV load, power in from the clone's own children.
    assert mv_lv.pubs(clone) == _power(clone, [idx]) | _keys(
        clone, range(LV_LOADS), ["voltage"]
    )
    assert mv_lv.subs(clone) == (
        _keys(MV_ID, [idx], ["voltage"])
        | _power(lvload, [0, 1, 2, 3, 5, 7])
        | _power(house_4, [4])
        | _power(house_6, [6])
    )


def test_mv_lv_the_subtree_is_replicated_per_clone(mv_lv: Generated) -> None:
    for idx in (1, 3):
        clone = f"{MV_ID}.lv-grid_{idx}"
        house = f"{clone}.house_4"
        hems = f"{house}.hems_0"
        house_path = house.replace(".", "/")

        assert mv_lv.pubs(f"{clone}.lvload_0") == _power(
            f"{clone}.lvload_0", [0, 1, 2, 3, 5, 7]
        )
        assert mv_lv.pubs(house) == _power(house, [4]) | {f"{house_path}/state"}
        assert mv_lv.subs(house) == _keys(clone, [4], ["voltage"]) | {
            f"{house_path}/hems_0/control"
        }
        assert mv_lv.pubs(hems) == {f"{house_path}/hems_0/control"}
        assert mv_lv.subs(hems) == {f"{house_path}/state"}
        assert mv_lv.command(hems).endswith(
            f"--federate_name {hems} --house_federate {house} --horizon 24"
        )


def test_mv_lv_feeder_covers_every_lv_load(mv_lv: Generated) -> None:
    feeder = f"{MV_ID}.lv-feeder_5"
    player = f"{feeder}.feederload_0"

    assert mv_lv.pubs(feeder) == _power(feeder, [5]) | _keys(
        feeder, range(LV_LOADS), ["voltage"]
    )
    assert mv_lv.pubs(player) == _power(player, range(LV_LOADS))


def test_mv_lv_house_exogenous_data_is_stored_per_clone(mv_lv: Generated) -> None:
    for idx in (1, 3):
        doc = mv_lv.meta_store / "custom_metadata"
        assert (doc / f"{MV_ID}.lv-grid_{idx}.house_4.json").exists()


def test_mv_lv_placement_overlapping_an_mv_player_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clashing = _mv_lv_yaml(mv_load_placement="[3, 4]")

    with pytest.raises(ValueError, match=r"\[3\].*claimed by multiple federates"):
        _generate(tmp_path, monkeypatch, clashing, MV_LV_NETS, MV_LV_LAYOUTS)


def test_mv_lv_lv_net_with_two_ext_grids_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nets = {**MV_LV_NETS, LV_TEMPLATE: _net_json(LV_LOADS, MV_KV, ext_grids=2)}

    with pytest.raises(ValueError) as excinfo:
        _generate(tmp_path, monkeypatch, _mv_lv_yaml(), nets, MV_LV_LAYOUTS)

    # Both nets are named, so the message says which attachment is broken.
    message = str(excinfo.value)
    assert "exactly one ext_grid, found 2" in message
    assert f"'{LV_TEMPLATE}'" in message
    assert f"'{MV_ID}'" in message


def test_mv_lv_mismatched_voltage_base_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nets = {**MV_LV_NETS, LV_TEMPLATE: _net_json(LV_LOADS, vn_kv=10.0)}

    with pytest.raises(ValueError) as excinfo:
        _generate(tmp_path, monkeypatch, _mv_lv_yaml(), nets, MV_LV_LAYOUTS)

    message = str(excinfo.value)
    assert "20.0 kV bus" in message
    assert "10.0 kV" in message
    assert f"'{LV_TEMPLATE}'" in message
    assert f"'{MV_ID}'" in message
    assert "tools/make_lv_variant.py" in message


def test_mv_lv_voltage_is_labelled_per_unit(mv_lv: Generated) -> None:
    # The grid publishes `res_bus.vm_pu`, so no voltage key may claim volts.
    federation = mv_lv.federates[MV_ID]["HELICS_config"]
    units = {entry["unit"] for entry in federation["publications"]}
    assert units == {"pu"}


def test_sibling_named_like_a_grid_clone_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `lv-grid_0` on load 1 runs as `lv-grid_1`. A load player declared under
    # that id after it would replace the clone's config and coupling keys.
    clashing = (
        _mv_lv_yaml(mv_load_placement="[0, 2]")
        + """\
    - id: "lv-grid_1"
      name: "MV load player 2"
      class: "load"
      config:
        placement: [4]
        electrical_load: "load_profile.csv"
"""
    )

    with pytest.raises(ValueError, match=f"'{MV_ID}.lv-grid_1' is already registered"):
        _generate(tmp_path, monkeypatch, clashing, MV_LV_NETS, MV_LV_LAYOUTS)


def test_house_named_like_a_grid_net_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `lv-feeder_9` on load 0 runs as `lv-feeder_0`, which is the key the
    # `lv-feeder_0` template keeps its net under (it runs as `lv-feeder_5`).
    clashing = (
        _mv_lv_yaml(mv_load_placement="[2, 4]")
        + """\
    - id: "lv-feeder_9"
      name: "MV house"
      class: "house"
      config:
        placement: [0]
        exogenous_data: "house_data.csv"
        model:
          class: "1R1C"
          resistance: "5 Ohm"
          capacitance: "8 F"
"""
    )

    with pytest.raises(ValueError, match=r"\['mv-grid_0\.lv-feeder_0'\]"):
        _generate(tmp_path, monkeypatch, clashing, MV_LV_NETS, MV_LV_LAYOUTS)

    net_doc = tmp_path / "meta_store" / "custom_metadata" / f"{MV_ID}.lv-feeder_0.json"
    assert "net_json" in json.loads(net_doc.read_text())


def test_failed_coupling_writes_no_house_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The houses under `lv-grid_0` are wired before `lv-feeder_0`'s coupling
    # is checked; their datasets must not be stored for a run that never comes.
    nets = {**MV_LV_NETS, f"{MV_ID}.lv-feeder_0": _net_json(LV_LOADS, vn_kv=10.0)}

    with pytest.raises(ValueError, match="the bases must match"):
        _generate(tmp_path, monkeypatch, _mv_lv_yaml(), nets, MV_LV_LAYOUTS)

    stored = {
        path.stem for path in (tmp_path / "meta_store" / "custom_metadata").iterdir()
    }
    assert stored == set(nets)


def test_registering_a_grid_federate_twice_is_refused() -> None:
    from types import SimpleNamespace

    from load import _GridWiringContext, _register_grid_federate

    federates: dict[str, object] = {}
    federation = SimpleNamespace(
        federates=federates,
        add_federate_config=lambda fed: federates.__setitem__(fed.name, fed),
    )
    ctx = _GridWiringContext(
        federation=federation,
        tree=None,  # type: ignore[arg-type]
        scenario_name="s",
        time_step=3600,
        grid_waits=True,
        use_meta_db="json",
        meta_store_path="unused",
        data_input_path=Path("unused"),
        handled_nodes=set(),
    )

    _register_grid_federate(ctx, "mv-grid_0.lv-grid_1", "mv-grid_0.lv-grid_0", False)

    with pytest.raises(ValueError, match="already registered"):
        _register_grid_federate(
            ctx, "mv-grid_0.lv-grid_1", "mv-grid_0.lv-grid_0", False
        )


LOCATION_YAML = """
general:
  name: "TestLocation"
  start_time: 0
  end_time: 86400
  time_step: 3600
  duration: 86400
  use_meta_db: "json"
  use_data_db: "postgres"
  meta_store_path: "{meta_store}"
federation:
  id: "loc-grid"
  name: "Location grid"
  class: "grid"
  config:
    location:
      - plz: "80331"
  sub_federates:
    - id: "loadhouse_0"
      name: "Load profile house"
      class: "load"
      config:
        placement: "fill"
        electrical_load: "load_profile.csv"
"""


def test_several_root_grids_leave_the_ordering_slot_unclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plz-only query resolves to every net infdb stored for that postcode.
    # HELICS allows one waiting federate, so with two root grids none gets it.
    nets = {f"loc-grid_80331_1_{bcid}": _net_json(NUM_LOADS) for bcid in (1, 2)}

    result = _generate(tmp_path, monkeypatch, LOCATION_YAML, nets, layouts=[])

    assert {name for name in result.federates if name.count(".") == 0} == set(nets)
    waiting = {
        name
        for name, fed in result.federates.items()
        if fed["HELICS_config"].get("wait_for_current_time_update")
    }
    assert waiting == set()
