# 3. High-Level Architecture

The project implements a **HELICS-based co-simulation framework orchestrated by Docker Compose**. The architecture is designed to be highly configurable and dynamically generated.

The central pattern is:
1.  A user defines a simulation scenario in a high-level YAML file (`experiment.yml`).
2.  A Python script (`composegen/main.py`) parses this file.
3.  This script dynamically generates a `docker-compose.yml` file, creating a service for each simulation "federate" (the broker, the grid, and N houses).
4.  Each service is a container running a specific simulation logic (e.g., `grid/main.py`).
5.  The services communicate with each other over a Docker network using the HELICS message bus protocol.

## 3.1. Actual Tech Stack

| Category      | Technology  | Version | Notes                                                                                             |
|---------------|-------------|---------|---------------------------------------------------------------------------------------------------|
| Language      | Python      | ~3.x    | (Version not specified, assumed to be modern Python 3)                                            |
| Co-simulation | HELICS      | (TBD)   | Used for message bus, time synchronization, and federate communication.                           |
| Grid Modeling | pandapower  | (TBD)   | Used in the `grid` service to simulate the electrical network.                                    |
| Configuration | OmegaConf   | (TBD)   | Core technology used in `composegen` to parse, merge, and resolve the `experiment.yml` config.    |
| Orchestration | Docker      | (TBD)   | Each federate runs in its own container.                                                          |
|               | Docker Compose| (TBD)   | Used to define and run the multi-container simulation environment. The config is auto-generated.|

## 3.2. Repository Structure

- **Type**: Monorepo. Each core service (`grid`, `house`, `composegen`, etc.) is a directory within the single repository.
- **Notable**: The separation of concerns is clean, with each directory representing a distinct microservice/federate.
