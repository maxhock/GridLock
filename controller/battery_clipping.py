from __future__ import annotations


def clip_battery_power_to_soc(
    requested_battery_power_w: float,
    current_soc: float,
    dt_seconds: float,
    max_power_w: float,
    capacity_kwh: float,
    soc_min: float = 0.0,
    soc_max: float = 1.0,
    soc_epsilon: float = 1e-4,
) -> tuple[float, float]:
    """Clip a battery power command using the latest reported SOC."""

    effective_soc = min(max(float(current_soc), soc_min), soc_max)

    if max_power_w <= 0.0:
        clipped_power_w = 0.0
    else:
        clipped_power_w = max(
            -float(max_power_w),
            min(float(requested_battery_power_w), float(max_power_w)),
        )

    if effective_soc <= soc_min + soc_epsilon and clipped_power_w < 0.0:
        clipped_power_w = 0.0
    if effective_soc >= soc_max - soc_epsilon and clipped_power_w > 0.0:
        clipped_power_w = 0.0

    capacity_wh = max(float(capacity_kwh), 0.0) * 1000.0
    if capacity_wh <= 0.0 or dt_seconds <= 0.0:
        return float(clipped_power_w), float(effective_soc)

    p_soc_min = -(effective_soc - soc_min) * capacity_wh * 3600.0 / dt_seconds
    p_soc_max = (soc_max - effective_soc) * capacity_wh * 3600.0 / dt_seconds
    clipped_power_w = max(p_soc_min, min(clipped_power_w, p_soc_max))

    next_soc = effective_soc + (
        clipped_power_w * dt_seconds / (3600.0 * capacity_wh)
    )
    next_soc = min(max(next_soc, soc_min), soc_max)

    return float(clipped_power_w), float(next_soc)
