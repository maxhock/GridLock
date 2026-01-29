"""Configuration file processing utilities.

This module handles loading and processing of configuration files,
following Google Python Style Guide.
"""

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .config import DEFAULT_CONFIG_PATH, DEFAULT_START_TIME, FALLBACK_CONFIG_PATH


def load_config_file(config_path: Optional[Path] = None) -> dict:
    """Load configuration from YAML file.

    Args:
        config_path: Path to configuration file. If None, uses default paths.

    Returns:
        Configuration dictionary.
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
        if not config_path.exists():
            config_path = FALLBACK_CONFIG_PATH

    print(f"Loading configuration from {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def process_general_config(general_cfg: dict) -> None:
    """Process and validate general configuration settings.

    Args:
        general_cfg: General configuration dictionary.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    required_fields = ["end_time"]
    for field in required_fields:
        if field not in general_cfg or general_cfg[field] is None:
            raise ValueError(f"[General] Missing required field '{field}'.")

    if "start_time" not in general_cfg or general_cfg["start_time"] is None:
        general_cfg["start_time"] = DEFAULT_START_TIME

    start_time = general_cfg["start_time"]
    end_time = general_cfg["end_time"]
    try:
        start_ts = pd.Timestamp(start_time)
        if isinstance(end_time, (int, float)):
            end_ts = start_ts + pd.Timedelta(seconds=end_time)
            general_cfg["end_time"] = end_ts.isoformat()
        elif isinstance(end_time, str):
            pd.Timestamp(end_time)
        else:
            raise ValueError(f"End time format not recognized: {end_time}")
    except Exception as e:
        raise ValueError(f"Timestamp logic failed: {e}")
