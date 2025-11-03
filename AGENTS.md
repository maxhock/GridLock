# AGENTS.md

This is general information for coding agents applying to this repository and project GridLock.
Do not modify this file unless explicitly asked to do so by the user.

## Concept
GridLock is a Co-Simulation platform for power grids and houses or loads based on Helics.
Each Helics federate class is organized as a separate docker container and communication between them is restricted to using the Helics publication and subscription api.
Class specific features are defined inside each container, whereas experiment specific configurations of each instance are handled by using config files.
These configuration files are derived from a central experiment.yml with a configuration generation container. 
This configuration generation container also generates a docker compose file that is used to launch the correct containers.
Each container by default mounts a central data and config folder.
The data folder contains input and output folders where the input folder contains grid definitions and timeseries data and the output folder contains the resulting timeseries after an experiment.

### Tech Stack
| Component        | Technology         | Version/Notes                | Documentation Link                                      |
|------------------|-------------------|------------------------------|---------------------------------------------------------|
| Simulation       | HELICS            | Latest stable                | [HELICS Docs](https://docs.helics.org/en/latest/)       |
| Grid Modeling    | pandapower        | Python 3.11 compatible       | [pandapower Docs](https://pandapower.readthedocs.io/)   |
| Containerization | Docker Compose    | v2+                          | [Docker Compose Docs](https://docs.docker.com/compose/) |
| Configuration    | OmegaConf         | YAML-based, Python 3.11      | [OmegaConf Docs](https://omegaconf.readthedocs.io/)     |
| Scripting        | Python            | 3.11                         | [Python Docs](https://docs.python.org/3.11/)            |
| Data I/O         | Excel, CSV        | Input: Excel, Output: CSV    | [pandas Docs](https://pandas.pydata.org/docs/)          |
| Testing          | pytest            | Dockerized and local support | [pytest Docs](https://docs.pytest.org/en/stable/)       |
| Formatting       | black, ruff       | Optional, recommended        | [black](https://black.readthedocs.io/) / [ruff](https://docs.astral.sh/ruff/) |

### Container Types / Classes

| Container   | Description                                                                 | Entrypoint / Role                |
|-------------|-----------------------------------------------------------------------------|----------------------------------|
| composegen  | Docker compose file and configuration generator, automatically derives necessary values if possible | Turns experiment.yml into configs |
| broker      | HELICS broker, manages message routing and synchronisation between simulations | Starts HELICS broker             |
| grid        | Grid federate, runs pandapower simulation, subscribes to house setpoints     | Loads grid, runs power flow      |
| rl_house    | RL-based house federate, interacts via obs/action topics for rich control    | RL agent, publishes/receives obs/action |
| csv_house   | Player house federate, publishes precomputed load series from CSV            | Publishes timeseries to grid (needs to be reintroduced later)    |
| recorder    | Recorder federate, captures HELICS topics to output logs                     | Runs helics_recorder CLI         |

### Folder Structure
```
.
├── composegen/           # Compose and config generator (Dockerfile, main.py, test_main.py)
├── grid/                 # Grid federate
│   ├── Dockerfile        # Docker build file for grid federate
│   ├── main.py           # Entrypoint for grid federate
│   └── test_main.py      # Unit tests for grid logic
├── house/                # RL house federate and CSV player
├── recorder/             # Minimal image for helics_recorder
├── config/
│   ├── experiment.yml    # Main experiment configuration (source of truth)
│   └── tmp/              # Generated docker-compose and config files
├── data/
│   ├── input/            # Input datasets (Excel grid files, CSV timeseries)
│   └── output/           # Recorded outputs (logs, results)
├── tools/                # Folder for single use scripts that might be useful but are not core functionality
├── docker-compose.test.yml  # Test container orchestration
├── run.sh                # Entrypoint: run experiment end-to-end (Linux/Mac)
├── run.ps1               # Entrypoint: run experiment end-to-end (Windows)
├── run-tests.sh          # Entrypoint: run all tests (dockerized)
└── README.md             # Project overview and instructions
```


## Code Conventions
Each folder representing a container must be independent of the other folders in the sense that they use their own environment through their dockerfile.
Code is implemented in simple scripts that use functions provided by libraries as far as possible. 
There is no need for pyproject.toml or similar, as the main script is automatically executed through the docker file or compose command.
Each function is accompanied by a function test in the test_*.py file using pytest.

## Configuration Conventions
Configuration decisions that need to be changed for each experiment must be defined in 'experiment.yml' and must be piped to a config inside the config folder for that federate through composegen.
Inside the experiment.yml file two sections define all classes of federates launched and general configuration.
In experiment.yml information must never be doubled.
It must be placed at the most logical place and all other mentions must reference this according to the OmegaConf interpolation pattern ${.nestinglevel.value}.
Other, more static configuration should be either done in code or inside a configuration file in the federate folder.


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

## Issue #23 Todo List - HELICS Runner.json Refactoring

### Phase 1: Grid Federate Proof-of-Concept
- [x] Research HELICS `helics run` command and runner.json format/schema
- [x] Create static runner.json for grid federate
- [x] Update grid Dockerfile/entrypoint to use `helics run` with runner.json
- [x] Test grid federate with runner.json (single instance)
- [x] Document learnings and patterns

### Phase 2: House Federate Implementation
- [x] Create static runner.json for house federate
- [x] Update house Dockerfile/entrypoint to use `helics run` with runner.json
- [x] Test house federate with runner.json (single instance)
- [x] Test house federate with multiple instances (13 houses in one container)

### Phase 3: Remaining Federates
- [x] Create static runner.json for broker
- [x] Update broker Dockerfile/entrypoint to use `helics run` with runner.json
- [x] Create static runner.json for recorder
- [x] Update recorder Dockerfile/entrypoint to use `helics run` with runner.json

### Phase 4: Composegen Integration
- [x] Move all runner.json files from federate folders to config/tmp folder
- [x] Update all Dockerfiles to remove COPY runner.json and use /config mount path in CMD instead
- [x] Test that all federates still work with runner.json loaded from /config mount at runtime
- [x] Refactor composegen to generate runner.json files per federate class
- [x] Refactor composegen to generate simplified docker-compose.yml (one container per class)
- [x] End-to-end test with composegen-generated configs (4 containers: broker, grid, house with 13 instances, recorder)
- [x] Update composegen unit tests (10 tests passing: docker-compose generation, 4 runner.json generation functions, grid config, Excel node detection)
- [ ] Update experiment.yml structure if needed (currently compatible with new architecture)

### Phase 5: Testing & Documentation
- [x] Update docker-compose.test.yml (documented 4-service architecture, composegen-test and grid-test)
- [x] Update README.md with new architecture (comprehensive documentation: architecture overview, runner.json format, workflow, testing, development guide, troubleshooting)

### Phase 6: HELICS Config.json Integration
- [x] Research HELICS config.json format and relationship with runner.json
- [x] Create static config.json for grid federate (broker connection, timing, logging settings)
- [x] Update grid runner.json to reference config.json via --config flag (grid loads config programmatically)
- [x] Update grid/main.py to load config from /config/tmp/grid_config.json with fallback
- [x] Create static config.json for house_player federate (broker connection, player settings)
- [x] Update house_player runner.json to reference config.json via --config flag
- [x] Test house_player with config.json (verify 13 instances work via composegen test)
- [x] Create static config.json for broker (network settings, logging)
- [x] Update broker runner.json to reference config.json via --config flag
- [x] Create static config.json for recorder (capture settings, output format)
- [x] Update recorder runner.json to reference config.json via --config flag
- [x] Update composegen to generate config.json files for each federate class
- [x] Test all federates config generation (all 13 composegen unit tests pass)
- [x] Document config.json vs runner.json separation of concerns (added to README.md)

**Note**: E2E integration testing with actual HELICS execution deferred due to SSL certificate issues in build environment. Unit tests verify correct file generation and structure.

### Phase 7: Cosim-toolbox Integration (Future)
- [ ] (Deferred for later issue)