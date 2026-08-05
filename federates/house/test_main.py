"""Unit tests for the house federate's exogenous-data alignment helpers."""

import pandas as pd
import pytest

from house.exogenous_data import (
    _infer_source_dt_seconds,
    _write_csv_atomic,
    prepare_aligned_timeseries,
)


def _write_csv(tmp_path, name: str, rows: str) -> str:
    path = tmp_path / name
    path.write_text(rows)
    return str(path)


def test_infer_source_dt_seconds_from_hourly_gaps() -> None:
    timestamps = pd.to_datetime(
        pd.Series(["2024-01-01 00:00:00", "2024-01-01 01:00:00", "2024-01-01 02:00:00"])
    )

    assert _infer_source_dt_seconds(timestamps) == 3600


def test_infer_source_dt_seconds_raises_with_fewer_than_two_timestamps() -> None:
    timestamps = pd.to_datetime(pd.Series(["2024-01-01 00:00:00"]))

    with pytest.raises(ValueError, match="fewer than two"):
        _infer_source_dt_seconds(timestamps)


def test_prepare_aligned_timeseries_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        prepare_aligned_timeseries("/nonexistent/dataset.csv", 3600)


def test_prepare_aligned_timeseries_rejects_non_positive_target_step(tmp_path) -> None:
    path = _write_csv(
        tmp_path,
        "hourly.csv",
        "timestamp,base_load\n" "2024-01-01 00:00:00,100\n" "2024-01-01 01:00:00,200\n",
    )

    with pytest.raises(ValueError, match="target_dt_seconds"):
        prepare_aligned_timeseries(path, 0)


def test_prepare_aligned_timeseries_requires_timestamp_column(tmp_path) -> None:
    path = _write_csv(tmp_path, "no_timestamp.csv", "foo,base_load\n1,100\n2,200\n")

    with pytest.raises(ValueError, match="timestamp"):
        prepare_aligned_timeseries(path, 3600)


def test_prepare_aligned_timeseries_passes_through_on_matching_step(tmp_path) -> None:
    path = _write_csv(
        tmp_path,
        "hourly.csv",
        "timestamp,base_load\n"
        "2024-01-01 00:00:00,100\n"
        "2024-01-01 01:00:00,200\n"
        "2024-01-01 02:00:00,300\n",
    )

    result = prepare_aligned_timeseries(path, 3600)

    assert result.was_resampled is False
    assert result.path == path
    assert result.source_rows == 3
    assert result.aligned_rows == 3
    assert result.source_dt_seconds == 3600
    assert result.target_dt_seconds == 3600


def test_prepare_aligned_timeseries_downsamples_by_averaging(tmp_path) -> None:
    path = _write_csv(
        tmp_path,
        "hourly.csv",
        "timestamp,base_load\n"
        "2024-01-01 00:00:00,100\n"
        "2024-01-01 01:00:00,200\n"
        "2024-01-01 02:00:00,300\n"
        "2024-01-01 03:00:00,400\n",
    )

    result = prepare_aligned_timeseries(path, 7200)

    assert result.was_resampled is True
    assert result.source_dt_seconds == 3600
    assert result.target_dt_seconds == 7200
    assert result.aligned_rows == 2

    aligned = pd.read_csv(result.path)
    assert list(aligned["base_load"]) == [150.0, 350.0]


def test_prepare_aligned_timeseries_upsamples_by_interpolating(tmp_path) -> None:
    path = _write_csv(
        tmp_path,
        "two_hourly.csv",
        "timestamp,base_load\n" "2024-01-01 00:00:00,100\n" "2024-01-01 02:00:00,300\n",
    )

    result = prepare_aligned_timeseries(path, 3600)

    assert result.was_resampled is True
    assert result.source_dt_seconds == 7200
    assert result.target_dt_seconds == 3600
    assert result.aligned_rows == 3

    aligned = pd.read_csv(result.path)
    assert list(aligned["base_load"]) == [100.0, 200.0, 300.0]


def test_write_csv_atomic_writes_readable_csv_and_no_leftover_tmp(tmp_path) -> None:
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    output_path = tmp_path / "out.csv"

    _write_csv_atomic(df, output_path)

    assert output_path.exists()
    written = pd.read_csv(output_path)
    assert written["a"].tolist() == [1, 2]
    assert written["b"].tolist() == [3, 4]
    assert list(tmp_path.glob("*.tmp")) == []
