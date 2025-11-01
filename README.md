
# GridLock - HELICS Co-Simulation Platform

## Overview

GridLock is a fully containerized, reproducible, and config-driven HELICS co-simulation platform for power grid simulations. It uses **helics_runner** (the HELICS-preferred orchestration method) to manage multiple federates including a power grid (pandapower), multiple houses (HELICS player), and a recorder for experiment data. All runtime parameters are derived from a single YAML configuration file.

## Architecture

GridLock uses modern HELICS best practices:
- **helics_runner per federate class** - Each container type (broker, grid, house, recorder) runs helics_runner
- **Single container per federate class** - One container spawns multiple instances of that federate type
- **Efficient resource usage** - helics_runner spawns instances within containers rather than requiring separate containers per instance

### How It Works

1. **composegen** generates:
   - Separate `helics_runner_*.json` files for each federate class
   - `docker-compose.yml` that launches one container per class

2. **Each container** runs `helics_runner` with its class-specific config:
   - `broker` container: Runs helics_runner with broker config
   - `grid` container: Runs helics_runner to spawn 1 grid federate instance
   - `house` container: Runs helics_runner to spawn N house federate instances (via `count` parameter)
   - `recorder` container: Runs helics_runner to spawn 1 recorder instance

## Quick Start

1. **Edit your experiment parameters** in `config/experiment.yml`.

2. **Run the experiment:**
        ```bash
        ./run.sh
        ```
        This will:
        - Build the configuration generator image
        - Generate separate `helics_runner_*.json` files for each federate class
        - Generate `docker-compose.yml` that launches one container per class
        - Build and launch all containers using docker-compose
        - Each container internally uses helics_runner to spawn federate instances

3. **View results:**
        - Simulation data is recorded by the recorder federate and written to the output file specified in your config (default: `data/output/`)

4. **Stop the simulation:**
        ```bash
        docker compose -f config/tmp/docker-compose.yml down
        ```

## Project Structure

- `broker/`     — Broker container (runs helics_runner with broker config)
- `grid/`       — Grid federate container (runs helics_runner with grid config)
- `house/`      — House federate container (runs helics_runner with house config)
- `recorder/`   — Recorder federate container (runs helics_runner with recorder config)
- `composegen/` — Configuration generator (creates helics_runner_*.json and docker-compose.yml)
- `config/`     — All configuration files (experiment.yml, generated configs)
- `data/`       — Input data (grid files, timeseries) and output results
- `run.sh`      — Main entrypoint script (Linux/Mac)
- `run.ps1`     — Main entrypoint script (Windows)

## Configuration

All runtime parameters are set in `config/experiment.yml`. Example structure:
```yaml
federates:
  broker:
    name: broker
  grid:
    name: grid
    grid_file: kerber_landnetz_freileitung_1.xlsx
  house:
    name: house
    num_houses: 13  # Automatically determined from grid file
    input_file: /data/input/sample_house.csv
  recorder:
    name: recorder
    target: grid
    output_file: /data/output/grid.log
general:
  start_time: 0
  end_time: 82800
  time_step: 3600
```

Key points:
- `num_houses` is automatically calculated from the grid file (number of load buses)
- The broker is automatically managed by helics_runner
- Multiple house instances are spawned using helics_runner's `count` parameter

## Generated Configuration Files

After running composegen, the following files are created in `config/tmp/`:

- `helics_runner_broker.json` - Broker configuration for helics_runner
- `helics_runner_grid.json` - Grid federate configuration with 1 instance
- `helics_runner_house.json` - House federate configuration with N instances (via `count`)
- `helics_runner_recorder.json` - Recorder federate configuration with 1 instance
- `docker-compose.yml` - Docker compose file that launches one container per federate class

Additionally, `config/helics_grid_config.json` is generated with grid-specific HELICS settings.

## Requirements

- Docker (version 20.10 or later)
- Docker Compose v2+
- Linux, macOS, or Windows with WSL2
- No Python or other dependencies needed on the host

## Extending the Platform

### Adding a New Federate Class

1. Create a new directory (e.g., `my_federate/`)
2. Add `Dockerfile` that installs helics[cli] and dependencies
3. Add federate script (e.g., `main.py`)
4. Add federate definition to `config/experiment.yml`
5. Update `composegen/main.py`:
   - Add generation logic for `helics_runner_my_federate.json`
   - Add service to docker-compose generation
6. Set Dockerfile CMD to: `helics_runner /config/tmp/helics_runner_my_federate.json`

### Using cosim-toolbox

The federate containers can use [cosim-toolbox](https://cst.readthedocs.io/) for common utilities:
- `Federate` base class with common patterns
- Configuration management helpers
- Database integration for timeseries storage
- Logging and debugging tools

To use, add `cosim-toolbox` to the federate's Dockerfile:
```dockerfile
RUN pip install --no-cache-dir helics[cli] cosim-toolbox
        pass
```

### Modifying Simulation Parameters

- Edit `config/experiment.yml` to change:
  - Simulation duration (`general.start_time`, `general.end_time`)
  - Time step size (`general.time_step`)
  - Grid topology (`federates.grid.grid_file`)
  - Input data files (`federates.house.input_file`)
  - Output locations (`federates.recorder.output_file`)

## Troubleshooting

- **helics_runner not found**: Ensure the runner container built successfully
- **Permission denied on volumes**: Check that config/ and data/ directories have appropriate permissions
- **Simulation hangs**: Check that the number of federates matches the broker configuration