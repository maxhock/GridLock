"""Resolve an experiment's `start_time` / `end_time` / `duration` into one window.

Both times are absolute. A number is seconds after `RUN_EPOCH`, a string an
ISO 8601 wall-clock time. `duration` is the run length in seconds. Any two of
the three fix the third:

* `duration` alone runs from 0 to `duration`;
* `end_time` alone runs from 0 to `end_time`;
* `start_time` + `end_time` gives the duration as their difference;
* `start_time` + `duration` or `end_time` + `duration` gives the missing time.

All three together are refused rather than cross-checked: two of them always
determine the third, so a third value can only agree or silently contradict.

This module needs nothing but pandas on purpose: the parity tools load it by
path (`tools/parity_common.py`) so they size a run exactly as composegen does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

RUN_EPOCH = pd.Timestamp("2023-01-01T00:00:00")

# CST and the federates parse exactly this form; any other ISO 8601 spelling
# (a bare date, an offset) used to be passed through and kill every federate.
_WALLCLOCK_FORMAT = "%Y-%m-%dT%H:%M:%S"


@dataclass(frozen=True)
class RunWindow:
    """The wall-clock span of one run."""

    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def duration_s(self) -> int:
        """Run length in whole seconds; HELICS time runs from 0 to this."""
        return int((self.end - self.start).total_seconds())

    @property
    def start_iso(self) -> str:
        return self.start.strftime(_WALLCLOCK_FORMAT)

    @property
    def end_iso(self) -> str:
        return self.end.strftime(_WALLCLOCK_FORMAT)


def _whole_seconds(value: float, key: str) -> pd.Timedelta:
    if not float(value).is_integer():
        raise ValueError(
            f"[General] '{key}' must be a whole number of seconds, got {value!r}."
        )
    return pd.Timedelta(seconds=int(value))


def _parse_iso(value: str, key: str) -> datetime:
    # datetime.fromisoformat rather than pd.Timestamp: pandas also accepts ""
    # (NaT) and keywords like "now", which would surface as a baffling
    # whole-second error, or worse, as a run window that moves every call.
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"[General] '{key}' is not an ISO 8601 time: {value!r}."
        ) from exc


def _as_timestamp(value: Any, key: str) -> pd.Timestamp:
    """An absolute time: seconds after `RUN_EPOCH`, or an ISO 8601 time.

    The time may come as a string or, when written unquoted, as the ``date`` /
    ``datetime`` that ``yaml.safe_load`` turns ``2023-01-02`` or
    ``2023-01-01T00:30:00`` into.
    """
    if isinstance(value, bool):
        raise ValueError(f"[General] '{key}' must be a time, got {value!r}.")

    if isinstance(value, (int, float)):
        return RUN_EPOCH + _whole_seconds(value, key)

    if isinstance(value, str):
        value = _parse_iso(value, key)

    if not isinstance(value, date):
        raise ValueError(
            f"[General] '{key}' must be seconds or an ISO 8601 time, got {value!r}."
        )

    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        raise ValueError(
            f"[General] '{key}' carries a UTC offset ({value!r}); give a "
            f"local wall-clock time without one."
        )
    if ts != ts.floor("s"):
        raise ValueError(
            f"[General] '{key}' must fall on a whole second, got {value!r}."
        )
    return ts


def _as_duration(value: Any) -> pd.Timedelta:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"[General] 'duration' must be a number of seconds, got {value!r}."
        )
    return _whole_seconds(value, "duration")


def resolve_run_window(general: Mapping[str, Any]) -> RunWindow:
    """Turn the `general:` block's time keys into one validated window."""
    start_raw = general.get("start_time")
    end_raw = general.get("end_time")
    duration_raw = general.get("duration")

    given = [
        key
        for key, value in (
            ("start_time", start_raw),
            ("end_time", end_raw),
            ("duration", duration_raw),
        )
        if value is not None
    ]

    if len(given) == 3:
        raise ValueError(
            "[General] 'start_time', 'end_time' and 'duration' are all set; give "
            "at most two of them - any two determine the third."
        )
    if end_raw is None and duration_raw is None:
        raise ValueError("[General] Give 'end_time' or 'duration' (or both).")

    if duration_raw is None:
        start = (
            RUN_EPOCH if start_raw is None else _as_timestamp(start_raw, "start_time")
        )
        end = _as_timestamp(end_raw, "end_time")
    elif end_raw is None:
        start = (
            RUN_EPOCH if start_raw is None else _as_timestamp(start_raw, "start_time")
        )
        end = start + _as_duration(duration_raw)
    else:
        end = _as_timestamp(end_raw, "end_time")
        start = end - _as_duration(duration_raw)

    if end <= start:
        raise ValueError(
            f"[General] The run must end after it starts: start {start}, end {end} "
            f"(given: {', '.join(given)})."
        )

    return RunWindow(start=start, end=end)
