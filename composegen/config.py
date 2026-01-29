"""Configuration constants and mappings for the composegen module.

This module contains all constant values and configuration mappings used
throughout the composegen package, following Google Python Style Guide.
"""

from pathlib import Path

# Default configuration paths
DEFAULT_CONFIG_PATH = Path("../config/experiment.yml")
FALLBACK_CONFIG_PATH = Path("../config/MV-LV.yml")
DATA_INPUT_PATH = Path("../data/input")
DEFAULT_START_TIME = "2023-01-01T00:00:00"

# Federate type to Docker image and command mappings
FEDERATE_TYPE_MAPPING = {
    "grid": {
        "image": "gridlock-grid:latest",
        "command": "python3 main.py",
    },
    "house": {
        "image": "gridlock-house:latest",
        "command": "python3 main.py",
    },
    "recorder": {
        "image": "gridlock-recorder:latest",
        "command": "helics_recorder",
    },
    "pv": {"image": "gridlock-house:latest", "command": "python3 main.py"},
    "battery": {"image": "gridlock-house:latest", "command": "python3 main.py"},
    "hems": {"image": "gridlock-house:latest", "command": "python3 main.py"},
}

# Default federate mapping for unknown types
DEFAULT_FEDERATE_MAPPING = {"image": "cosim-cst:latest", "command": "python3 main.py"}
