"""Load-player federate using CoSim Toolbox (CST).

Reads a timeseries CSV and publishes ``active_power`` (and optionally
``reactive_power``) at each co-simulation time step via HELICS.
"""

import numpy as np
import pandas as pd
from cosim_toolbox.sims import Federate


class LoadPlayerFederate(Federate):
    """CST federate that replays a load timeseries.

    The CSV must contain at least a ``timestamp`` column (Unix epoch
    seconds or ISO-8601) and a ``base_load`` column (watts).
    A ``reactive_power`` column is used when present, otherwise
    reactive power is assumed to be zero.

    Publication keys are discovered dynamically from the CST
    federation config – any key containing ``active_power`` or
    ``reactive_power`` is matched.
    """

    def __init__(self, federate_name: str, timeseries: pd.DataFrame) -> None:
        """Bind this federate to the profile it replays."""
        super().__init__(federate_name)
        self.timeseries = timeseries
        # Mapping built after create_federate()
        self._pub_p_keys: list[str] = []
        self._pub_q_keys: list[str] = []

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _build_pub_keys(self) -> None:
        """Discover this federate's power keys, one pair per load index.

        Scanned from the CST config rather than built by name, because composegen
        derives the keys from the experiment tree, not from a federate's own name.
        """
        pubs = self.data_to_federation.get("publications", {})
        self._pub_p_keys = sorted(
            k for k in pubs if k.endswith("/active_power") or k == "active_power"
        )
        self._pub_q_keys = sorted(
            k for k in pubs
            if k.endswith("/reactive_power") or k == "reactive_power"
        )
        print(
            f"Pub keys built: {len(self._pub_p_keys)} P, "
            f"{len(self._pub_q_keys)} Q"
        )

    # ------------------------------------------------------------------
    # CST lifecycle hooks
    # ------------------------------------------------------------------

    def create_federate(
        self,
        scenario_name: str = "",
        use_meta_db: str = "mongo",
        use_data_db: str = "postgres",
    ) -> None:
        """Create HELICS federate and discover publication keys."""
        super().create_federate(scenario_name, use_meta_db, use_data_db)
        self._build_pub_keys()

    def on_enter_executing_mode(self) -> None:
        """Publish initial t=0 state so grid has realistic loads from the start."""
        p_value, q_value = lookup_power(self.timeseries, 0.0)

        for key in self._pub_p_keys:
            self.data_to_federation["publications"][key] = p_value
        for key in self._pub_q_keys:
            self.data_to_federation["publications"][key] = q_value

        self.send_data_to_federation()

        print(
            f"t=0s (initial)  P={p_value:.2f} W  Q={q_value:.2f} VAr"
        )

    def update_internal_model(self) -> None:
        """Look up the current time step in the timeseries and publish."""
        current_time = self.granted_time
        p_value, q_value = lookup_power(self.timeseries, current_time)

        for key in self._pub_p_keys:
            self.data_to_federation["publications"][key] = p_value
        for key in self._pub_q_keys:
            self.data_to_federation["publications"][key] = q_value

        print(
            f"t={current_time:.0f}s  P={p_value:.2f} W  Q={q_value:.2f} VAr"
        )


# ---------------------------------------------------------------------------
# Timeseries loading / lookup helpers
# ---------------------------------------------------------------------------


def load_timeseries(csv_path: str) -> pd.DataFrame:
    """Read a load profile and put it on simulation-relative time.

    Requires ``timestamp`` (epoch seconds or ISO-8601) and ``base_load`` (W) columns;
    ``reactive_power`` (VAr) is optional and defaults to zero.
    """
    df = pd.read_csv(csv_path)

    if "timestamp" not in df.columns:
        raise ValueError(f"CSV {csv_path} missing required 'timestamp' column")
    if "base_load" not in df.columns:
        raise ValueError(f"CSV {csv_path} missing required 'base_load' column")

    # Convert ISO timestamps to epoch seconds if needed
    if df["timestamp"].dtype == object:
        df["timestamp"] = pd.to_datetime(df["timestamp"]).astype(int) // 10**9

    df = df.sort_values("timestamp").reset_index(drop=True)

    # Simulation time advances relative to scenario start (typically 0..duration).
    # If the CSV uses Unix epoch timestamps, shift them to start at 0 so lookup
    # aligns with the CST/HELICS granted time.
    first_timestamp = float(df["timestamp"].iloc[0])
    if first_timestamp > 1_000_000:
        df["timestamp"] = df["timestamp"] - first_timestamp
        print(
            "Normalized epoch timestamps to simulation-relative seconds "
            f"(offset={first_timestamp:.0f})."
        )

    if "reactive_power" not in df.columns:
        df["reactive_power"] = 0.0

    print(f"Loaded timeseries: {len(df)} rows from {csv_path}")
    return df


def lookup_power(
    timeseries: pd.DataFrame, sim_time: float
) -> tuple[float, float]:
    """Read (active power in W, reactive power in VAr) at a simulation time.

    Holds the nearest previous sample, and the first row for anything before it.
    ``sim_time`` is seconds since the scenario start, which is what
    ``load_timeseries`` normalised the CSV's own timestamps onto.
    """
    timestamps = timeseries["timestamp"].values
    idx = int(np.searchsorted(timestamps, sim_time, side="right")) - 1
    idx = max(0, idx)

    row = timeseries.iloc[idx]
    return float(row["base_load"]), float(row["reactive_power"])
