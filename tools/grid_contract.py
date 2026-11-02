"""The MV/LV coupling contract, as a check the grid-generator scripts can run.

An LV grid federate attached to an MV load is only meaningful if the two nets
agree on the voltage base: voltage is exchanged between the federates in per
unit, so a 10 kV LV slack under a 20 kV MV load bus would silently scale every
LV voltage by two. The LV net must also have exactly one `ext_grid`, since that
is the one bus the MV side drives.

composegen enforces the same contract at generation time
(`composegen/load.py:_check_coupling`), but it runs in its own image with no
access to `tools/` and no pandapower, so it carries its own copy of this check.
The duplication is deliberate, not an import: the two run in different images
with no shared code. Keep the two in step, messages and the 1e-6 relative
tolerance included.
"""

from __future__ import annotations

import math

import pandapower as pp


class ContractViolation(ValueError):
    """An LV net cannot be attached to the given MV load."""


def _lv_slack_vn_kv(lv_net: pp.pandapowerNet) -> float:
    """Nominal voltage of the single LV slack bus, or raise."""
    if len(lv_net.ext_grid) != 1:
        raise ContractViolation(
            f"LV net must have exactly one ext_grid, found {len(lv_net.ext_grid)}"
        )
    slack_bus = lv_net.ext_grid.bus.iloc[0]
    return float(lv_net.bus.at[slack_bus, "vn_kv"])


def check_attach(
    mv_net: pp.pandapowerNet, mv_load_idx: int, lv_net: pp.pandapowerNet
) -> None:
    """Raise unless `lv_net` can hang off MV load `mv_load_idx`."""
    if mv_load_idx not in mv_net.load.index:
        raise ContractViolation(f"MV net has no load with index {mv_load_idx}")
    mv_vn_kv = float(mv_net.bus.at[mv_net.load.at[mv_load_idx, "bus"], "vn_kv"])
    lv_vn_kv = _lv_slack_vn_kv(lv_net)
    if not math.isclose(mv_vn_kv, lv_vn_kv, rel_tol=1e-6):
        raise ContractViolation(
            f"Load {mv_load_idx} of the MV net sits on a {mv_vn_kv} kV bus but "
            f"the ext_grid bus of the LV net is {lv_vn_kv} kV; the bases must "
            f"match"
        )


def check_attach_all(mv_net: pp.pandapowerNet, lv_net: pp.pandapowerNet) -> int:
    """Check `lv_net` against every MV load; return how many were checked."""
    for load_idx in mv_net.load.index:
        check_attach(mv_net, int(load_idx), lv_net)
    return len(mv_net.load)
