# 4. Source Tree and Module Organization

## 4.1. Project Structure (Actual)

```text
HelicsProto/
├── composegen/
│   └── main.py        # Reads experiment.yml, generates docker-compose.yml
├── config/
│   ├── experiment.yml   # (Assumed) User-defined experiment parameters
│   └── helics_grid_config.json # Generated HELICS config for the grid
├── grid/
│   └── main.py        # Pandapower/HELICS grid simulator logic
├── house/
│   └── Dockerfile     # (Content TBD) Logic for house simulators
├── broker/
│   └── Dockerfile     # HELICS broker service
├── recorder/
│   └── Dockerfile     # HELICS recorder service
└── data/
    └── input/         # Location for input data like CSV profiles
```

## 4.2. Key Modules and Their Purpose

- **`composegen`**: The "brains" of the operation. It is a meta-service that builds the simulation environment itself from a high-level definition. Its output is a complete Docker Compose file.
- **`grid`**: A HELICS "federate" that simulates the main power grid. It uses `pandapower` for the physics simulation and communicates its state (like transformer power) to other federates. It subscribes to load data from the `house` federates.
- **`house`**: Represents a consumer of electricity. In the MVP, this will be a simple CSV-replay federate that publishes its load to the `grid` federate.
- **`broker`**: The central HELICS message broker that all federates connect to.
