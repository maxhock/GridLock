#!/usr/bin/env python3
"""Compare the minimal MV/LV co-simulation with one integrated pandapower net.

GridLock side: what the federates recorded in Postgres. The root MV grid does not
publish its own slack draw (it has no parent), so the HV/MV demand is recovered by
solving the MV net with the coupling power the MV federate actually applied; the
recovered MV load-bus voltage must match what the MV federate published, which
proves the recovery is the federate's own power flow.

Reference side: MV cable, MV/LV trafo, LV cable and the load in ONE net, solved
once per step with the ramp value of that step.
"""

import copy
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pandapower as pp
import psycopg2

HERE = Path(__file__).resolve().parent
INPUT = HERE.parent / "data" / "input"
OUT = HERE / "out"
ANALYSIS = "MinimalMvLvAnalysis"
GRIDLOCK, REFERENCE = "#2a78d6", "#eb6834"


def recorded(scenario: str) -> pd.DataFrame:
    conn = psycopg2.connect(
        host="localhost",
        port=os.environ["CST_POSTGRES_PORT"],
        dbname=os.environ["CST_POSTGRES_DB"],
        user=os.environ["CST_POSTGRES_USER"],
        password=os.environ["CST_POSTGRES_PASSWORD"],
    )
    try:
        df = pd.read_sql(
            f'SELECT sim_time, data_name, data_value FROM "{ANALYSIS}".hdt_double '
            "WHERE scenario = %s",
            conn,
            params=(scenario,),
        )
    finally:
        conn.close()
    wide = df.pivot(index="sim_time", columns="data_name", values="data_value")
    return wide.astype(float).sort_index()


def solve_mv(mv: pp.pandapowerNet, p_w: float, q_var: float) -> tuple[float, float, float]:
    mv.load.at[0, "p_mw"], mv.load.at[0, "q_mvar"] = p_w / 1e6, q_var / 1e6
    pp.runpp(mv)
    return (
        float(mv.res_ext_grid.p_mw.iat[0]) * 1e6,
        float(mv.res_ext_grid.q_mvar.iat[0]) * 1e6,
        float(mv.res_bus.vm_pu.at[int(mv.load.bus.iat[0])]),
    )


def gridlock_hv_mv(rec: pd.DataFrame) -> pd.DataFrame:
    """Re-solve the MV net with the coupling power, at each candidate lag."""
    mv = pp.from_json(str(INPUT / "mv_minimal_20kv.json"))
    p = rec["mv-grid_0/lv-grid_0/load_0/active_power"]
    q = rec["mv-grid_0/lv-grid_0/load_0/reactive_power"]
    v_pub = rec["mv-grid_0/load_0/voltage"]
    best = None
    for lag in (0, 1):
        rows = [solve_mv(mv, pi, qi) for pi, qi in zip(p.shift(lag).fillna(0), q.shift(lag).fillna(0))]
        out = pd.DataFrame(rows, index=rec.index, columns=["p_w", "q_var", "v_pu"])
        err = float((out.v_pu - v_pub).abs().max())
        print(f"  MV recovery with coupling lag {lag}: max |V - published V| = {err:.2e} pu")
        if best is None or err < best[1]:
            best = (out, err, lag)
    if best[1] > 1e-9:
        sys.exit("No lag reproduces the MV federate's published voltage; recovery is invalid.")
    print(f"  -> MV federate applied the LV draw with a lag of {best[2]} step(s).")
    return best[0]


def integrated_net() -> tuple[pp.pandapowerNet, int, int]:
    mv = pp.from_json(str(INPUT / "mv_minimal_20kv.json"))
    lv = pp.from_json(str(INPUT / "lv_minimal_20kv.json"))
    net = copy.deepcopy(mv)
    net.load.drop(net.load.index, inplace=True)
    mv_load_bus = int(mv.load.bus.iat[0])
    bus_map = {int(lv.ext_grid.bus.iat[0]): mv_load_bus}
    for b, row in lv.bus.iterrows():
        if b not in bus_map:
            bus_map[b] = pp.create_bus(net, row.vn_kv, name=row["name"])
    for _, t in lv.trafo.iterrows():
        pp.create_transformer(net, bus_map[t.hv_bus], bus_map[t.lv_bus], t.std_type, name=t["name"])
    for _, ln in lv.line.iterrows():
        pp.create_line(net, bus_map[ln.from_bus], bus_map[ln.to_bus], ln.length_km, ln.std_type, name=ln["name"])
    load = pp.create_load(net, bus_map[int(lv.load.bus.iat[0])], p_mw=0.0, name="ramp load")
    trafo = int(net.trafo.index[0])
    return net, load, trafo


def reference(ramp: pd.DataFrame, times: pd.Index) -> pd.DataFrame:
    net, load, trafo = integrated_net()
    rows = []
    for t in times:
        r = ramp.loc[int(t)]
        net.load.at[load, "p_mw"], net.load.at[load, "q_mvar"] = r.base_load / 1e6, r.reactive_power / 1e6
        pp.runpp(net)
        rows.append(
            {
                "hv_mv_p_w": float(net.res_ext_grid.p_mw.iat[0]) * 1e6,
                "mv_lv_p_w": float(net.res_trafo.p_hv_mw.at[trafo]) * 1e6,
                "load_p_w": float(net.res_load.p_mw.at[load]) * 1e6,
                "load_v_pu": float(net.res_bus.vm_pu.at[int(net.load.bus.at[load])]),
            }
        )
    return pd.DataFrame(rows, index=times)


def panel(ax, x, gl, ref, title, unit, gl_label="GridLock (co-simulation)"):
    ax.plot(x, gl, color=GRIDLOCK, lw=2, marker="o", ms=4, label=gl_label)
    ax.plot(x, ref, color=REFERENCE, lw=2, ls="--", marker="s", ms=4, label="Integrated pandapower")
    ax.set_title(title, loc="left", fontsize=11)
    ax.set_ylabel(unit)
    ax.grid(color="#e4e3df", lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=9)


def main() -> None:
    scenario = sys.argv[1]
    OUT.mkdir(exist_ok=True)
    rec = recorded(scenario)
    ramp = pd.read_csv(INPUT / "ramp_load_150kw.csv").set_index("timestamp")
    hv = gridlock_hv_mv(rec)
    ref = reference(ramp, rec.index)
    gl = pd.DataFrame(
        {
            "hv_mv_p_w": hv.p_w,
            "mv_lv_p_w": rec["mv-grid_0/lv-grid_0/load_0/active_power"],
            "load_p_w": rec["mv-grid_0/lv-grid_0/rampload_0/load_0/active_power"],
            "load_v_pu": rec["mv-grid_0/lv-grid_0/load_0/voltage"],
        }
    )
    table = gl.add_prefix("gridlock_").join(ref.add_prefix("integrated_"))
    table.to_csv(OUT / "comparison.csv")

    h = rec.index / 3600
    fig, axes = plt.subplots(3, 1, figsize=(9, 10), sharex=True)
    panel(axes[0], h, gl.hv_mv_p_w / 1e3, ref.hv_mv_p_w / 1e3, "Demand at the HV/MV point (MV ext_grid)", "P [kW]")
    panel(axes[1], h, gl.load_p_w / 1e3, ref.load_p_w / 1e3, "Demand at the load", "P [kW]",
          gl_label="GridLock (load player publishes)")
    panel(axes[2], h, gl.load_v_pu, ref.load_v_pu, "Voltage at the load bus", "V [pu]")
    axes[2].set_xlabel("Simulation time [h]")
    fig.suptitle("Minimal MV/LV: GridLock co-simulation vs one integrated pandapower net", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(OUT / "minimal_mvlv_comparison.png", dpi=150)

    # Same comparison with the GridLock series shifted back by the lag the
    # coupling introduces, so only the physics difference remains.
    diff = pd.DataFrame(
        {
            "HV/MV P lag-aligned [W]": gl.hv_mv_p_w.shift(-1) - ref.hv_mv_p_w,
            "load V lag-aligned [pu]": gl.load_v_pu.shift(-1) - ref.load_v_pu,
        }
    )
    print("\nLast steps (kW / pu):")
    print((table[["gridlock_hv_mv_p_w", "integrated_hv_mv_p_w", "gridlock_mv_lv_p_w",
                  "integrated_mv_lv_p_w"]] / 1e3).join(table[["gridlock_load_v_pu", "integrated_load_v_pu"]]).tail(5).round(4))
    print("\nLag-aligned max |difference|:")
    print(diff.abs().max())


if __name__ == "__main__":
    main()
