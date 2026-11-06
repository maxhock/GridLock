#!/usr/bin/env python3
"""Build the minimal MV and LV nets and the ramping load profile for the demo.

MV: ext_grid (20 kV busbar, stands for the HV/MV substation) -- 2 km cable -- load 0.
LV: ext_grid (20 kV) -- 20/0.4 kV trafo -- 0.2 km cable -- load 0.

The LV net needs its trafo because the coupling contract requires the LV slack bus
to sit at the voltage of the MV load bus it hangs from (20 kV).
"""

from pathlib import Path

import pandapower as pp

INPUT = Path(__file__).resolve().parent.parent / "data" / "input"

TIME_STEP_S = 900
STEPS = 25  # 0 .. 6 h inclusive
P_MAX_W = 150_000.0
TAN_PHI = 0.33


def mv_net() -> pp.pandapowerNet:
    net = pp.create_empty_network(name="mv_minimal")
    b0 = pp.create_bus(net, 20.0, name="MV busbar (HV/MV)")
    b1 = pp.create_bus(net, 20.0, name="MV load bus (MV/LV substation)")
    pp.create_ext_grid(net, b0, vm_pu=1.0, name="HV/MV infeed")
    pp.create_line(net, b0, b1, 2.0, "NA2XS2Y 1x185 RM/25 12/20 kV", name="MV cable")
    pp.create_load(net, b1, p_mw=0.0, name="LV grid")
    return net


def lv_net() -> pp.pandapowerNet:
    net = pp.create_empty_network(name="lv_minimal")
    b0 = pp.create_bus(net, 20.0, name="MV side of MV/LV trafo")
    b1 = pp.create_bus(net, 0.4, name="LV busbar")
    b2 = pp.create_bus(net, 0.4, name="LV load bus")
    pp.create_ext_grid(net, b0, vm_pu=1.0, name="MV coupling")
    pp.create_transformer(net, b0, b1, "0.25 MVA 20/0.4 kV", name="MV/LV trafo")
    pp.create_line(net, b1, b2, 0.2, "NAYY 4x150 SE", name="LV cable")
    pp.create_load(net, b2, p_mw=0.0, name="ramp load")
    return net


def main() -> None:
    pp.to_json(mv_net(), str(INPUT / "mv_minimal_20kv.json"))
    pp.to_json(lv_net(), str(INPUT / "lv_minimal_20kv.json"))
    rows = ["timestamp,base_load,reactive_power"]
    for i in range(STEPS + 1):  # one spare row past the end
        p = P_MAX_W * min(i, STEPS - 1) / (STEPS - 1)
        rows.append(f"{i * TIME_STEP_S},{p:.1f},{p * TAN_PHI:.1f}")
    (INPUT / "ramp_load_150kw.csv").write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
