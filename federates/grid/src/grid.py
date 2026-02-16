"""Grid federate – runs pandapower power flow, exchanges load/voltage data via HELICS.

HELICS keys embed the pandapower load index (e.g. ``…/load_42/active_power``),
so values map directly to ``net.load`` rows without a lookup table.
"""

import re
from cosim_toolbox.sims import Federate
import pandapower as pp

_LOAD_RE = re.compile(r"/load_(\d+)/")


def _parse_load_index(key: str) -> int | None:
    """Extract pandapower load index from a HELICS key like ``…/load_3/active_power``."""
    m = _LOAD_RE.search(key)
    return int(m.group(1)) if m else None


class GridFederate(Federate):
    """Pandapower grid federate. Overrides only ``update_internal_model``."""

    def __init__(self, federate_name: str, net: pp.pandapowerNet) -> None:
        super().__init__(federate_name)
        self.net = net

    def update_internal_model(self) -> None:
        print(f"\n=== Time: {self.granted_time} ===")

        # 1. Apply received load values
        for key, value in self.data_from_federation.get("inputs", {}).items():
            idx = _parse_load_index(key)
            if value is None or idx is None:
                continue
            if key.endswith("/active_power"):
                self.net.load.at[idx, "p_mw"] = float(value)
            elif key.endswith("/reactive_power"):
                self.net.load.at[idx, "q_mvar"] = float(value)

        # 2. Run power flow
        try:
            pp.runpp(self.net, numba=False)
            print("Power flow converged.")
        except Exception as e:
            print(f"Power flow failed: {e}")
            return

        # 3. Publish per-load bus voltages
        for key in self.data_to_federation.get("publications", {}):
            idx = _parse_load_index(key)
            if idx is None or not key.endswith("/voltage"):
                continue
            bus = int(self.net.load.at[idx, "bus"])
            v_pu = float(self.net.res_bus.at[bus, "vm_pu"])
            self.data_to_federation["publications"][key] = v_pu
            print(f"  Published {key} = {v_pu:.4f} pu")