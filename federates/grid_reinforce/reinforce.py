"""Grid reinforcement rules, applied to a solved pandapower net.

The measures follow the heuristic of eDisGo (openego/eDisGo,
``edisgo/flex_opt/reinforce_grid.py`` and ``reinforce_measures.py``), which in
turn follows what German DSOs commonly do and the dena distribution grid study.
eDisGo is AGPL-3.0 and built on PyPSA, so its code is neither imported nor
copied: the rules are reimplemented here on pandapower's tables.

The module knows nothing about HELICS. ``reinforce`` takes a net whose power
flow has just been solved, a ``solve`` callback that re-runs it, and the
state carried between steps, and returns the measures it applied.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import networkx as nx
import pandas as pd
import pandapower as pp
import pandapower.topology as top

# ---------------------------------------------------------------------------
# Catalogue: eDisGo's defaults (config_grid_expansion_default.cfg), expressed
# as pandapower standard types.
# ---------------------------------------------------------------------------

# Buses at or below this are LV, below _HV_KV MV; HV is the upstream slack side,
# which eDisGo does not check either.
_LV_KV_MAX = 1.0
_HV_KV = 60.0

# eDisGo: lv_line NAYY 4x1x150, mv_line_10kv NA2XS2Y 3x1x185, mv_line_20kv
# NA2XS2Y 3x1x240.
_STANDARD_LV_LINE = "NAYY 4x150 SE"
_STANDARD_MV_LINE = {
    10: "NA2XS2Y 1x185 RM/25 6/10 kV",
    20: "NA2XS2Y 1x240 RM/25 12/20 kV",
}
# eDisGo: mv_lv_transformer 630 kVA, hv_mv_transformer 40 MVA.
_STANDARD_TRAFO = {
    (10, 0.4): "0.63 MVA 10/0.4 kV",
    (20, 0.4): "0.63 MVA 20/0.4 kV",
    (110, 10): "40 MVA 110/10 kV",
    (110, 20): "40 MVA 110/20 kV",
}

# kEUR per km (eDisGo [costs_cables]); the earthwork figures include the cable.
_CABLE_KEUR_PER_KM = {"lv": 9.0, "mv": 20.0}
_CABLE_WITH_EARTHWORK_KEUR_PER_KM = {
    ("lv", "rural"): 60.0,
    ("lv", "urban"): 100.0,
    ("mv", "rural"): 80.0,
    ("mv", "urban"): 140.0,
}
# kEUR per transformer (eDisGo [costs_transformers]), keyed by the level of
# the transformer's low-voltage side: an MV/LV station, or an HV/MV one.
_TRAFO_KEUR = {"lv": 10.0, "mv": 1000.0}

# eDisGo's split of the +/-10 % band (grid_expansion_allowed_voltage_deviations).
# An MV bus is held to its feeding busbar (eDisGo: to 1.0 pu, see
# voltage_limits); an MV/LV station's busbar to its primary side; an LV bus to
# the busbar of the station that feeds it.
_MV_MAX_V_DROP, _MV_MAX_V_RISE = 0.015, 0.05
_STATION_MAX_V_DROP, _STATION_MAX_V_RISE = 0.02, 0.015
_LV_MAX_V_DROP, _LV_MAX_V_RISE = 0.065, 0.035

# A line overloaded by less than this, of a non-standard cable on its own, gets
# one parallel cable of its own type (eDisGo's ``max_rel_overload < 2``).
_ONE_PARALLEL_CABLE_BELOW = 2.0

VOLTAGE_BANDS = ("split", "flat")
EARTHWORKS = ("rural", "urban")


@dataclass(frozen=True)
class Settings:
    """What an experiment chose; composegen resolves every field."""

    max_loading_percent: float
    voltage_band: str
    earthwork: str
    max_iterations: int

    @classmethod
    def parse(cls, spec: str) -> "Settings":
        """Read ``key=value,key=value`` as composegen writes it on the command line.

        Every key must be present: composegen fills in the defaults, so a missing
        one means the command line and composegen disagree.
        """
        pairs = dict(item.split("=", 1) for item in spec.split(",") if item)
        expected = {f for f in cls.__dataclass_fields__}
        if set(pairs) != expected:
            raise ValueError(
                f"--reinforcement must set exactly {sorted(expected)}, got "
                f"{sorted(pairs)} from {spec!r}"
            )
        settings = cls(
            max_loading_percent=float(pairs["max_loading_percent"]),
            voltage_band=pairs["voltage_band"],
            earthwork=pairs["earthwork"],
            max_iterations=int(pairs["max_iterations"]),
        )
        if settings.voltage_band not in VOLTAGE_BANDS:
            raise ValueError(f"voltage_band must be one of {VOLTAGE_BANDS}: {spec!r}")
        if settings.earthwork not in EARTHWORKS:
            raise ValueError(f"earthwork must be one of {EARTHWORKS}: {spec!r}")
        if settings.max_loading_percent <= 0 or settings.max_iterations < 1:
            raise ValueError(
                f"max_loading_percent must be > 0 and max_iterations >= 1: {spec!r}"
            )
        return settings


@dataclass(frozen=True)
class Measure:
    """One change to the net, with what caused it and what it cost."""

    time: float
    element: str  # "line" or "trafo"
    index: int
    name: str
    action: str
    reason: str
    severity: float  # relative overload, or voltage deviation in pu
    added: int  # cables or transformers installed
    std_type: str
    cost_keur: float


@dataclass(frozen=True)
class Violation:
    """One element outside its limit, as the step's load first found it.

    ``value`` is ``loading_percent`` for a line or trafo and ``vm_pu`` for a
    bus; ``limit`` is the bound it crossed.
    """

    time: float
    element: str  # "line", "trafo" or "bus"
    index: int
    value: float
    limit: float


@dataclass
class State:
    """What reinforcement carries from one step to the next."""

    measures: list[Measure] = field(default_factory=list)
    # Every violation before any measure, which the measures alone do not show:
    # one feeder split answers all the buses behind it.
    violations: list[Violation] = field(default_factory=list)
    # Earthwork is paid once per line; a second cable in the same trench is
    # only the cable (eDisGo's grid_expansion_costs).
    dug_lines: set[int] = field(default_factory=set)

    @property
    def cost_keur(self) -> float:
        """Everything spent so far."""
        return sum(m.cost_keur for m in self.measures)


class UnresolvedViolation(RuntimeError):
    """The iteration cap was reached with limits still violated."""


# ---------------------------------------------------------------------------
# Levels and standard equipment
# ---------------------------------------------------------------------------


def _level(vn_kv: float) -> str:
    if vn_kv <= _LV_KV_MAX:
        return "lv"
    return "mv" if vn_kv < _HV_KV else "hv"


def _line_level(net: pp.pandapowerNet, idx: int) -> str:
    return _level(float(net.bus.at[net.line.at[idx, "from_bus"], "vn_kv"]))


def _standard_line(net: pp.pandapowerNet, idx: int) -> str:
    vn_kv = float(net.bus.at[net.line.at[idx, "from_bus"], "vn_kv"])
    if vn_kv <= _LV_KV_MAX:
        return _STANDARD_LV_LINE
    standard = _STANDARD_MV_LINE.get(round(vn_kv))
    if standard is None:
        raise ValueError(
            f"No standard line for line {idx} at {vn_kv} kV; the catalogue has "
            f"0.4 kV and {sorted(_STANDARD_MV_LINE)} kV."
        )
    return standard


def _standard_trafo(net: pp.pandapowerNet, idx: int) -> str:
    hv = round(float(net.trafo.at[idx, "vn_hv_kv"]))
    lv_kv = float(net.trafo.at[idx, "vn_lv_kv"])
    lv: float = 0.4 if lv_kv <= _LV_KV_MAX else round(lv_kv)
    standard = _STANDARD_TRAFO.get((hv, lv))
    if standard is None:
        raise ValueError(
            f"No standard transformer for trafo {idx} ({hv}/{lv} kV); the "
            f"catalogue has {sorted(_STANDARD_TRAFO)}."
        )
    return standard


def _trafo_level(net: pp.pandapowerNet, idx: int) -> str:
    return _level(float(net.trafo.at[idx, "vn_lv_kv"]))


def _name(table: pd.DataFrame, idx: int) -> str:
    name = table.at[idx, "name"] if "name" in table.columns else None
    return str(name) if isinstance(name, str) else ""


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


def _line_cost_estimate(
    net: pp.pandapowerNet, idx: int, added: int, settings: Settings, state: State
) -> float:
    """Cost of ``added`` cables along line ``idx``, plus its trench if not dug yet."""
    level = "lv" if _line_level(net, idx) == "lv" else "mv"
    length_km = float(net.line.at[idx, "length_km"])
    cable = _CABLE_KEUR_PER_KM[level]
    cost = cable * length_km * added
    if idx not in state.dug_lines:
        with_earthwork = _CABLE_WITH_EARTHWORK_KEUR_PER_KM[(level, settings.earthwork)]
        cost += (with_earthwork - cable) * length_km
    return cost


def _line_cost(
    net: pp.pandapowerNet, idx: int, added: int, settings: Settings, state: State
) -> float:
    """Charge ``added`` cables along line ``idx``, marking its trench as dug."""
    cost = _line_cost_estimate(net, idx, added, settings, state)
    state.dug_lines.add(idx)
    return cost


def _trafo_cost(net: pp.pandapowerNet, idx: int, added: int) -> float:
    level = _trafo_level(net, idx)
    return _TRAFO_KEUR.get(level, _TRAFO_KEUR["mv"]) * added


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def overloaded(net: pp.pandapowerNet, element: str, settings: Settings) -> pd.Series:
    """Relative overload (loading over the limit) of each overloaded element."""
    table = net[element]
    if table.empty:
        return pd.Series(dtype=float)
    loading = net[f"res_{element}"]["loading_percent"]
    in_service = table["in_service"].astype(bool)
    relative = loading[in_service] / settings.max_loading_percent
    return relative[relative > 1.0].sort_values(ascending=False)


def _line_graph(net: pp.pandapowerNet) -> nx.MultiGraph:
    """Buses joined by lines and closed bus-bus switches, weighted by length."""
    return top.create_nxgraph(
        net, include_trafos=False, include_impedances=False, respect_switches=True
    )


def _lv_stations(net: pp.pandapowerNet) -> dict[int, int]:
    """LV busbar -> primary bus of every in-service MV/LV transformer."""
    trafo = net.trafo[net.trafo["in_service"].astype(bool)]
    lv_vn = net.bus.loc[trafo["lv_bus"], "vn_kv"].to_numpy()
    hv_vn = net.bus.loc[trafo["hv_bus"], "vn_kv"].to_numpy()
    mask = (lv_vn <= _LV_KV_MAX) & (hv_vn > _LV_KV_MAX)
    return dict(
        zip(
            trafo.loc[mask, "lv_bus"].astype(int), trafo.loc[mask, "hv_bus"].astype(int)
        )
    )


def _feeding_buses(net: pp.pandapowerNet, level: str) -> set[int]:
    """Where feeders of one level start: its stations' busbars and its slacks."""
    slacks = {
        int(b)
        for b in net.ext_grid.loc[net.ext_grid["in_service"].astype(bool), "bus"]
        if _level(float(net.bus.at[b, "vn_kv"])) == level
    }
    trafo = net.trafo[net.trafo["in_service"].astype(bool)]
    busbars = {
        int(lv)
        for lv, hv in zip(trafo["lv_bus"], trafo["hv_bus"])
        if _level(float(net.bus.at[lv, "vn_kv"])) == level
        and _level(float(net.bus.at[hv, "vn_kv"])) != level
    }
    return slacks | busbars


def voltage_limits(net: pp.pandapowerNet, settings: Settings) -> pd.DataFrame:
    """Allowed ``min``/``max`` voltage and ``level`` of each solved bus.

    ``level`` is ``mv``, ``station`` or ``lv``; HV buses and slack buses are
    left out, since nothing in this grid can change them.
    """
    vm = net.res_bus["vm_pu"].dropna()
    levels = net.bus.loc[vm.index, "vn_kv"].map(_level)
    slack_buses = set(net.ext_grid["bus"].astype(int))
    stations = _lv_stations(net)
    rows: dict[int, tuple[float, float, str]] = {}

    if settings.voltage_band == "flat":
        for bus, level in levels.items():
            if level != "hv" and bus not in slack_buses:
                kind = "station" if bus in stations else level
                rows[int(bus)] = (0.9, 1.1, kind)
        return pd.DataFrame.from_dict(
            rows, orient="index", columns=["min", "max", "level"]
        )

    graph = _line_graph(net)
    # eDisGo holds MV buses to an absolute band, assuming the HV/MV station
    # keeps its busbar at 1.0 pu. Here MV buses are held to their feeding
    # busbar instead, as LV buses are to their station: the same where the
    # busbar sits at 1.0, but a net without an OLTC on its HV/MV transformer
    # would otherwise blame the cables for the transformer's drop. On the
    # synthetic MV net, 25 MVA at 72 % put the busbar at 0.964 pu, and the
    # feeder rules piled up 58 parallel cables trying to fix it.
    rows.update(
        _relative_limits(
            net, graph, vm, levels, "mv", _MV_MAX_V_DROP, _MV_MAX_V_RISE, slack_buses
        )
    )
    for busbar, primary in stations.items():
        if busbar in vm.index and primary in vm.index:
            v_primary = float(vm[primary])
            rows[busbar] = (
                v_primary - _STATION_MAX_V_DROP,
                v_primary + _STATION_MAX_V_RISE,
                "station",
            )
    rows.update(
        _relative_limits(
            net, graph, vm, levels, "lv", _LV_MAX_V_DROP, _LV_MAX_V_RISE, slack_buses
        )
    )
    return pd.DataFrame.from_dict(rows, orient="index", columns=["min", "max", "level"])


def _relative_limits(
    net: pp.pandapowerNet,
    graph: nx.MultiGraph,
    vm: pd.Series,
    levels: pd.Series,
    level: str,
    max_drop: float,
    max_rise: float,
    slack_buses: set[int],
) -> dict[int, tuple[float, float, str]]:
    """Limits of one level's buses around the busbar nearest along the lines.

    That busbar is the one feeding the bus unless the net is meshed between
    stations. Buses no station or slack of the level reaches are left out.
    """
    sources = [s for s in _feeding_buses(net, level) if s in graph]
    buses = set(levels.index[levels == level]) - set(sources) - slack_buses
    if not buses or not sources:
        return {}
    _, paths = nx.multi_source_dijkstra(graph, sources, weight="weight")
    rows: dict[int, tuple[float, float, str]] = {}
    for bus in buses:
        if bus in paths and paths[bus][0] in vm.index:
            v_source = float(vm[paths[bus][0]])
            rows[int(bus)] = (v_source - max_drop, v_source + max_rise, level)
    return rows


def voltage_violations(net: pp.pandapowerNet, settings: Settings) -> pd.DataFrame:
    """Buses outside their limits, with ``deviation`` (pu beyond the limit)."""
    limits = voltage_limits(net, settings)
    if limits.empty:
        return limits.assign(deviation=pd.Series(dtype=float))
    vm = net.res_bus.loc[limits.index, "vm_pu"]
    deviation = pd.concat([limits["min"] - vm, vm - limits["max"]], axis=1).max(axis=1)
    limits = limits.assign(deviation=deviation)
    return limits[limits["deviation"] > 1e-9].sort_values("deviation", ascending=False)


# ---------------------------------------------------------------------------
# Measures against overloading
# ---------------------------------------------------------------------------


def _reinforce_trafo_overload(
    net: pp.pandapowerNet,
    idx: int,
    relative: float,
    settings: Settings,
    time: float,
) -> Measure:
    """One more transformer of the same kind if that is enough, else standard ones.

    eDisGo installs one parallel transformer when the largest existing one
    covers the missing power; otherwise it replaces the station's transformers
    with as many standard transformers as the load needs.
    """
    parallel = int(net.trafo.at[idx, "parallel"])
    sn = float(net.trafo.at[idx, "sn_mva"])
    missing_mva = (relative - 1.0) * sn * parallel
    if sn >= missing_mva:
        net.trafo.at[idx, "parallel"] = parallel + 1
        action, added = "parallel_same_type", 1
        std_type = str(net.trafo.at[idx, "std_type"])
    else:
        std_type = _standard_trafo(net, idx)
        needed_mva = relative * sn * parallel
        pp.change_std_type(net, idx, std_type, element="trafo")
        added = math.ceil(needed_mva / float(net.trafo.at[idx, "sn_mva"]))
        net.trafo.at[idx, "parallel"] = added
        action = "replace_by_standard"
    return Measure(
        time=time,
        element="trafo",
        index=int(idx),
        name=_name(net.trafo, idx),
        action=action,
        reason="overload",
        severity=float(relative),
        added=added,
        std_type=std_type,
        cost_keur=_trafo_cost(net, idx, added),
    )


def _reinforce_line_overload(
    net: pp.pandapowerNet,
    idx: int,
    relative: float,
    settings: Settings,
    state: State,
    time: float,
) -> Measure:
    """eDisGo's three cases for an overloaded line.

    A standard line gets as many parallel standard lines as the overload needs.
    A single non-standard cable overloaded by less than a factor of two gets
    one parallel cable of its own type. Anything else is replaced by as many
    parallel standard lines as carry its current load.
    """
    standard = _standard_line(net, idx)
    parallel = int(net.line.at[idx, "parallel"])
    if net.line.at[idx, "std_type"] == standard:
        new_parallel = math.ceil(relative * parallel)
        action = "parallel_standard"
    elif (
        parallel == 1
        and net.line.at[idx, "type"] == "cs"
        and relative < _ONE_PARALLEL_CABLE_BELOW
    ):
        new_parallel = 2
        action = "parallel_same_type"
    else:
        capacity_ka = float(net.line.at[idx, "max_i_ka"]) * parallel
        pp.change_std_type(net, idx, standard, element="line")
        new_parallel = math.ceil(
            capacity_ka * relative / float(net.line.at[idx, "max_i_ka"])
        )
        parallel = 0  # the old lines are gone; every new one is installed
        action = "replace_by_standard"
    net.line.at[idx, "parallel"] = new_parallel
    added = new_parallel - parallel
    return Measure(
        time=time,
        element="line",
        index=int(idx),
        name=_name(net.line, idx),
        action=action,
        reason="overload",
        severity=float(relative),
        added=added,
        std_type=str(net.line.at[idx, "std_type"]),
        cost_keur=_line_cost(net, idx, added, settings, state),
    )


def _reinforce_overloading(
    net: pp.pandapowerNet, settings: Settings, state: State, time: float
) -> list[Measure]:
    measures = [
        _reinforce_trafo_overload(net, int(idx), rel, settings, time)
        for idx, rel in overloaded(net, "trafo", settings).items()
    ]
    measures += [
        _reinforce_line_overload(net, int(idx), rel, settings, state, time)
        for idx, rel in overloaded(net, "line", settings).items()
    ]
    return measures


def _has_overloading(net: pp.pandapowerNet, settings: Settings) -> bool:
    return bool(
        len(overloaded(net, "trafo", settings))
        or len(overloaded(net, "line", settings))
    )


# ---------------------------------------------------------------------------
# Measures against voltage issues
# ---------------------------------------------------------------------------


def _reinforce_station_voltage(
    net: pp.pandapowerNet, busbar: int, deviation: float, time: float
) -> Measure:
    """One more standard transformer at an MV/LV station whose busbar is off."""
    trafos = net.trafo.index[
        (net.trafo["lv_bus"] == busbar) & net.trafo["in_service"].astype(bool)
    ]
    idx = int(trafos[0])
    std_type = _standard_trafo(net, idx)
    if net.trafo.at[idx, "std_type"] == std_type:
        net.trafo.at[idx, "parallel"] = int(net.trafo.at[idx, "parallel"]) + 1
    else:
        # A parallel row only multiplies its own type, so a non-standard
        # station gets a separate standard transformer beside it.
        idx = int(
            pp.create_transformer(
                net,
                int(net.trafo.at[idx, "hv_bus"]),
                busbar,
                std_type,
                name=f"{_name(net.trafo, idx)} reinforced".strip(),
            )
        )
    return Measure(
        time=time,
        element="trafo",
        index=idx,
        name=_name(net.trafo, idx),
        action="parallel_standard",
        reason="voltage",
        severity=float(deviation),
        added=1,
        std_type=std_type,
        cost_keur=_trafo_cost(net, idx, 1),
    )


def _edge_line(graph: nx.MultiGraph, u: int, v: int) -> int:
    """The line joining two adjacent buses on a path (a switch has no cost)."""
    for key in graph.get_edge_data(u, v):
        if key[0] == "line":
            return int(key[1])
    raise ValueError(f"Buses {u} and {v} are joined only by a switch.")


def _is_disconnect_point(net: pp.pandapowerNet, bus: int, level: str) -> bool:
    """Whether a feeder may be split at this bus.

    eDisGo splits MV feeders only at LV stations, which have the switchgear.
    GridLock's MV nets usually stand in for a station with a load, so either
    counts. eDisGo also refuses buses inside buildings in LV; pandapower has
    no such notion, so every LV bus counts.
    """
    if level != "mv":
        return True
    return bool((net.trafo["hv_bus"] == bus).any() or (net.load["bus"] == bus).any())


def _split_feeder(
    net: pp.pandapowerNet,
    station: int,
    path: list[int],
    lengths: dict[int, float],
    node_2_3: int,
    deviation: float,
    settings: Settings,
    state: State,
    time: float,
) -> Measure:
    """Reconnect the feeder at ``node_2_3`` straight to the station.

    The line into ``node_2_3`` is detached from its predecessor, attached to
    the station busbar, given the path's length and the standard type.
    """
    predecessor = path[path.index(node_2_3) - 1]
    idx = _edge_line(_line_graph(net), predecessor, node_2_3)
    end = "from_bus" if net.line.at[idx, "from_bus"] == predecessor else "to_bus"
    net.line.at[idx, end] = station
    net.line.at[idx, "length_km"] = lengths[node_2_3]
    std_type = _standard_line(net, idx)
    pp.change_std_type(net, idx, std_type, element="line")
    net.line.at[idx, "parallel"] = 1
    state.dug_lines.discard(idx)  # a new route needs a new trench
    return Measure(
        time=time,
        element="line",
        index=idx,
        name=_name(net.line, idx),
        action="split_feeder",
        reason="voltage",
        severity=float(deviation),
        added=1,
        std_type=std_type,
        cost_keur=_line_cost(net, idx, 1, settings, state),
    )


def _reinforce_path(
    net: pp.pandapowerNet,
    path: list[int],
    critical: int,
    deviation: float,
    settings: Settings,
    state: State,
    time: float,
) -> list[Measure]:
    """Reinforce lines on the path, best voltage gain per kEUR first.

    The voltage drop along the path is estimated as the sum of R * I. Lines
    are reinforced one at a time until that sum shrinks by half the share of
    the drop that is out of limits; what is left is taken up by the next
    iteration, as in eDisGo, which found the full share over-reinforced.
    A line becomes one standard line if that lowers its resistance, otherwise
    it gets one more parallel line of its own type.
    """
    graph = _line_graph(net)
    lines = [_edge_line(graph, u, v) for u, v in zip(path[:-1], path[1:])]
    current = net.res_line.loc[lines, "i_ka"].abs().fillna(0.0)
    if float(current.sum()) == 0.0:
        current[:] = 1.0
    vm = net.res_bus["vm_pu"]
    v_diff = abs(float(vm[critical]) - float(vm[path[0]]))
    share = min(1.0, deviation / v_diff) if v_diff > 0 else 1.0

    def resistance(idx: int) -> float:
        line = net.line.loc[idx]
        return float(line.r_ohm_per_km * line.length_km / line.parallel)

    target = sum(resistance(i) * current[i] for i in lines) * (1 - share / 2)
    std_r = {
        i: float(pp.load_std_type(net, _standard_line(net, i), "line")["r_ohm_per_km"])
        * float(net.line.at[i, "length_km"])
        for i in lines
    }
    measures: list[Measure] = []
    while sum(resistance(i) * current[i] for i in lines) > target:
        best: tuple[float, int, bool] | None = None
        for i in lines:
            r_now = resistance(i)
            replace = net.line.at[i, "std_type"] != _standard_line(net, i) and (
                std_r[i] < r_now
            )
            # One more of n parallel lines takes R to R * n / (n + 1).
            parallel = int(net.line.at[i, "parallel"])
            gain = r_now - std_r[i] if replace else r_now / (parallel + 1)
            cost = _line_cost_estimate(net, i, 1, settings, state)
            score = gain * float(current[i]) / max(cost, 1e-9)
            if score > 0 and (best is None or score > best[0]):
                best = (score, i, replace)
        if best is None:
            break
        _, idx, replace = best
        if replace:
            pp.change_std_type(net, idx, _standard_line(net, idx), element="line")
            net.line.at[idx, "parallel"] = 1
            action = "replace_by_standard"
        else:
            net.line.at[idx, "parallel"] = int(net.line.at[idx, "parallel"]) + 1
            action = "parallel_same_type"
        measures.append(
            Measure(
                time=time,
                element="line",
                index=idx,
                name=_name(net.line, idx),
                action=action,
                reason="voltage",
                severity=float(deviation),
                added=1,
                std_type=str(net.line.at[idx, "std_type"]),
                cost_keur=_line_cost(net, idx, 1, settings, state),
            )
        )
    return measures


def _reinforce_feeders(
    net: pp.pandapowerNet,
    critical: pd.DataFrame,
    level: str,
    settings: Settings,
    state: State,
    time: float,
) -> list[Measure]:
    """eDisGo's feeder rule, once per feeder with a voltage issue.

    The critical bus farthest from the station is taken; the first bus on its
    path at two thirds of that distance is reconnected straight to the
    station. If that bus already hangs off the station, the path is
    reinforced instead.
    """
    graph = _line_graph(net)
    sources = [s for s in _feeding_buses(net, level) if s in graph]
    if not sources:
        raise UnresolvedViolation(
            f"Voltage issue at {level.upper()} buses {list(critical.index)} but no "
            f"station or slack feeds that level."
        )
    lengths_from, paths = nx.multi_source_dijkstra(graph, sources, weight="weight")

    feeders: dict[tuple[int, int], int] = {}
    for bus in critical.index:
        path = paths.get(bus)
        if path is None or len(path) < 2:
            continue
        key = (path[0], path[1])
        farthest = feeders.get(key)
        if farthest is None or lengths_from[bus] > lengths_from[farthest]:
            feeders[key] = int(bus)

    measures: list[Measure] = []
    for (station, head), bus in feeders.items():
        path = paths[bus]
        lengths = {n: float(lengths_from[n]) for n in path}
        node_2_3 = next(n for n in path if lengths[n] >= lengths[bus] * 2 / 3)
        while not _is_disconnect_point(net, node_2_3, level):
            position = path.index(node_2_3)
            if position + 1 == len(path):
                break
            node_2_3 = path[position + 1]
        deviation = float(critical.at[bus, "deviation"])
        if node_2_3 not in (station, head):
            measures.append(
                _split_feeder(
                    net,
                    station,
                    path,
                    lengths,
                    node_2_3,
                    deviation,
                    settings,
                    state,
                    time,
                )
            )
        else:
            measures += _reinforce_path(
                net, path, bus, deviation, settings, state, time
            )
    return measures


def _reinforce_voltage(
    net: pp.pandapowerNet, level: str, settings: Settings, state: State, time: float
) -> list[Measure]:
    violations = voltage_violations(net, settings)
    critical = violations[violations["level"] == level]
    if critical.empty:
        return []
    if level == "station":
        return [
            _reinforce_station_voltage(net, int(bus), float(row.deviation), time)
            for bus, row in critical.iterrows()
        ]
    return _reinforce_feeders(net, critical, level, settings, state, time)


def _has_voltage_issue(net: pp.pandapowerNet, level: str, settings: Settings) -> bool:
    violations = voltage_violations(net, settings)
    return bool((violations["level"] == level).any())


# ---------------------------------------------------------------------------
# The sequence
# ---------------------------------------------------------------------------


def _describe(net: pp.pandapowerNet, settings: Settings) -> str:
    parts = [
        f"{element} {list(overloaded(net, element, settings).round(3).items())}"
        for element in ("trafo", "line")
    ]
    violations = voltage_violations(net, settings)
    parts.append(f"buses {list(violations['deviation'].round(4).items())}")
    return "; ".join(parts)


def _loop(
    net: pp.pandapowerNet,
    settings: Settings,
    time: float,
    what: str,
    has_issue: Callable[[], bool],
    apply: Callable[[], list[Measure]],
    solve: Callable[[], None],
) -> list[Measure]:
    """Apply one kind of measure and re-solve until it is no longer needed."""
    measures: list[Measure] = []
    for _ in range(settings.max_iterations):
        if not has_issue():
            return measures
        applied = apply()
        if not applied:
            break
        measures += applied
        solve()
    if has_issue():
        raise UnresolvedViolation(
            f"{what} not resolved at simulation time {time}s after "
            f"{settings.max_iterations} iterations ({len(measures)} measures "
            f"applied): {_describe(net, settings)}"
        )
    return measures


def violations(
    net: pp.pandapowerNet, settings: Settings, time: float
) -> list[Violation]:
    """Every line, transformer and bus outside its limits in the solved net."""
    found = [
        Violation(
            time=time,
            element=element,
            index=int(idx),
            value=float(relative * settings.max_loading_percent),
            limit=float(settings.max_loading_percent),
        )
        for element in ("line", "trafo")
        for idx, relative in overloaded(net, element, settings).items()
    ]
    for bus, row in voltage_violations(net, settings).iterrows():
        vm = float(net.res_bus.at[bus, "vm_pu"])
        limit = row["min"] if vm < row["min"] else row["max"]
        found.append(Violation(time, "bus", int(bus), vm, float(limit)))
    return found


def needs_reinforcement(net: pp.pandapowerNet, settings: Settings) -> bool:
    """Whether any limit is violated in the solved net."""
    return (
        _has_overloading(net, settings) or not voltage_violations(net, settings).empty
    )


def reinforce(
    net: pp.pandapowerNet,
    settings: Settings,
    state: State,
    solve: Callable[[], None],
    time: float,
) -> list[Measure]:
    """Reinforce a solved net until every limit holds; return what was done.

    The order is eDisGo's: overloading first, then voltage issues in MV, at the
    MV/LV station busbars and in LV, then overloading once more, since a split
    feeder can overload the line it now runs on. ``solve`` must re-run the
    power flow; the net is left solved.
    """
    if not needs_reinforcement(net, settings):
        return []
    state.violations += violations(net, settings, time)

    def overload_loop() -> list[Measure]:
        return _loop(
            net,
            settings,
            time,
            "Overloading",
            lambda: _has_overloading(net, settings),
            lambda: _reinforce_overloading(net, settings, state, time),
            solve,
        )

    measures = overload_loop()
    for level in ("mv", "station", "lv"):
        measures += _loop(
            net,
            settings,
            time,
            f"{level.upper()} voltage issue",
            partial(_has_voltage_issue, net, level, settings),
            partial(_reinforce_voltage, net, level, settings, state, time),
            solve,
        )
    measures += overload_loop()
    state.measures += measures
    return measures


def as_records(items: list[Measure] | list[Violation]) -> list[dict[str, Any]]:
    """Measures or violations as plain dicts, for the report document."""
    return [item.__dict__.copy() for item in items]
