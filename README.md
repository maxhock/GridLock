
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
- `grid/`        — Grid federate
- `house/`       — House federate (HELICS player)
- `recorder/`    — Minimal HELICS recorder federate
- `composegen/`  — Docker Compose generator script and Dockerfile
- `config/`      — All configuration files (YAML, CSV, etc.)
- `data/`        — Data and results (recorder output)
- `run.sh` — Main entrypoint script

## Configuration

All runtime parameters are set in `config/experiment.yml`. Example:
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
- The recorder subscribes to `Grid` and writes results to the output file as configured (default may be in `data/`).
- All configuration and results are mounted from the host for easy access and reproducibility.

### Configuration Architecture

The system uses a two-layer configuration approach for maximum flexibility:

#### 1. experiment.yml (Source of Truth)
- **Location**: `config/experiment.yml`
- **Purpose**: High-level experiment configuration
- **Contents**: 
  - Number of houses (derived from grid file)
  - Grid file specification
  - Timing parameters (start_time, end_time, time_step)
  - Broker, grid, house, and recorder settings
- **Usage**: Edit this file to configure your experiment

#### 2. Generated Configuration Files (config/tmp/)

The composegen container reads `experiment.yml` and generates:

**docker-compose.yml**
- Defines 4 services: broker, grid, house, recorder
- Each service runs multiple federate instances via runner.json

**runner.json files** (one per federate class)
- `broker_runner.json` - Defines broker execution
- `grid_runner.json` - Defines grid federate execution
- `house_runner.json` - Defines all house player instances
- `recorder_runner.json` - Defines recorder execution
- **Purpose**: Specify which executables to run and their command-line arguments
- **Format**: HELICS runner format with federates array

**config.json files** (one per federate class)
- `broker_config.json` - Broker settings (log level, network interface)
- `grid_config.json` - Grid federate settings (timing, broker connection)
- `house_player_config.json` - House player settings (broker connection)
- `recorder_config.json` - Recorder settings (broker connection)
- **Purpose**: Define federate-specific HELICS configuration
- **Format**: HELICS configuration format (JSON)

#### Separation of Concerns

| File Type | Purpose | Examples |
|-----------|---------|----------|
| **runner.json** | Federation structure, execution commands | Which program to run, command-line arguments, working directory |
| **config.json** | HELICS federate configuration | Timing parameters, broker connection, log levels, core type |
| **experiment.yml** | High-level experiment parameters | Grid file, number of houses, simulation duration |

**Example Flow:**
1. User edits `config/experiment.yml` (e.g., change grid file)
2. `run.sh` invokes composegen
3. Composegen generates:
   - `config/tmp/docker-compose.yml`
   - `config/tmp/*_runner.json` (4 files)
   - `config/tmp/*_config.json` (4 files)
4. Docker Compose launches containers
5. Each container runs `helics run --path=/config/tmp/{federate}_runner.json`
6. HELICS tools load their config via `--config=/config/tmp/{federate}_config.json`

This architecture allows:
- **Reproducibility**: All generated files are in `config/tmp/`
- **Flexibility**: Change experiment parameters without modifying Docker images
- **Clarity**: Clear separation between structure (runner.json) and configuration (config.json)

## Requirements

- Docker (Linux or WSL recommended)
- No Python or venv needed on the host

## Extending

- Edit `config/experiment.yml` to change experiment parameters.
- Add new federates by extending the config and compose generator.
- Add new recorder topics by editing the recorder command in the compose generator.
- All logic is modular and config-driven for easy experimentation.