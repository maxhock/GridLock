"""Grid federate using CoSim Toolbox (CST).

Runs pandapower power flow and exchanges data via HELICS.
Subscription/publication keys are read dynamically from the
CST federation config — no hardcoded topic names.
"""

from cosim_toolbox.sims import Federate
import pandapower as pp


class GridFederate(Federate):
    """Grid federate with pandapower power flow simulation.

    CST auto-populates ``data_from_federation["inputs"]`` and
    ``data_to_federation["publications"]`` with keys from the
    federation JSON.  This class maps those keys to pandapower
    load / bus indices by convention:

    * Subscription keys containing ``active_power``  → ``net.load.p_mw``
    * Subscription keys containing ``reactive_power`` → ``net.load.q_mvar``
    * Publication keys containing ``voltage``         → bus voltage result
    """

    def __init__(self, federate_name: str, net: pp.pandapowerNet) -> None:
        super().__init__(federate_name)
        self.net = net
        # Mapping built once after create_federate(): HELICS key → pp load index
        self._sub_p_map: dict[str, int] = {}
        self._sub_q_map: dict[str, int] = {}
        # Mapping: HELICS key → pp bus index for voltage publication
        self._pub_v_map: dict[str, int] = {}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _build_key_maps(self) -> None:
        """Build mappings from HELICS keys to pandapower indices.

        Called once after ``create_federate()`` has populated
        ``data_from_federation`` and ``data_to_federation``.

        Strategy:
        * Subscriptions with ``active_power`` / ``reactive_power``
          are assigned round-robin to ``net.load`` indices.
        * Publications with ``voltage`` are assigned round-robin
          to ``net.load.bus`` (the bus each load is connected to).
        """
        load_indices = list(self.net.load.index)
        load_buses = [int(self.net.load.at[i, "bus"]) for i in load_indices]

        # --- subscriptions → loads --------------------------------
        p_keys = sorted(
            k for k in self.data_from_federation.get("inputs", {})
            if "active_power" in k
        )
        q_keys = sorted(
            k for k in self.data_from_federation.get("inputs", {})
            if "reactive_power" in k
        )

        for idx, key in enumerate(p_keys):
            pp_idx = load_indices[idx % len(load_indices)] if load_indices else 0
            self._sub_p_map[key] = pp_idx

        for idx, key in enumerate(q_keys):
            pp_idx = load_indices[idx % len(load_indices)] if load_indices else 0
            self._sub_q_map[key] = pp_idx

        # --- publications → voltages on load buses ----------------
        v_keys = sorted(
            k for k in self.data_to_federation.get("publications", {})
            if "voltage" in k
        )
        for idx, key in enumerate(v_keys):
            bus = load_buses[idx % len(load_buses)] if load_buses else 0
            self._pub_v_map[key] = bus

        print(
            f"Key maps built: {len(self._sub_p_map)} P subs, "
            f"{len(self._sub_q_map)} Q subs, {len(self._pub_v_map)} V pubs"
        )

    # ------------------------------------------------------------------
    # CST lifecycle hooks
    # ------------------------------------------------------------------

    def create_federate(
        self,
        scenario_name: str = "",
        use_meta_db: bool = False,
        use_data_db: bool = False,
    ) -> None:
        """Create HELICS federate and build key→index maps."""
        super().create_federate(scenario_name, use_meta_db, use_data_db)
        self._build_key_maps()

    def update_internal_model(self) -> None:
        """Read subscribed loads, run power flow, publish voltages."""
        print(f"\n=== Time: {self.granted_time} ===")

        # 1. Apply received active power values to pandapower loads
        for key, pp_idx in self._sub_p_map.items():
            value = self.data_from_federation["inputs"].get(key)
            if value is not None:
                self.net.load.at[pp_idx, "p_mw"] = float(value)

        # 2. Apply received reactive power values
        for key, pp_idx in self._sub_q_map.items():
            value = self.data_from_federation["inputs"].get(key)
            if value is not None:
                self.net.load.at[pp_idx, "q_mvar"] = float(value)

        # 3. Run power flow
        try:
            pp.runpp(self.net, numba=False)
            print("Power flow converged.")
        except Exception as e:
            print(f"Power flow failed: {e}")
            return

        # 4. Publish bus voltages
        for key, bus in self._pub_v_map.items():
            v_pu = float(self.net.res_bus.at[bus, "vm_pu"])
            self.data_to_federation["publications"][key] = v_pu
            print(f"  Published {key} = {v_pu:.4f} pu")