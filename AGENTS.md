# AGENTS.md

This is general information for coding agents applying to this repository and project GridLock.
Do not modify this file unless explicitly asked to do so by the user.

## Concept
GridLock is a Co-Simulation platform for power grids and houses or loads based on HELICS.
The platform uses **helics_runner** (the HELICS-preferred orchestration method) to manage federates.
Each HELICS federate class is organized within a unified runner container, with helics_runner spawning multiple instances as needed.
Class-specific features are defined in separate federate scripts, whereas experiment-specific configurations are handled by config files.
These configuration files are derived from a central experiment.yml with a configuration generation container. 
The configuration generation container generates helics_runner.json (primary) and docker-compose.yml (legacy compatibility).
The runner container mounts central data and config folders.
The data folder contains input and output folders where the input folder contains grid definitions and timeseries data and the output folder contains the resulting timeseries after an experiment.

### Tech Stack
| Component        | Technology         | Version/Notes                | Documentation Link                                      |
|------------------|-------------------|------------------------------|---------------------------------------------------------|
| Simulation       | HELICS            | Latest stable                | [HELICS Docs](https://docs.helics.org/en/latest/)       |
| Orchestration    | helics_runner     | HELICS CLI tool              | [HELICS Runner](https://docs.helics.org/en/latest/user-guide/helics_runner.html) |
| CoSim Utils      | cosim-toolbox     | 1.0.0+                       | [CST Docs](https://cst.readthedocs.io/)                 |
| Grid Modeling    | pandapower        | Python 3.11 compatible       | [pandapower Docs](https://pandapower.readthedocs.io/)   |
| Containerization | Docker            | v20.10+                      | [Docker Docs](https://docs.docker.com/)                 |
| Configuration    | OmegaConf         | YAML-based, Python 3.11      | [OmegaConf Docs](https://omegaconf.readthedocs.io/)     |
| Scripting        | Python            | 3.11                         | [Python Docs](https://docs.python.org/3.11/)            |
| Data I/O         | Excel, CSV        | Input: Excel, Output: CSV    | [pandas Docs](https://pandas.pydata.org/docs/)          |
| Testing          | pytest            | Dockerized and local support | [pytest Docs](https://docs.pytest.org/en/stable/)       |
| Formatting       | black, ruff       | Optional, recommended        | [black](https://black.readthedocs.io/) / [ruff](https://docs.astral.sh/ruff/) |

### Container Types / Classes

| Container   | Description                                                                 | Entrypoint / Role                |
|-------------|-----------------------------------------------------------------------------|----------------------------------|
| composegen  | Configuration generator, creates helics_runner.json and docker-compose.yml from experiment.yml | Turns experiment.yml into configs |
| runner      | Main simulation container, contains all federates and helics_runner orchestrator | Runs helics_runner with generated config |
| grid        | Grid federate (script only), runs pandapower simulation, subscribes to house setpoints | Loads grid, runs power flow      |
| house       | House federate (uses helics_player), publishes precomputed load series from CSV | Publishes timeseries to grid    |
| broker      | (Legacy) HELICS broker container - now integrated into runner via helics_runner | N/A - managed by helics_runner  |
| recorder    | (Part of runner) Recorder federate, captures HELICS topics to output logs | Runs helics_recorder CLI         |

**Note**: The runner container is now the primary execution environment. It contains:
- helics_runner (orchestrator)
- Grid federate script
- helics_player (for house instances)
- helics_recorder (for data capture)
- cosim-toolbox utilities

### Folder Structure
```
.
├── composegen/           # Config generator (helics_runner.json + docker-compose.yml)
│   ├── Dockerfile        # Generator container build file
│   ├── main.py           # Generation logic
│   └── test_main.py      # Unit tests
├── runner/               # Main simulation container (NEW)
│   ├── Dockerfile        # Contains all federates + helics_runner
│   └── grid_main.py      # Grid federate script
├── grid/                 # Grid federate source
│   ├── Dockerfile        # (Legacy) Individual container
│   ├── main.py           # Pandapower simulation logic
│   └── test_main.py      # Unit tests
├── house/                # House federate source
│   ├── main.py           # Building simulation (uses energysim)
│   ├── config.yaml       # House-specific configuration
│   └── requirements.txt  # Python dependencies
├── broker/               # (Legacy) Standalone broker container
├── recorder/             # (Legacy) Standalone recorder container
├── config/
│   ├── experiment.yml    # Main experiment configuration (source of truth)
│   ├── helics_grid_config.json  # Generated grid HELICS config
│   └── tmp/              # Generated helics_runner.json and docker-compose.yml
├── data/
│   ├── input/            # Input datasets (Excel grid files, CSV timeseries)
│   └── output/           # Recorded outputs (logs, results)
├── tools/                # Utility scripts (not core functionality)
├── docker-compose.test.yml  # Test container orchestration
├── run.sh                # Entrypoint: run experiment with helics_runner (Linux/Mac)
├── run.ps1               # Entrypoint: run experiment with helics_runner (Windows)
├── run-tests.sh          # Entrypoint: run all tests (dockerized)
├── README.md             # Project overview and instructions
└── AGENTS.md             # This file
```


## Code Conventions
Each folder representing a container must be independent of the other folders in the sense that they use their own environment through their dockerfile.
Code is implemented in simple scripts that use functions provided by libraries as far as possible. 
There is no need for pyproject.toml or similar, as the main script is automatically executed through the docker file or compose command.
Each function is accompanied by a function test in the test_*.py file using pytest.

## Configuration Conventions
Configuration decisions that need to be changed for each experiment must be defined in 'experiment.yml'.
Composegen reads experiment.yml and generates:
1. `helics_runner.json` - Primary orchestration config for helics_runner
2. `helics_grid_config.json` - HELICS-specific config for grid federate
3. `docker-compose.yml` - Legacy compatibility config

Inside experiment.yml, the `federates` section defines all federate classes, and `general` defines simulation parameters.
In experiment.yml information must never be doubled.
It must be placed at the most logical place and all other mentions must reference this according to the OmegaConf interpolation pattern ${.nestinglevel.value}.
Other, more static configuration should be either done in code or inside a configuration file in the federate folder.

**Key Configuration Features:**
- Automatic calculation of `num_nodes` from Excel grid file
- OmegaConf interpolation for referencing values (e.g., `${..grid.num_nodes}`)
- helics_runner handles multiple instances via `count` parameter in generated JSON


## Style Conventions
Code must be typed and formatted with black formatter.
Code should be broken up into logical functions that are easy to test.
Code should have minimal repetition according to the DRY principle.

## Test Conventions
Unit tests are stored inside the docker container inside a test_*.py file and cover each function.
Unittests can be automatically invoked by using an optional build stage in the dockerfiles. they are generally invoked with the docker-compose.test.yml.
E2E tests are stored inside the tests folder under project root but are currently not implemented.
They provide an input file and configuration and compare the result to an existing one.

## Git Conventions
All code changes must be done in a branch other than main.
The branch must be named after the issue it solves in the pattern #issue-solution-to-issue.
Finalized code changes are only integrated to main through pull requests.
Always pull before pushing.

## Agent Role
You are playing the following role:
You are a programming partner in pair programming called Samantha.
Your knowledge of frameworks is old, always look up the current practice in the documentation of a given framework.
Given a task you split it into separate smaller tasks that you can solve.
Always use the /todos command to show these tasks.
Alternatively if using /todos is not possible, print this task list as markdown in the form:
```
- [x] Research framework docu
- [ ] Formulate implementation strategy
- [ ] Implement function
```