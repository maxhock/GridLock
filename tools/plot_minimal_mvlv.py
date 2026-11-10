#!/usr/bin/env python3
"""Compare a run of `config/experiment-minimal-mvlv.yml` with one integrated net.

GridLock side: what the federates recorded in Postgres. The HV/MV demand is the
root MV grid's published slack draw (`mv-grid_0/active_power`). As a cross-check,
the MV net is re-solved with the coupling power the MV federate applied: the
re-solved load-bus voltage must match the voltage the federate published, and the
re-solved slack draw must match the published one.

Reference side: the LV net fused onto the MV load bus (`mvlv_merge.py`), solved
once per step with the ramp value of that step. The nets and the ramp come from
`tools/make_minimal_mvlv.py`.

Writes `comparison.csv` and `minimal_mvlv_comparison.png` to
`generated/minimal_mvlv/`. Usage, after `./run.sh config/experiment-minimal-mvlv.yml`:
    python tools/plot_minimal_mvlv.py <MinimalMvLv_YYYYMMDD_HHMMSS> \
        --pg-port ... --pg-db ... --pg-user ... --pg-password ...
"""

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandapower as pp
import pandas as pd
import psycopg2
from mvlv_merge import build_merged_net, power_into_part
from parity_common import add_postgres_arguments, postgres_dsn

REPO = Path(__file__).resolve().parent.parent
INPUT = REPO / "data" / "input"
OUT = REPO / "generated" / "minimal_mvlv"
ANALYSIS = "MinimalMvLvAnalysis"
MV_LOAD = LV_LOAD = 0
GRIDLOCK, REFERENCE = "#2a78d6", "#eb6834"


def recorded(scenario: str, dsn: dict) -> pd.DataFrame:
    conn = psycopg2.connect(**dsn)
    try:
        df = pd.read_sql(
            f'SELECT sim_time, data_name, data_value FROM "{ANALYSIS}".hdt_double '
            "WHERE scenario = %s",
            conn,
            params=(scenario,),
        )
    finally:
        conn.close()
    if df.empty:
        sys.exit(f"No rows recorded for {scenario}.")
    wide = df.pivot(index="sim_time", columns="data_name", values="data_value")
    # A step a key has no row for becomes NaN, which every max() below would
    # skip, passing the checks over the remaining steps only.
    gaps = wide.isna().sum()
    if gaps.any():
        sys.exit(f"{scenario} has steps without a value: {gaps[gaps > 0].to_dict()}")
    return wide.astype(float).sort_index()


def solve_mv(
    mv: pp.pandapowerNet, p_w: float, q_var: float
) -> tuple[float, float, float]:
    mv.load.at[MV_LOAD, "p_mw"], mv.load.at[MV_LOAD, "q_mvar"] = p_w / 1e6, q_var / 1e6
    pp.runpp(mv)
    return (
        float(mv.res_ext_grid.p_mw.iat[0]) * 1e6,
        float(mv.res_ext_grid.q_mvar.iat[0]) * 1e6,
        float(mv.res_bus.vm_pu.at[int(mv.load.bus.at[MV_LOAD])]),
    )


def gridlock_hv_mv(rec: pd.DataFrame) -> pd.DataFrame:
    """Re-solve the MV net with the coupling power, at each candidate lag, as a check."""
    mv = pp.from_json(str(INPUT / "mv_minimal_20kv.json"))
    p = rec["mv-grid_0/lv-grid_0/load_0/active_power"]
    q = rec["mv-grid_0/lv-grid_0/load_0/reactive_power"]
    v_pub = rec["mv-grid_0/load_0/voltage"]
    best: tuple[pd.DataFrame, float, int] | None = None
    for lag in (0, 1):
        rows = [
            solve_mv(mv, pi, qi)
            for pi, qi in zip(p.shift(lag).fillna(0), q.shift(lag).fillna(0))
        ]
        out = pd.DataFrame(rows, index=rec.index, columns=["p_w", "q_var", "v_pu"])
        err = float((out.v_pu - v_pub).abs().max())
        print(
            f"  MV recovery with coupling lag {lag}: max |V - published V| = {err:.2e} pu"
        )
        if best is None or err < best[1]:
            best = (out, err, lag)
    assert best is not None  # the loop above always runs
    if best[1] > 1e-9:
        sys.exit(
            "No lag reproduces the MV federate's published voltage; recovery is invalid."
        )
    print(f"  -> MV federate applied the LV draw with a lag of {best[2]} step(s).")
    return best[0]


def reference(ramp: pd.DataFrame, times: pd.Index) -> pd.DataFrame:
    """The LV net fused onto the MV load bus, solved at each step's ramp value."""
    merged = build_merged_net(
        pp.from_json(str(INPUT / "mv_minimal_20kv.json")),
        {MV_LOAD: pp.from_json(str(INPUT / "lv_minimal_20kv.json"))},
    )
    net = merged.net
    load = merged.parts[MV_LOAD].load_ids[LV_LOAD]
    load_bus = merged.lv_bus_of_load(MV_LOAD, LV_LOAD)
    rows = []
    for t in times:
        r = ramp.loc[int(t)]
        net.load.at[load, "p_mw"] = r.base_load / 1e6
        net.load.at[load, "q_mvar"] = r.reactive_power / 1e6
        pp.runpp(net)
        rows.append(
            {
                "hv_mv_p_w": float(net.res_ext_grid.p_mw.iat[0]) * 1e6,
                "mv_lv_p_w": power_into_part(merged, MV_LOAD)[0],
                "load_p_w": float(net.res_load.p_mw.at[load]) * 1e6,
                "load_v_pu": float(net.res_bus.vm_pu.at[load_bus]),
            }
        )
    return pd.DataFrame(rows, index=times)


def panel(ax, x, gl, ref, title, unit, gl_label="GridLock (co-simulation)"):
    ax.plot(x, gl, color=GRIDLOCK, lw=2, marker="o", ms=4, label=gl_label)
    ax.plot(
        x,
        ref,
        color=REFERENCE,
        lw=2,
        ls="--",
        marker="s",
        ms=4,
        label="Integrated pandapower",
    )
    ax.set_title(title, loc="left", fontsize=11)
    ax.set_ylabel(unit)
    ax.grid(color="#e4e3df", lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=9)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", help="the run's timestamped scenario name")
    add_postgres_arguments(parser)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rec = recorded(args.scenario, postgres_dsn(args))
    ramp = pd.read_csv(INPUT / "ramp_load_150kw.csv").set_index("timestamp")
    hv = gridlock_hv_mv(rec)
    published = rec["mv-grid_0/active_power"]
    mismatch = float((hv.p_w - published).abs().max())
    print(f"  re-solved vs published MV slack draw: max |dP| = {mismatch:.2e} W")
    if mismatch > 1e-3:
        sys.exit(
            "The MV federate's published slack draw differs from its re-solved net."
        )
    ref = reference(ramp, rec.index)
    gl = pd.DataFrame(
        {
            "hv_mv_p_w": published,
            "mv_lv_p_w": rec["mv-grid_0/lv-grid_0/load_0/active_power"],
            "load_p_w": rec["mv-grid_0/lv-grid_0/rampload_0/load_0/active_power"],
            "load_v_pu": rec["mv-grid_0/lv-grid_0/load_0/voltage"],
        }
    )
    table = gl.add_prefix("gridlock_").join(ref.add_prefix("integrated_"))
    table.to_csv(OUT / "comparison.csv")

    h = rec.index / 3600
    fig, axes = plt.subplots(3, 1, figsize=(9, 10), sharex=True)
    panel(
        axes[0],
        h,
        gl.hv_mv_p_w / 1e3,
        ref.hv_mv_p_w / 1e3,
        "Demand at the HV/MV point (MV ext_grid)",
        "P [kW]",
    )
    panel(
        axes[1],
        h,
        gl.load_p_w / 1e3,
        ref.load_p_w / 1e3,
        "Demand at the load",
        "P [kW]",
        gl_label="GridLock (load player publishes)",
    )
    panel(axes[2], h, gl.load_v_pu, ref.load_v_pu, "Voltage at the load bus", "V [pu]")
    axes[2].set_xlabel("Simulation time [h]")
    fig.suptitle(
        "Minimal MV/LV: GridLock co-simulation vs one integrated pandapower net",
        x=0.01,
        ha="left",
    )
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
    print(
        (
            table[
                [
                    "gridlock_hv_mv_p_w",
                    "integrated_hv_mv_p_w",
                    "gridlock_mv_lv_p_w",
                    "integrated_mv_lv_p_w",
                ]
            ]
            / 1e3
        )
        .join(table[["gridlock_load_v_pu", "integrated_load_v_pu"]])
        .tail(5)
        .round(4)
    )
    print("\nLag-aligned max |difference|:")
    print(diff.abs().max())


if __name__ == "__main__":
    main()
