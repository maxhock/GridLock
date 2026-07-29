# controller/main.py

import json
import logging
import argparse
import os

import equinox as eqx
import jax.numpy as jnp

from cosim_toolbox.sims import Federate
from energysim.sim.simulator import JAXSimulator
from energysim.control.mpc_solver import JAX_MPC_Solver
from energysim.core.data.dataset import SimulationDataset
from house.common_config import create_common_configs
from house.build_my_house import create_2_room_house
from house.exogenous_data import (
    prepare_aligned_timeseries,
    write_exogenous_csv_from_metadata,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def setup_mpc_and_data(dt_seconds: int, exogenous_csv_path: str):
    """Setup MPC solver and aligned exogenous data.

    ``exogenous_csv_path`` must be the dataset of the house this controller
    drives. The MPC optimises against a load/PV/price forecast, so reading a
    different dataset than the house simulates means optimising for a
    building that does not exist.
    """
    dataset_info = prepare_aligned_timeseries(
        exogenous_csv_path,
        dt_seconds,
    )
    logger.info(
        f"Using exogenous dataset {dataset_info.path} "
        f"(source_dt={dataset_info.source_dt_seconds}s, "
        f"target_dt={dataset_info.target_dt_seconds}s, "
        f"rows={dataset_info.source_rows}->{dataset_info.aligned_rows})."
    )
    dataset = SimulationDataset(dataset_info.path, dt_seconds)

    configs = create_common_configs(dt_seconds=dt_seconds)
    t_config = configs["t_config"]

    sim_template = JAXSimulator(
        dt_seconds=dt_seconds,
        t_config=t_config,
        r_config=configs["r_config"],
        b_config=configs["b_config"],
        hp_config=configs["hp_config"],
        ac_config=configs["ac_config"],
        ts_config=configs["ts_config"],
        pv_config=configs["pv_config"],
    )

    mpc = JAX_MPC_Solver(N_horizon=24, simulator_template=sim_template)

    return mpc, dataset, sim_template


class ControllerFederate(Federate):
    """HEMS controller federate using CST for lifecycle/time management.

    Runs a battery-only MPC (see ``energysim.control.mpc_solver``) against
    the house's published state and its own copy of the load/PV/price
    forecast, then commands the house's battery through the house's
    existing ``control`` interface. Heat pump / AC / thermal storage are
    not part of this solver and are left at zero, same as before this
    federate was split out of the monolithic house simulation.
    """

    def __init__(self, federate_name: str, house_federate_name: str):
        super().__init__(federate_name)
        self.house_federate_name = house_federate_name
        self.state_key = ""
        self.control_key = ""
        self.mpc: JAX_MPC_Solver | None = None
        self.dataset: SimulationDataset | None = None
        self.sim_template: JAXSimulator | None = None
        self.max_steps = 0

    def create_federate(
        self,
        scenario_name: str = "",
        use_meta_db: str = "mongo",
        use_data_db: str = "postgres",
    ) -> None:
        """Create the CST federate and register HELICS interfaces."""
        super().create_federate(scenario_name, use_meta_db, use_data_db)

        dt_seconds = int(self.period)
        if dt_seconds <= 0:
            raise ValueError(f"dt_seconds (period) must be > 0, got {dt_seconds}")

        self.state_key = self._find_sub_key("state")
        self.control_key = self._find_pub_key("control")

        exogenous_csv_path = self._materialize_house_exogenous_data()

        self.mpc, self.dataset, self.sim_template = setup_mpc_and_data(
            dt_seconds, exogenous_csv_path
        )
        self.max_steps = len(self.dataset)

        logger.info(
            f"Federate '{self.federate_name}' forecasting from the exogenous "
            f"dataset of '{self.house_federate_name}'."
        )
        logger.info(f"Federate '{self.federate_name}' subscribing to:")
        logger.info(f"  - {self.state_key}")
        logger.info(f"Federate '{self.federate_name}' publishing to:")
        logger.info(f"  - {self.control_key}")

    def _materialize_house_exogenous_data(self) -> str:
        """Fetch the controlled house's exogenous dataset from the CST store.

        composegen writes each house's dataset into ``custom_metadata`` keyed
        by that house's federate name, and the house reads it back the same
        way. Reading the *house's* entry rather than a path baked into this
        image is what keeps the MPC's forecast and the house's simulation on
        the same data.
        """
        metadata = self.metadata_manager.read(
            "custom_metadata", self.house_federate_name
        )
        csv_text = (metadata or {}).get("exogenous_data_csv")

        if not csv_text:
            raise ValueError(
                f"No exogenous dataset found in the metadata store for house "
                f"'{self.house_federate_name}', which '{self.federate_name}' "
                f"controls. Ensure composegen ran with a valid "
                f"'exogenous_data' field for that house."
            )

        return write_exogenous_csv_from_metadata(self.federate_name, csv_text)

    def _find_pub_key(self, suffix: str) -> str:
        """Find the registered publication key ending in ``suffix``."""
        matches = [
            key
            for key in self.data_to_federation["publications"]
            if key == suffix or key.endswith(f"/{suffix}")
        ]
        if not matches:
            raise ValueError(
                f"No publication ending in '{suffix}' found for federate "
                f"'{self.federate_name}'. Registered publications: "
                f"{list(self.data_to_federation['publications'])}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple publications ending in '{suffix}' found for "
                f"federate '{self.federate_name}': {matches}"
            )
        return matches[0]

    def _find_sub_key(self, suffix: str) -> str:
        """Find the registered subscription key ending in ``suffix``."""
        matches = [
            key
            for key in self.data_from_federation["inputs"]
            if key == suffix or key.endswith(f"/{suffix}")
        ]
        if not matches:
            raise ValueError(
                f"No subscription ending in '{suffix}' found for federate "
                f"'{self.federate_name}'. Registered subscriptions: "
                f"{list(self.data_from_federation['inputs'])}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple subscriptions ending in '{suffix}' found for "
                f"federate '{self.federate_name}': {matches}"
            )
        return matches[0]

    def _read_battery_soc(self) -> float | None:
        """Extract the house's current battery SOC from its published state."""
        state_str = self.data_from_federation["inputs"].get(self.state_key)
        if not state_str or isinstance(state_str, list):
            return None

        try:
            payload = json.loads(state_str)
        except json.JSONDecodeError:
            logger.warning(
                f"Time {self.granted_time}s | Could not decode house state JSON."
            )
            return None

        try:
            return float(payload["state"]["battery"]["soc"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                f"Time {self.granted_time}s | House state missing battery SOC."
            )
            return None

    def update_internal_model(self) -> None:
        """Advance the controller by one CST-controlled time step."""
        dt_seconds = int(self.period)
        step_idx = int(self.granted_time // dt_seconds)

        current_soc = self._read_battery_soc()

        if current_soc is None:
            logger.info(
                f"Time {self.granted_time}s | No valid house state yet, "
                "publishing zero action."
            )
            self.data_to_federation["publications"][self.control_key] = json.dumps(
                {"battery_power_w": 0.0}
            )
            return

        horizon = self.mpc.N
        if step_idx + horizon >= self.max_steps:
            logger.info(
                f"Time {self.granted_time}s | Not enough forecast horizon left, "
                "publishing zero action."
            )
            self.data_to_federation["publications"][self.control_key] = json.dumps(
                {"battery_power_w": 0.0}
            )
            return

        current_sim = eqx.tree_at(
            lambda s: s.battery.soc, self.sim_template, jnp.array(current_soc)
        )
        exo_forecast = self.dataset.get_forecast(step_idx, horizon)
        action = self.mpc.solve(current_sim, exo_forecast)

        battery_power_w = float(action.battery_power_w)

        self.data_to_federation["publications"][self.control_key] = json.dumps(
            {"battery_power_w": battery_power_w}
        )

        logger.info(
            f"Time {self.granted_time}s | MPC action: "
            f"battery_power_w={battery_power_w:.1f}, soc={current_soc:.3f}"
        )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Namespace with scenario and federate_name.
    """
    parser = argparse.ArgumentParser(description="HEMS controller federate using CST")
    parser.add_argument(
        "--scenario",
        type=str,
        required=True,
        help="CST scenario name (e.g. TestGridScenario)",
    )
    parser.add_argument(
        "--federate_name",
        type=str,
        required=True,
        help="Federate name matching meta_store entry (e.g. house_4.hems_0)",
    )
    parser.add_argument(
        "--house_federate",
        type=str,
        required=True,
        help=(
            "Federate name of the house this controller drives (e.g. "
            "local-grid.house_4). Its exogenous dataset is used for the "
            "MPC forecast."
        ),
    )
    args, _ = parser.parse_known_args()
    return args


def get_db_backends_from_env() -> tuple[str, str]:
    """Read DB backends from environment variables set by composegen."""
    use_meta_db = os.getenv("CST_USE_META_DB")
    use_data_db = os.getenv("CST_USE_DATA_DB")

    if not use_meta_db or not use_data_db:
        raise ValueError(
            "Missing DB backend env vars. Expected CST_USE_META_DB and "
            "CST_USE_DATA_DB from experiment general config."
        )

    return use_meta_db, use_data_db


def main(
    scenario_name: str | None = None,
    federate_name: str | None = None,
    house_federate_name: str | None = None,
) -> None:
    """Run the HEMS controller federate using CST lifecycle.

    Args:
        scenario_name: CST scenario name. If None, parsed from CLI.
        federate_name: Federate name. If None, parsed from CLI.
        house_federate_name: Federate name of the controlled house. If None,
            parsed from CLI.
    """
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name
        house_federate_name = args.house_federate

    if scenario_name is None or federate_name is None:
        raise ValueError("scenario_name and federate_name are required")

    if house_federate_name is None:
        raise ValueError("house_federate_name is required")

    use_meta_db, use_data_db = get_db_backends_from_env()

    federate = ControllerFederate(federate_name, house_federate_name)
    federate.run(
        scenario_name,
        use_meta_db=use_meta_db,
        use_data_db=use_data_db,
    )


if __name__ == "__main__":
    main()
