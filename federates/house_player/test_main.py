"""Unit tests for the load-player federate's pure helpers."""

import pandas as pd
import pytest

from src.load_player import load_timeseries, lookup_power


def _write_csv(tmp_path, rows: str) -> str:
    path = tmp_path / "profile.csv"
    path.write_text(rows)
    return str(path)


def test_load_timeseries_requires_timestamp_column(tmp_path) -> None:
    path = _write_csv(tmp_path, "base_load\n100\n")

    with pytest.raises(ValueError, match="timestamp"):
        load_timeseries(path)


def test_load_timeseries_requires_base_load_column(tmp_path) -> None:
    path = _write_csv(tmp_path, "timestamp\n0\n")

    with pytest.raises(ValueError, match="base_load"):
        load_timeseries(path)


def test_load_timeseries_defaults_reactive_power_to_zero(tmp_path) -> None:
    path = _write_csv(tmp_path, "timestamp,base_load\n0,100\n3600,200\n")

    df = load_timeseries(path)

    assert (df["reactive_power"] == 0.0).all()


def test_load_timeseries_keeps_reactive_power_when_present(tmp_path) -> None:
    path = _write_csv(
        tmp_path, "timestamp,base_load,reactive_power\n0,100,10\n3600,200,20\n"
    )

    df = load_timeseries(path)

    assert list(df["reactive_power"]) == [10.0, 20.0]


def test_load_timeseries_sorts_by_timestamp(tmp_path) -> None:
    path = _write_csv(tmp_path, "timestamp,base_load\n3600,200\n0,100\n")

    df = load_timeseries(path)

    assert list(df["timestamp"]) == [0, 3600]
    assert list(df["base_load"]) == [100.0, 200.0]


def test_load_timeseries_converts_iso_timestamps(tmp_path) -> None:
    path = _write_csv(
        tmp_path,
        "timestamp,base_load\n2024-01-01T01:00:00,200\n2024-01-01T00:00:00,100\n",
    )

    df = load_timeseries(path)

    assert list(df["timestamp"]) == [0, 3600]


def test_load_timeseries_normalizes_epoch_offset(tmp_path) -> None:
    # Two hourly samples starting at a real Unix epoch timestamp.
    epoch_start = 1_700_000_000
    path = _write_csv(
        tmp_path,
        f"timestamp,base_load\n{epoch_start},100\n{epoch_start + 3600},200\n",
    )

    df = load_timeseries(path)

    assert list(df["timestamp"]) == [0, 3600]


def test_load_timeseries_leaves_small_relative_timestamps_untouched(tmp_path) -> None:
    path = _write_csv(tmp_path, "timestamp,base_load\n0,100\n3600,200\n7200,300\n")

    df = load_timeseries(path)

    assert list(df["timestamp"]) == [0, 3600, 7200]


def _series() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [0, 3600, 7200],
            "base_load": [100.0, 200.0, 300.0],
            "reactive_power": [10.0, 20.0, 30.0],
        }
    )


def test_lookup_power_before_first_sample_holds_first_row() -> None:
    p, q = lookup_power(_series(), -100.0)

    assert (p, q) == (100.0, 10.0)


def test_lookup_power_exact_match() -> None:
    p, q = lookup_power(_series(), 3600.0)

    assert (p, q) == (200.0, 20.0)


def test_lookup_power_holds_previous_sample_between_steps() -> None:
    p, q = lookup_power(_series(), 5000.0)

    assert (p, q) == (200.0, 20.0)


def test_lookup_power_after_last_sample_holds_last_row() -> None:
    p, q = lookup_power(_series(), 100_000.0)

    assert (p, q) == (300.0, 30.0)
