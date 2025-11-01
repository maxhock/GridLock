
# GridLock - HELICS Co-Simulation Platform

## Overview

GridLock is a fully containerized, reproducible, and config-driven HELICS co-simulation platform for power grid simulations. It uses **helics_runner** (the HELICS-preferred orchestration method) to manage multiple federates including a power grid (pandapower), multiple houses (HELICS player), and a recorder for experiment data. All runtime parameters are derived from a single YAML configuration file.

## Architecture

GridLock uses modern HELICS best practices:
- **helics_runner.json** - Configuration-driven federate orchestration
- **Single container per federate class** - Efficient resource usage with helics_runner spawning multiple instances
- **cosim-toolbox integration** - Common utilities for HELICS co-simulation development

## Quick Start

1. **Edit your experiment parameters** in `config/experiment.yml`.

2. **Run the experiment:**
        ```bash
        ./run.sh
        ```
        This will:
        - Build the configuration generator image
        - Generate `helics_runner.json` in `config/tmp/` based on your config
        - Build the simulation runner container (contains all federates)
        - Launch the co-simulation using helics_runner

3. **View results:**
        - Simulation data is recorded by the recorder federate and written to the output file specified in your config (default: `data/output/`)

## Legacy Docker Compose Support

For backward compatibility, the system also generates a `docker-compose.yml` file. To use the legacy approach:
```bash
docker compose -f config/tmp/docker-compose.yml up --build
docker compose -f config/tmp/docker-compose.yml down
```

## Project Structure

- `runner/`      — Main simulation container (contains all federates + helics_runner)
- `grid/`        — Grid federate (pandapower power flow simulation)
- `house/`       — House federate (uses HELICS player for load profiles)
- `broker/`      — Legacy broker container (deprecated, now handled by helics_runner)
- `recorder/`    — Legacy recorder container (now part of runner container)
- `composegen/`  — Configuration generator (creates helics_runner.json)
- `config/`      — All configuration files (experiment.yml, generated configs)
- `data/`        — Input data (grid files, timeseries) and output results
- `run.sh`       — Main entrypoint script (Linux/Mac)
- `run.ps1`      — Main entrypoint script (Windows)

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

## How It Works

1. **Configuration Generation**: The composegen container reads `experiment.yml` and generates:
   - `helics_runner.json` - Federate orchestration configuration
   - `helics_grid_config.json` - Grid-specific HELICS configuration
   - `docker-compose.yml` - Legacy support

2. **Federate Orchestration**: The runner container uses helics_runner to:
   - Start an embedded HELICS broker
   - Spawn the grid federate (single instance)
   - Spawn multiple house federates (count determined by grid topology)
   - Start the recorder federate
   
3. **Simulation Execution**: 
   - Grid federate runs pandapower power flow calculations
   - House federates publish load profiles via helics_player
   - Recorder captures all data to output files
   
4. **Resource Efficiency**: Unlike the legacy approach (one container per federate instance), helics_runner spawns multiple instances within a single container, reducing overhead.

## Requirements

- Docker (version 20.10 or later)
- Linux, macOS, or Windows with WSL2
- No Python or other dependencies needed on the host

## Extending the Platform

### Adding a New Federate

1. Create federate script in appropriate directory
2. Add federate definition to `config/experiment.yml`
3. Update `composegen/main.py` to include the new federate in `helics_runner.json`
4. If needed, add dependencies to `runner/Dockerfile`

### Using cosim-toolbox

The runner container includes [cosim-toolbox](https://cst.readthedocs.io/), which provides utilities for HELICS federate development:
- `Federate` base class with common patterns
- Configuration management helpers
- Database integration for timeseries storage
- Logging and debugging tools

Example usage in a custom federate:
```python
from cosim_toolbox.sims import Federate

class MyCustomFederate(Federate):
    def update_model(self, current_time):
        # Your simulation logic here
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