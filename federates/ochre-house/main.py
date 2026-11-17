"""OCHRE house federate entry point: simulates one building with an OCHRE `Dwelling`.

The Dwelling is built from the three files the experiment names for this house:
an HPXML building description, an occupant/load schedule and a weather file.
composegen stores them in the CST metadata store; this federate writes them to a
scratch directory and hands the paths to OCHRE.

Usage:
    python main.py --scenario TestGrid_20260804_120000 --federate_name ochre-house_6
"""

import argparse
import datetime as dt
import io
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from cosim_toolbox.sims import Federate

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# OCHRE's schedules are annual and refuse leap years, so a run starts in this year.
# 2018 begins on a Monday, like 2024, the year of the house's sample dataset.
SIM_YEAR = 2018
SCRATCH_DIR = Path("/tmp/gridlock_ochre")

# A Gridlock exogenous dataset (the house's `sample_data.csv` shape) used as weather.
# It has no sun geometry (its solar columns are synthetic or PV output), so the solar
# column is read as global horizontal irradiance and treated as purely diffuse.
SOLAR_COLUMN = "solar_gains_w"
EXOGENOUS_WEATHER_COLUMNS = ("ambient_temp", SOLAR_COLUMN, "wind_speed_m_s")

# Not in the dataset, assumed constant. They enter OCHRE's psychrometrics only.
# (Water mains and ground temperature are the dataset's mean ambient temperature,
# see `write_ochre_inputs`: OCHRE derives them itself only from a full year.)
# TODO: Set from geoloccation if we have a geolocation and read from the config.
ASSUMED_RELATIVE_HUMIDITY = 0.7  # fraction
ASSUMED_PRESSURE_KPA = 95.0  # Munich, about 520 m

# OCHRE status key -> name under `loads_w` in the published `state` record.
END_USE_KEYS = {
    "HVAC Heating Electric Power (kW)": "heating",
    "HVAC Cooling Electric Power (kW)": "cooling",
    "Water Heating Electric Power (kW)": "water_heating",
    "Lighting Electric Power (kW)": "lighting",
    "Other Electric Power (kW)": "other",
}

# OCHRE reports PV as negative power, and only for a house whose HPXML has a PVSystem.
# Published like the `house` federate does: generation as a positive `pv_generation`.
PV_KEY = "PV Electric Power (kW)"

# OCHRE reads PV only from its `Equipment` argument, not from the HPXML. Tilt and
# azimuth match the `house` federate's default array (30 deg, south; OCHRE's azimuth is
# 0 = south); only the size is an experiment setting (`pv_capacity_kw`).
PV_TILT_DEG = 30.0
PV_AZIMUTH_DEG = 0.0


@dataclass(frozen=True)
class OchreInputs:
    """The files an OCHRE Dwelling is built from, written to local disk."""

    hpxml_file: str
    schedule_file: str
    weather_file: str
    weather_metadata: dict[str, float] | None
    # Extra Dwelling arguments OCHRE needs when the weather is not a full year.
    extra_args: dict[str, float]
    start_time: dt.datetime
    # OCHRE `Equipment` argument: devices added on top of the HPXML building.
    equipment: dict[str, dict[str, float]] = field(default_factory=dict)


# TODO: Read from config and InfDB ideally
def pv_equipment(document: dict) -> dict[str, dict[str, float]]:
    """The OCHRE `Equipment` entry for the house's PV, empty when it has none."""
    capacity_kw = document.get("pv_capacity_kw")
    if capacity_kw is None:
        return {}
    return {
        "PV": {
            "capacity": float(capacity_kw),
            "tilt": PV_TILT_DEG,
            "azimuth": PV_AZIMUTH_DEG,
        }
    }


def exogenous_to_ochre_weather(csv_text: str) -> pd.DataFrame:
    """Convert a Gridlock exogenous dataset to OCHRE's weather table.

    Timestamps are moved into ``SIM_YEAR`` by whole years, so the run keeps its
    calendar date and the dataset's own order. Raises on a missing column or a
    leap day, rather than letting OCHRE fail later or shift the data.
    """
    df = pd.read_csv(io.StringIO(csv_text))
    missing = [c for c in ("timestamp", *EXOGENOUS_WEATHER_COLUMNS) if c not in df]
    if missing:
        raise ValueError(f"Weather dataset is missing columns: {missing}")

    times = pd.DatetimeIndex(pd.to_datetime(df["timestamp"]))
    if ((times.month == 2) & (times.day == 29)).any():
        raise ValueError("Weather dataset contains a leap day; OCHRE cannot use it.")
    times = times + (pd.Timestamp(SIM_YEAR, 1, 1) - pd.Timestamp(times[0].year, 1, 1))

    ambient_c = df["ambient_temp"].to_numpy(dtype=float)
    solar = df[SOLAR_COLUMN].to_numpy(dtype=float)
    weather = pd.DataFrame(
        {
            "temp_air": ambient_c,
            "relative_humidity": ASSUMED_RELATIVE_HUMIDITY,
            "pressure": ASSUMED_PRESSURE_KPA,
            "ghi": solar,
            "dni": 0.0,
            "dhi": solar,
            "wind_speed": df["wind_speed_m_s"].to_numpy(dtype=float),
            # Swinbank clear-sky estimate, in K, converted back to deg C.
            "sky_temperature": 0.0552 * (ambient_c + 273.15) ** 1.5 - 273.15,
        },
        index=times,
    )
    weather.index.name = "Time"
    return weather


def write_ochre_inputs(federate_name: str, document: dict[str, Any]) -> OchreInputs:
    """Write the files composegen stored for this house and say how to load them.

    An EPW is handed to OCHRE as it is. A CSV is a Gridlock dataset: it is converted
    and passed with the location composegen stored beside it.
    """
    missing = [
        key
        for key in ("hpxml_xml", "schedule_csv", "weather_text", "weather_file")
        if not document.get(key)
    ]
    if missing:
        raise ValueError(
            f"No OCHRE inputs found in metadata store for '{federate_name}' "
            f"(missing {missing}). Ensure composegen ran with 'hpxml', 'schedule' "
            f"and 'weather' set for this house."
        )

    directory = SCRATCH_DIR / federate_name.replace("/", "_")
    directory.mkdir(parents=True, exist_ok=True)
    hpxml_file = directory / "house.xml"
    schedule_file = directory / "schedule.csv"
    hpxml_file.write_text(document["hpxml_xml"])
    schedule_file.write_text(document["schedule_csv"])

    if str(document["weather_file"]).lower().endswith(".epw"):
        weather_file = directory / "weather.epw"
        weather_file.write_text(document["weather_text"])
        return OchreInputs(
            str(hpxml_file),
            str(schedule_file),
            str(weather_file),
            None,
            {},
            dt.datetime(SIM_YEAR, 1, 1),
            pv_equipment(document),
        )

    location = document.get("weather_location")
    if not location:
        raise ValueError(
            f"CSV weather for '{federate_name}' has no location in the metadata "
            f"store; set latitude, longitude and timezone on the house."
        )
    weather = exogenous_to_ochre_weather(document["weather_text"])
    weather_file = directory / "weather.csv"
    weather.to_csv(weather_file)
    mean_ambient_c = float(weather["temp_air"].mean())
    return OchreInputs(
        str(hpxml_file),
        str(schedule_file),
        str(weather_file),
        dict(location),
        {
            "Mains Temperature (C)": mean_ambient_c,
            "Ground Temperature (C)": mean_ambient_c,
        },
        weather.index[0].to_pydatetime(),
        pv_equipment(document),
    )


def setup_dwelling(inputs: OchreInputs, dt_seconds: int, duration_s: float) -> Any:
    """Build the Dwelling from its input files, ready for `update()` calls.

    Imported lazily so the helpers in this module stay importable without OCHRE.
    """
    from ochre import Dwelling

    return Dwelling(
        name="ochre-house",
        start_time=inputs.start_time,
        time_res=dt.timedelta(seconds=dt_seconds),
        duration=dt.timedelta(seconds=duration_s),
        initialization_time=dt.timedelta(days=1),
        hpxml_file=inputs.hpxml_file,
        hpxml_schedule_file=inputs.schedule_file,
        weather_file=inputs.weather_file,
        weather_metadata=inputs.weather_metadata,
        **inputs.extra_args,
        **({"Equipment": inputs.equipment} if inputs.equipment else {}),
        verbosity=6,  # 6 is the lowest level that exposes outdoor temperature
        save_results=False,
    )


def serialize_dwelling_status(
    status: dict[str, Any], federate_name: str, time_s: float, dt_seconds: int
) -> dict:
    """Reduce one OCHRE status dict to the record published on the `state` topic.

    Same envelope as the house federate's record, so the two can be read side by
    side from the results DB. Raises KeyError naming the missing OCHRE key, so a
    renamed OCHRE output fails loudly instead of publishing zeros.
    """
    loads_w = {
        short: float(status[ochre_key]) * 1000.0
        for ochre_key, short in END_USE_KEYS.items()
    }
    loads_w["pv_generation"] = -float(status.get(PV_KEY, 0.0)) * 1000.0
    total_load_w = float(status["Total Electric Power (kW)"]) * 1000.0
    loads_w["grid_total"] = total_load_w

    return {
        "schema_version": 1,
        "federate": federate_name,
        "time": time_s,
        "dt_s": dt_seconds,
        "grid_exchange": {
            "total_load_w": total_load_w,
            "reactive_power_var": float(status["Total Reactive Power (kVAR)"]) * 1000.0,
            "sign_convention": "positive_consumption",
        },
        "loads_w": loads_w,
        "state": {
            "t_indoor_c": float(status["Temperature - Indoor (C)"]),
            "t_outdoor_c": float(status["Temperature - Outdoor (C)"]),
        },
    }


class OchreHouseFederate(Federate):
    """OCHRE house federate using CST for lifecycle/time management."""

    def __init__(self, federate_name: str):
        """Bind to a federate name; the Dwelling is built in `create_federate`."""
        super().__init__(federate_name)
        self.dwelling: Any = None
        self.load_key = ""
        self.reactive_power_key = ""
        self.state_key = ""

    def create_federate(
        self,
        scenario_name: str = "",
        use_meta_db: str = "mongo",
        use_data_db: str = "postgres",
    ) -> None:
        """Create the CST federate, register HELICS interfaces, build the Dwelling."""
        super().create_federate(scenario_name, use_meta_db, use_data_db)

        dt_seconds = int(self.period)
        if dt_seconds <= 0:
            raise ValueError(f"dt_seconds (period) must be > 0, got {dt_seconds}")

        self.load_key = self._find_pub_key("active_power")
        self.reactive_power_key = self._find_pub_key("reactive_power")
        self.state_key = self._find_pub_key("state")

        # One extra period of headroom: OCHRE refuses to step past its duration and
        # the HELICS loop also grants the final time.
        document = self.metadata_manager.read("custom_metadata", self.federate_name)
        inputs = write_ochre_inputs(self.federate_name, document or {})
        self.dwelling = setup_dwelling(inputs, dt_seconds, self.stop_time + self.period)

        logger.info(f"Federate '{self.federate_name}' publishing to:")
        logger.info(f"  - {self.load_key}")
        logger.info(f"  - {self.reactive_power_key}")
        logger.info(f"  - {self.state_key}")
        logger.info(
            f"[{self.federate_name}] Running with dt={dt_seconds}s "
            f"(stop_time={self.stop_time}s), weather {inputs.weather_file}."
        )

    def _find_pub_key(self, suffix: str) -> str:
        """Find the registered publication key ending in ``suffix``.

        Keys are discovered from ``self.data_to_federation`` (populated by
        the base class from the CST federation config) rather than guessed,
        since composegen derives the actual HELICS key names from the
        experiment tree rather than the federate's own name/index.
        """
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

    def _step_and_publish(self) -> dict:
        """Advance the Dwelling one period and queue its power and state."""
        assert self.dwelling is not None, "create_federate must run first"
        assert self.federate_name is not None
        record = serialize_dwelling_status(
            self.dwelling.update(),
            self.federate_name,
            self.granted_time,
            int(self.period),
        )
        publications = self.data_to_federation["publications"]
        publications[self.load_key] = record["grid_exchange"]["total_load_w"]
        publications[self.reactive_power_key] = record["grid_exchange"][
            "reactive_power_var"
        ]
        publications[self.state_key] = json.dumps(record)
        return record

    def on_enter_executing_mode(self) -> None:
        """Publish the first interval at t=0, before the first time step.

        An unpublished `double` reads as 0.0 downstream, which no subscriber can tell
        from a real zero. OCHRE has no status read-out without stepping, so t=0
        publishes the Dwelling's first step: the value at t is the power drawn over
        [t, t + period). The loop's steps at t=period, 2*period, ... follow, which is
        why `create_federate` asks OCHRE for one period more than the run.
        """
        self._step_and_publish()
        self.send_data_to_federation()

    def update_internal_model(self) -> None:
        """Advance the Dwelling by one CST-controlled time step."""
        record = self._step_and_publish()

        logger.info(
            f"t={self.granted_time:.0f}s  "
            f"P={record['grid_exchange']['total_load_w']:.0f} W  "
            f"Tin={record['state']['t_indoor_c']:.1f} C"
        )


def parse_args() -> argparse.Namespace:
    """Read the scenario and federate name composegen put on the CLI."""
    parser = argparse.ArgumentParser(description="OCHRE house federate using CST")
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
        help="Federate name matching meta_store entry (e.g. ochre-house_6)",
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
) -> None:
    """Run one ochre-house federate, taking the names from the CLI unless given."""
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name

    if scenario_name is None or federate_name is None:
        raise ValueError("scenario_name and federate_name are required")

    use_meta_db, use_data_db = get_db_backends_from_env()

    federate = OchreHouseFederate(federate_name)
    federate.run(
        scenario_name,
        use_meta_db=use_meta_db,
        use_data_db=use_data_db,
    )


if __name__ == "__main__":
    main()
