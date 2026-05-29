from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import os
from pathlib import Path
import tempfile

import pandas as pd
from pandas.api.types import is_numeric_dtype


@dataclass(frozen=True)
class AlignedTimeseries:
    path: str
    source_dt_seconds: int
    target_dt_seconds: int
    source_rows: int
    aligned_rows: int
    was_resampled: bool


def _write_csv_atomic(df: pd.DataFrame, output_path: Path) -> None:
    """Write a CSV via temp file + replace so concurrent readers never see partial data."""
    fd, tmp_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f"{output_path.stem}.",
        suffix=".tmp",
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        df.to_csv(tmp_path, index=False)
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _infer_source_dt_seconds(timestamps: pd.Series) -> int:
    deltas = timestamps.sort_values().diff().dropna().dt.total_seconds()
    positive_deltas = deltas[deltas > 0]
    if positive_deltas.empty:
        raise ValueError("Cannot infer source timestep from fewer than two timestamps.")

    return int(round(float(positive_deltas.mode().iloc[0])))


def prepare_aligned_timeseries(
    source_path: str,
    target_dt_seconds: int,
    timestamp_column: str = "timestamp",
) -> AlignedTimeseries:
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Exogenous dataset not found: {source}")
    if target_dt_seconds <= 0:
        raise ValueError(f"target_dt_seconds must be > 0, got {target_dt_seconds}")

    df = pd.read_csv(source)
    if timestamp_column not in df.columns:
        raise ValueError(
            f"Expected timestamp column '{timestamp_column}' in {source}, "
            f"found columns: {list(df.columns)}"
        )

    timestamps = pd.to_datetime(df[timestamp_column], errors="raise")
    source_dt_seconds = _infer_source_dt_seconds(timestamps)

    if source_dt_seconds == target_dt_seconds:
        return AlignedTimeseries(
            path=str(source),
            source_dt_seconds=source_dt_seconds,
            target_dt_seconds=target_dt_seconds,
            source_rows=len(df),
            aligned_rows=len(df),
            was_resampled=False,
        )

    data = df.copy()
    data[timestamp_column] = timestamps
    data = data.sort_values(timestamp_column).drop_duplicates(subset=[timestamp_column])
    data = data.set_index(timestamp_column)

    numeric_cols = [col for col in data.columns if is_numeric_dtype(data[col])]
    non_numeric_cols = [col for col in data.columns if col not in numeric_cols]

    target_freq = pd.to_timedelta(target_dt_seconds, unit="s")

    if target_dt_seconds > source_dt_seconds:
        pieces = []
        if numeric_cols:
            pieces.append(
                data[numeric_cols].resample(target_freq, label="left", closed="left").mean()
            )
        if non_numeric_cols:
            pieces.append(
                data[non_numeric_cols].resample(
                    target_freq, label="left", closed="left"
                ).first()
            )
        aligned = pd.concat(pieces, axis=1)
    else:
        pieces = []
        if numeric_cols:
            pieces.append(
                data[numeric_cols].resample(target_freq).interpolate(method="time")
            )
        if non_numeric_cols:
            pieces.append(data[non_numeric_cols].resample(target_freq).ffill())
        aligned = pd.concat(pieces, axis=1)

    aligned = aligned.dropna(how="all").reset_index()
    aligned[timestamp_column] = aligned[timestamp_column].dt.strftime("%Y-%m-%d %H:%M:%S")

    cache_dir = Path("/tmp/gridlock_aligned")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = (
        f"{source.resolve()}|{source.stat().st_mtime_ns}|{source.stat().st_size}|"
        f"{target_dt_seconds}"
    )
    digest = sha1(cache_key.encode("utf-8")).hexdigest()[:12]
    output_path = cache_dir / f"{source.stem}_dt{target_dt_seconds}_{digest}.csv"
    _write_csv_atomic(aligned, output_path)

    return AlignedTimeseries(
        path=str(output_path),
        source_dt_seconds=source_dt_seconds,
        target_dt_seconds=target_dt_seconds,
        source_rows=len(df),
        aligned_rows=len(aligned),
        was_resampled=True,
    )
