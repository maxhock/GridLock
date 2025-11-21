#!/usr/bin/env python3
"""
Process helics_recorder TSV output into formatted CSV.
Reads TSV from stdin, pivots to wide format (time × topics), writes CSV.
"""

import argparse
import sys
from typing import TextIO

import pandas as pd


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Process helics_recorder output to CSV"
    )
    parser.add_argument(
        "--input",
        required=False,
        help="Input file path (if not provided, reads from stdin)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output CSV file path",
    )
    return parser.parse_args()


def read_helics_recorder_stream(stream: TextIO) -> pd.DataFrame:
    """
    Read helics_recorder TSV output from stream.

    Args:
        stream: Input stream (stdin) with TSV data

    Returns:
        DataFrame with columns: time, tag, type, value
    """
    # helics_recorder outputs TSV with header: #time, tag, type*, value
    # Note: type* means the column name has an asterisk in helics_recorder output
    df = pd.read_csv(
        stream,
        sep="\t",
        names=["time", "tag", "type", "value"],
        comment="#",  # Skip header line starting with #
    )

    # Remove quotes from value column (helics_recorder wraps values in quotes)
    df["value"] = df["value"].str.strip('"').astype(float)

    return df


def pivot_to_wide_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot long format (time, tag, value) to wide format (time × topics).

    Args:
        df: Long format DataFrame

    Returns:
        Wide format DataFrame with time as index and tags as columns
    """
    # Pivot: rows = time, columns = tag, values = value
    wide_df = df.pivot(index="time", columns="tag", values="value")

    # Sort by time index
    wide_df = wide_df.sort_index()

    return wide_df


def main() -> None:
    """Main entry point for process_log.py."""
    args = parse_args()

    # Read TSV from file or stdin
    if args.input:
        print(f"Reading from file: {args.input}", file=sys.stderr)
        with open(args.input, "r") as f:
            df_long = read_helics_recorder_stream(f)
    else:
        print("Reading from stdin...", file=sys.stderr)
        df_long = read_helics_recorder_stream(sys.stdin)

    print(f"Read {len(df_long)} records", file=sys.stderr)
    if len(df_long) == 0:
        print("WARNING: No data received from helics_recorder", file=sys.stderr)
        # Still write empty CSV with just time column
        pd.DataFrame(columns=["time"]).to_csv(args.output, index=False)
        return

    # Pivot to wide format
    df_wide = pivot_to_wide_format(df_long)

    # Write to CSV
    df_wide.to_csv(args.output)

    print(f"Processed {len(df_long)} records into {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
