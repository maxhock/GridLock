
# HELICS Co-Simulation Project

## Overview

This project provides a fully containerized, reproducible, and config-driven HELICS co-simulation workflow for a power grid (pandapower), multiple houses (HELICS player), and a recorder for experiment data. All orchestration is handled via Docker Compose, with all runtime parameters derived from a single YAML config.

## Quick Start

1. **Edit your experiment parameters** in `config/experiment_config.yaml`.

2. **Run the experiment:**
        ```bash
        ./run_with_composegen.sh
        ```
        This will:
        - Build the compose generator image.
        - Generate a `docker-compose.yaml` in `config/tmp/` based on your config.
        - Build and launch all federates (broker, grid, houses, recorder) as containers.

3. **View results:**
        - Simulation data is recorded by the recorder federate and written to `data/output/transformer_power.log`.

4. **Clean up:**
        ```bash
        docker compose -f config/tmp/docker-compose.yaml down
        ```

## Project Structure

- `broker/`      — Broker Dockerfile
- `grid/`        — Grid federate
- `house/`       — House federate (HELICS player)
- `recorder/`    — Minimal HELICS recorder federate
- `composegen/`  — Docker Compose generator script and Dockerfile
- `config/`      — All configuration files (YAML, CSV, etc.)
- `data/`        — Data and results (recorder output)
- `run.sh` — Main entrypoint script

## Configuration

All runtime parameters are set in `config/experiment_config.yaml`. Example:
```yaml
experiment:
    num_houses: 4
    broker:
        name: broker
    house:
        csv: /config/sample_house.csv
    grid:
        script: GridSimulation.py
```
- Change `num_houses` to set the number of house federates.
- The broker, grid federate and recorder are always included.

## How It Works

- The compose generator reads your config and generates a Docker Compose file with the correct number of federates and all runtime parameters.
- Each federate (grid, house, recorder, broker) runs in its own container.
- The recorder subscribes to `Grid` and writes results to `data/output/grid.log`.
- All configuration and results are mounted from the host for easy access and reproducibility.

## Requirements

- Docker (Linux or WSL recommended)
- No Python or venv needed on the host

## Extending

- Edit `config/experiment_config.yaml` to change experiment parameters.
- Add new federates by extending the config and compose generator.
- Add new recorder topics by editing the recorder command in the compose generator.
- All logic is modular and config-driven for easy experimentation.