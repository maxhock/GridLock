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


def _net_json(num_loads: int) -> str:
    """A pandapower net reduced to the load table composegen reads from it.

    pandapower is not installed in the composegen image, and `_read_load_list`
    only looks at `net["_object"]["load"]`, so the serialisation is built by
    hand in pandapower's own format (a DataFrame as `orient="split"` JSON).
    """
    load_table = {
        "columns": ["name", "bus"],
        "index": list(range(num_loads)),
        "data": [[f"load_{i}", i + 1] for i in range(num_loads)],
    }
    return json.dumps({"_object": {"load": {"_object": json.dumps(load_table)}}})


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


@pytest.fixture
def generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generated:
    """Generate the local-grid federation in ``tmp_path``."""
    data_dir = tmp_path / "input"
    data_dir.mkdir()
    meta_store = tmp_path / "meta_store"

    # The HEMS asks for a 24-step forecast beyond the 24 hour run.
    _write_timeseries(data_dir / "load_profile.csv", 86400, 3600)
    _write_timeseries(data_dir / "house_data.csv", 2 * 86400, 3600)
    pd.DataFrame({"bus": [0]}).to_excel(data_dir / "layout.xlsx", index=False)

    # What the infdb stage writes for a local layout.
    (meta_store / "custom_metadata").mkdir(parents=True)
    seed = {"grid_id": GRID_ID, "net_json": _net_json(NUM_LOADS)}
    (meta_store / "custom_metadata" / f"{GRID_ID}.json").write_text(json.dumps(seed))

    config_path = tmp_path / "experiment.yml"
    config_path.write_text(EXPERIMENT_YAML.format(meta_store=meta_store))

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
