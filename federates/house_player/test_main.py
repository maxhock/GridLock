"""Unit tests for the load-player federate."""

import numpy as np
import pandas as pd
import pytest

from src.load_player import LoadPlayerFederate, load_timeseries, lookup_power


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_timeseries() -> pd.DataFrame:
    """Generate a small timeseries DataFrame for testing."""
    return pd.DataFrame(
        {
            "timestamp": [1000, 2000, 3000, 4000],
            "base_load": [100.0, 200.0, 300.0, 400.0],
            "reactive_power": [10.0, 20.0, 30.0, 40.0],
        }
    )


@pytest.fixture
def sample_csv(tmp_path, sample_timeseries) -> str:
    """Write the sample timeseries to a CSV and return the path."""
    path = tmp_path / "test_ts.csv"
    sample_timeseries.to_csv(path, index=False)
    return str(path)


# ---------------------------------------------------------------------------
# load_timeseries tests
# ---------------------------------------------------------------------------


def test_load_timeseries_basic(sample_csv: str) -> None:
    """Loading a valid CSV returns a sorted DataFrame with all columns."""
    df = load_timeseries(sample_csv)
    assert len(df) == 4
    assert "timestamp" in df.columns
    assert "base_load" in df.columns
    assert "reactive_power" in df.columns
    assert list(df["timestamp"]) == [1000, 2000, 3000, 4000]


def test_load_timeseries_adds_reactive_power(tmp_path) -> None:
    """A CSV without reactive_power gets a zero-filled column."""
    path = tmp_path / "no_q.csv"
    pd.DataFrame({"timestamp": [1, 2], "base_load": [10, 20]}).to_csv(
        path, index=False
    )
    df = load_timeseries(str(path))
    assert "reactive_power" in df.columns
    assert df["reactive_power"].sum() == 0.0


def test_load_timeseries_missing_timestamp(tmp_path) -> None:
    """Missing 'timestamp' column raises ValueError."""
    path = tmp_path / "bad.csv"
    pd.DataFrame({"base_load": [1]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="timestamp"):
        load_timeseries(str(path))


def test_load_timeseries_missing_base_load(tmp_path) -> None:
    """Missing 'base_load' column raises ValueError."""
    path = tmp_path / "bad.csv"
    pd.DataFrame({"timestamp": [1]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="base_load"):
        load_timeseries(str(path))


def test_load_timeseries_sorts(tmp_path) -> None:
    """Out-of-order timestamps are sorted."""
    path = tmp_path / "unsorted.csv"
    pd.DataFrame(
        {"timestamp": [3000, 1000, 2000], "base_load": [30, 10, 20]}
    ).to_csv(path, index=False)
    df = load_timeseries(str(path))
    assert list(df["timestamp"]) == [1000, 2000, 3000]


def test_load_timeseries_iso_timestamps(tmp_path) -> None:
    """ISO-8601 timestamp strings are converted to epoch seconds."""
    path = tmp_path / "iso.csv"
    pd.DataFrame(
        {
            "timestamp": ["2023-01-01T00:00:00", "2023-01-01T01:00:00"],
            "base_load": [100, 200],
        }
    ).to_csv(path, index=False)
    df = load_timeseries(str(path))
    assert df["timestamp"].dtype in (np.int64, np.float64)
    assert df["timestamp"].iloc[1] - df["timestamp"].iloc[0] == 3600
    assert df["timestamp"].iloc[0] == 0


def test_load_timeseries_normalizes_epoch_start(tmp_path) -> None:
    """Epoch-based CSV timestamps are shifted to simulation-relative seconds."""
    path = tmp_path / "epoch.csv"
    pd.DataFrame(
        {
            "timestamp": [1672531200, 1672532100, 1672533000],
            "base_load": [100, 200, 300],
        }
    ).to_csv(path, index=False)

    df = load_timeseries(str(path))

    assert list(df["timestamp"]) == [0, 900, 1800]


# ---------------------------------------------------------------------------
# lookup_power tests
# ---------------------------------------------------------------------------


def test_lookup_exact_match(sample_timeseries: pd.DataFrame) -> None:
    """Exact timestamp returns the matching row."""
    p, q = lookup_power(sample_timeseries, 2000)
    assert p == 200.0
    assert q == 20.0


def test_lookup_between_timestamps(sample_timeseries: pd.DataFrame) -> None:
    """Time between rows returns the previous row (sample-and-hold)."""
    p, q = lookup_power(sample_timeseries, 2500)
    assert p == 200.0
    assert q == 20.0


def test_lookup_before_first(sample_timeseries: pd.DataFrame) -> None:
    """Time before first record returns the first row."""
    p, q = lookup_power(sample_timeseries, 500)
    assert p == 100.0
    assert q == 10.0


def test_lookup_after_last(sample_timeseries: pd.DataFrame) -> None:
    """Time after last record returns the last row."""
    p, q = lookup_power(sample_timeseries, 9999)
    assert p == 400.0
    assert q == 40.0


def test_lookup_progresses_for_normalized_epoch_series(tmp_path) -> None:
    """Relative simulation times advance through an epoch-based CSV after normalization."""
    path = tmp_path / "epoch.csv"
    pd.DataFrame(
        {
            "timestamp": [1672531200, 1672532100, 1672533000],
            "base_load": [100.0, 200.0, 300.0],
            "reactive_power": [10.0, 20.0, 30.0],
        }
    ).to_csv(path, index=False)

    df = load_timeseries(str(path))

    assert lookup_power(df, 0) == (100.0, 10.0)
    assert lookup_power(df, 900) == (200.0, 20.0)
    assert lookup_power(df, 1800) == (300.0, 30.0)


# ---------------------------------------------------------------------------
# LoadPlayerFederate tests (unit, no HELICS broker)
# ---------------------------------------------------------------------------


def test_federate_init(sample_timeseries: pd.DataFrame) -> None:
    """Federate initialises with timeseries and empty key maps."""
    fed = LoadPlayerFederate("test_fed", sample_timeseries)
    assert fed.timeseries is sample_timeseries
    assert fed._pub_p_keys == []
    assert fed._pub_q_keys == []


def test_build_pub_keys(sample_timeseries: pd.DataFrame) -> None:
    """_build_pub_keys discovers active_power and reactive_power keys."""
    fed = LoadPlayerFederate("test_fed", sample_timeseries)
    fed.data_to_federation = {
        "publications": {
            "test_fed/active_power": 0.0,
            "test_fed/reactive_power": 0.0,
            "test_fed/voltage": 0.0,
        }
    }
    fed._build_pub_keys()
    assert fed._pub_p_keys == ["test_fed/active_power"]
    assert fed._pub_q_keys == ["test_fed/reactive_power"]


def test_update_internal_model(sample_timeseries: pd.DataFrame) -> None:
    """update_internal_model publishes the correct power values."""
    fed = LoadPlayerFederate("test_fed", sample_timeseries)
    fed.data_to_federation = {
        "publications": {
            "test_fed/active_power": 0.0,
            "test_fed/reactive_power": 0.0,
        }
    }
    fed._build_pub_keys()
    fed.granted_time = 2000

    fed.update_internal_model()

    assert fed.data_to_federation["publications"]["test_fed/active_power"] == 200.0
    assert fed.data_to_federation["publications"]["test_fed/reactive_power"] == 20.0
