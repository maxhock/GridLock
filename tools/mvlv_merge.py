"""Fuse an MV net and its LV nets into ONE pandapower net.

This is the reference the MV/LV coupling is checked against
(`verify_mvlv_parity.py`): with constant loads the coupled co-simulation settles
on the fixed point of the merged net, because each LV slack is exactly "the MV
load bus, seen through the LV net". Pure pandapower, so it runs anywhere the
nets can be loaded; the unit tests in `test_grid_tools.py` use it too.

The fusion keeps the physics identical to what the federates solve:

* the LV `ext_grid` is dropped and its bus fused into the MV load's bus
  (`pp.toolbox.fuse_buses`, a zero-impedance tie), so the LV transformer's HV
  side hangs directly on the MV bus;
* the MV load the LV grid stands in for is removed, since its demand is now the
  LV part itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pandapower as pp
import pandapower.toolbox as tb

from grid_contract import check_attach


@dataclass(frozen=True)
class LvPart:
    """Where one LV net ended up inside the merged net."""

    mv_load_idx: int
    fused_bus: int
    load_ids: dict[int, int]  # LV load index -> merged load index
    bus_ids: dict[int, int]  # LV bus index -> merged bus index
    trafo_ids: tuple[int, ...]
    line_ids: tuple[int, ...]


@dataclass
class MergedNet:
    """The merged net plus the bookkeeping needed to read results out of it."""

    net: pp.pandapowerNet
    mv_load_ids: list[int]  # MV loads still in the net (not replaced by an LV net)
    parts: dict[int, LvPart] = field(default_factory=dict)  # by MV load index

    def mv_bus_of_load(self, mv_load_idx: int) -> int:
        """Merged bus of an MV load, also for one an LV net replaced."""
        if mv_load_idx in self.parts:
            return self.parts[mv_load_idx].fused_bus
        return int(self.net.load.at[mv_load_idx, "bus"])

    def lv_bus_of_load(self, mv_load_idx: int, lv_load_idx: int) -> int:
        """Merged bus of LV load `lv_load_idx` of the grid on `mv_load_idx`."""
        merged_load = self.parts[mv_load_idx].load_ids[lv_load_idx]
        return int(self.net.load.at[merged_load, "bus"])


def _align_bool_columns(net: pp.pandapowerNet, other: pp.pandapowerNet) -> None:
    """Give `net` every boolean column `other` has that it lacks, as False.

    Nets written by different pandapower versions or tools carry different
    columns (the Kerber workbooks have no `trafo.oltc`), and `merge_nets` refuses
    a NaN in a boolean column rather than filling it. False is pandapower's own
    default for these columns.
    """
    for element in other.keys():
        table, other_table = net.get(element), other.get(element)
        if not hasattr(table, "columns") or not hasattr(other_table, "columns"):
            continue
        for column in other_table.columns:
            if column not in table.columns and other_table[column].dtype == bool:
                table[column] = False


def _reindexed(lookup: Mapping[str, Mapping[int, int]], element: str, idx: int) -> int:
    """New index of a net2 element.

    `merge_nets` keeps an index unless it collides with one already in net1, and
    its lookup lists only the elements it had to move, so absent means unchanged.
    """
    return int(lookup.get(element, {}).get(idx, idx))


def _attach(merged: MergedNet, mv_load_idx: int, lv_net: pp.pandapowerNet) -> None:
    """Merge one LV net into `merged` and fuse its slack onto the MV load's bus."""
    net = merged.net
    check_attach(net, mv_load_idx, lv_net)
    mv_bus = int(net.load.at[mv_load_idx, "bus"])
    lv_slack_bus = int(lv_net.ext_grid.bus.iloc[0])

    lv_net = pp.copy.deepcopy(lv_net)
    _align_bool_columns(lv_net, net)
    _align_bool_columns(net, lv_net)
    net, lookup = pp.merge_nets(
        net,
        lv_net,
        validate=False,
        merge_results=False,
        return_net2_reindex_lookup=True,
    )
    merged.net = net

    def moved(element: str, indices: Sequence[int]) -> list[int]:
        return [_reindexed(lookup, element, int(i)) for i in indices]

    net.ext_grid.drop(moved("ext_grid", list(lv_net.ext_grid.index)), inplace=True)
    slack_bus = _reindexed(lookup, "bus", lv_slack_bus)
    tb.fuse_buses(net, mv_bus, slack_bus, drop=True)
    net.load.drop(mv_load_idx, inplace=True)
    merged.mv_load_ids.remove(mv_load_idx)

    merged.parts[mv_load_idx] = LvPart(
        mv_load_idx=mv_load_idx,
        fused_bus=mv_bus,
        load_ids={
            int(i): _reindexed(lookup, "load", int(i)) for i in lv_net.load.index
        },
        bus_ids={
            int(i): (
                mv_bus if int(i) == lv_slack_bus else _reindexed(lookup, "bus", int(i))
            )
            for i in lv_net.bus.index
        },
        trafo_ids=tuple(moved("trafo", list(lv_net.trafo.index))),
        line_ids=tuple(moved("line", list(lv_net.line.index))),
    )


def build_merged_net(
    mv_net: pp.pandapowerNet, attachments: Mapping[int, pp.pandapowerNet]
) -> MergedNet:
    """Fuse each LV net in `attachments` (by MV load index) into a copy of `mv_net`.

    The inputs are left untouched. Loads, sgens and scaling are the caller's
    business: the federates zero them at import and then drive them, so the
    caller sanitizes the nets first and writes the powers afterwards.
    """
    merged = MergedNet(
        net=pp.copy.deepcopy(mv_net), mv_load_ids=[int(i) for i in mv_net.load.index]
    )
    for mv_load_idx, lv_net in sorted(attachments.items()):
        if mv_load_idx not in merged.mv_load_ids:
            raise ValueError(f"MV load {mv_load_idx} is not available to attach to")
        _attach(merged, mv_load_idx, lv_net)
    return merged


def power_into_part(merged: MergedNet, mv_load_idx: int) -> tuple[float, float]:
    """(P in W, Q in VAr) the MV bus delivers into one fused LV part.

    Read off the part's own branches at the fused bus after `runpp`: what flows
    into the LV transformer(s) and lines is the sum of the LV loads plus every
    loss inside the part, exactly what the LV federate's `res_ext_grid`
    publishes. It is solved together with the MV net, so unlike re-solving the
    LV net alone at the co-simulated voltage it does not borrow the answer under
    test.
    """
    net, part = merged.net, merged.parts[mv_load_idx]
    bus = part.fused_bus
    p_mw = q_mvar = 0.0
    for idx in part.trafo_ids:
        if net.trafo.at[idx, "hv_bus"] == bus:
            p_mw += float(net.res_trafo.at[idx, "p_hv_mw"])
            q_mvar += float(net.res_trafo.at[idx, "q_hv_mvar"])
    for idx in part.line_ids:
        if net.line.at[idx, "from_bus"] == bus:
            p_mw += float(net.res_line.at[idx, "p_from_mw"])
            q_mvar += float(net.res_line.at[idx, "q_from_mvar"])
        if net.line.at[idx, "to_bus"] == bus:
            p_mw += float(net.res_line.at[idx, "p_to_mw"])
            q_mvar += float(net.res_line.at[idx, "q_to_mvar"])
    return p_mw * 1e6, q_mvar * 1e6
