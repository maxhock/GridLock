
# HELICS Co-Simulation Project

## Overview

This project provides a fully containerized, reproducible, and config-driven HELICS co-simulation workflow for a power grid (pandapower), multiple houses (HELICS player), and a recorder for experiment data. All orchestration is handled via Docker Compose, with all runtime parameters derived from a single YAML config.

## Quick Start

1. **Edit your experiment parameters** in `config/experiment.yml`.

2. **Run the experiment:**
        ```bash
        ./run.sh
        ```
        This will:
        - Build the compose generator image.
        - Generate a `docker-compose.yaml` in `config/tmp/` based on your config.
        - Build and launch all federates (broker, grid, houses, recorder) as containers.

3. **View results:**
        - Simulation data is recorded by the recorder federate and written to the output file specified in your config (default location may be `data/` or as configured).

4. **Clean up:**
        ```bash
        docker compose -f config/tmp/docker-compose.yaml down
        ```

## Project Structure

- `broker/`      — Broker Dockerfile
- `grid/`        — Grid federate (pandapower)
- `grid_pypsa/`  — Grid federate (PyPSA alternative)
- `house/`       — House federate (HELICS player)
- `recorder/`    — Minimal HELICS recorder federate
- `composegen/`  — Docker Compose generator script and Dockerfile
- `config/`      — All configuration files (YAML, CSV, etc.)
- `data/`        — Data and results (recorder output)
- `run.sh` — Main entrypoint script

## Configuration

All runtime parameters are set in `config/experiment.yml`. Example:
```yaml
federates:
        broker:
                name: broker
        grid:
                name: grid
                build_folder: grid
                grid_file: kerber_landnetz_freileitung_1.xlsx
        house_player:
                name: house_player
                fill_remaining: true
        recorder:
                name: recorder
                target: grid
```

### Using PyPSA Instead of pandapower

To use PyPSA for power flow simulation, replace `grid:` with `grid_pypsa:` in your configuration:

```yaml
federates:
        broker:
                name: broker
        grid_pypsa:
                name: grid
                build_folder: grid_pypsa
                grid_file: kerber_landnetz_freileitung_1.xlsx
        # ... other federates
```

See `config/experiment_pypsa.yml` for a complete example. Both grid federates use the same input format (pandapower Excel files) and provide identical HELICS interfaces, making them drop-in replacements for each other.

### Configuration Options

- Change grid federate implementation (`grid` or `grid_pypsa`)
- Set number of houses using placement or fill_remaining
- The broker, grid federate and recorder are always included.

## How It Works

- The compose generator reads your config and generates a Docker Compose file with the correct number of federates and all runtime parameters.
- Each federate (grid, house, recorder, broker) runs in its own container.
- The recorder subscribes to `Grid` and writes results to the output file as configured (default may be in `data/`).
- All configuration and results are mounted from the host for easy access and reproducibility.

## Requirements

- Docker (Linux or WSL recommended)
- No Python or venv needed on the host

## Extending

- Edit `config/experiment.yml` to change experiment parameters.
- Add new federates by extending the config and compose generator.
- Add new recorder topics by editing the recorder command in the compose generator.
- All logic is modular and config-driven for easy experimentation.