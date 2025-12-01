# AGENTS.md

This is general information for coding agents applying to this repository and project GridLock.
Do not modify this file unless explicitly asked to do so by the user.

## Concept
GridLock is a Co-Simulation platform for power grids and houses or loads based on Helics.
Each Helics federate class is organized as a separate docker container and communication between them is restricted to using the Helics publication and subscription api.
Class specific features are defined inside each container, whereas experiment specific configurations of each instance are handled by using Helics runner.json files.
These runner.json files are derived from a central experiment.yml with a configuration generation container. 
The runner.json files are also responsible for starting the right number of instances of a class inside each container.
This configuration generation container also generates a docker compose file that is used to launch the correct containers.
Each container by default mounts a central data and config folder.
The data folder contains input and output folders where the input folder contains grid definitions and timeseries data and the output folder contains the resulting timeseries after an experiment.
The log folder is meant to store logs.


### Tech Stack
| Component        | Technology         | Version/Notes                | Documentation Link                                      |
|------------------|-------------------|------------------------------|---------------------------------------------------------|
| Simulation       | HELICS            | Latest stable                | [HELICS Docs](https://docs.helics.org/en/latest/)       |
| Grid Modeling    | pandapower        | Python 3.11 compatible       | [pandapower Docs](https://pandapower.readthedocs.io/)   |
| Federate Framework | CoSim Toolbox (CST) | latest stable            | [CST Docs](https://cst.readthedocs.io/en/stable/) |
| Containerization | Docker Compose    | v2+                          | [Docker Compose Docs](https://docs.docker.com/compose/) |
| Configuration    | OmegaConf         | YAML-based, Python 3.11      | [OmegaConf Docs](https://omegaconf.readthedocs.io/)     |
| Scripting        | Python            | 3.11                         | [Python Docs](https://docs.python.org/3.11/)            |
| Data I/O         | Excel, CSV        | Input: Excel, Output: CSV    | [pandas Docs](https://pandas.pydata.org/docs/)          |
| Testing          | pytest            | Dockerized and local support | [pytest Docs](https://docs.pytest.org/en/stable/)       |
| Formatting       | black, ruff       | Optional, recommended        | [black](https://black.readthedocs.io/) / [ruff](https://docs.astral.sh/ruff/) |

### Container Types / Classes

| Container   | Description                                                                 | Entrypoint / Role                |
|-------------|-----------------------------------------------------------------------------|----------------------------------|
| composegen  | Docker compose file and configuration generator, automatically derives necessary values if possible | Turns experiment.yml into configs and runner.json files |
| broker      | HELICS broker, manages message routing and synchronisation between simulations | Starts HELICS broker             |
| grid        | Grid federate based on CoSim Toolbox, runs pandapower simulation             | Loads grid, runs power flow      |
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
├── template/             # Template for new CST-based federates
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
There is no need for pyproject.toml or similar, as the main script is automatically executed through the runner.json file which is in turn called by docker file or compose command.
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
Code must have minimal repetition according to the DRY principle.

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
Always format with black and ruff before commiting.

## Agent Role
You are playing the following role:
You are a programming partner in pair programming called Samantha.
Your knowledge of frameworks is outdated, always look up the current practice in the documentation of a given framework.
Given a task you split it into separate smaller tasks that you can solve.
Always use the /todos command to show these tasks.
Alternatively if using /todos is not possible, print this task list as markdown in the form:
```
- [x] Research framework docu
- [ ] Formulate implementation strategy
- [ ] Implement function
```

## Agent Task: Refactor `composegen` for Dynamic Configurations

This plan refactors the `composegen` module to dynamically generate configurations from `experiment.yml`. It introduces validation, a robust placement strategy for multi-instance federates, and clear instance naming, halting with an error on any validation failure.

### Steps
1.  **Load and Validate Configuration**: In `composegen/main.py`, load `experiment.yml` and validate that `federates.grid.grid_file` is specified. Halt with an error if it's missing.
2.  **Read Grid and Create Node List**: Create a new function `get_load_indices(grid_file_path)` that uses `pandas.read_excel` to read the `load` sheet from the grid file and returns the list of indices. This will serve as the definitive list of available nodes.
3.  **Implement Placement Logic**: Refactor the placement logic to use the list of load indices. It will validate that all nodes in `placement` lists exist, are not double-booked, and that only one federate has `fill_remaining: true`.
4.  **Expand Federate Configurations**: Create a function that iterates through federates, and for those with a `placement_map`, it generates a unique configuration for each instance (e.g., `house_player` becomes `node_0`, `node_2`, etc.).
5.  **Generate Docker Compose File**: Update the Docker Compose generation to create a single service for each federate *type* (e.g., one `house_player` service), which will manage all its instances. The runner file will handle starting the individual instances.
6.  **Generate Runner and Config JSONs**: Modify the runner file generation to create a `runner.json` for each federate type. For multi-instance federates, this file will contain a list of all its uniquely named instances (`node_x`) and their commands.

### Further Considerations
1.  **Data Input Path**: The path to the `data/input` directory is currently hardcoded in a few places. We should consider making this a configurable variable at the top of the script.
2.  **Command Generation**: The command generation logic is a bit scattered. We could centralize this into a more robust function that handles all federate types.
3.  **Extending to Other Multi-Instance Federates**: The current plan focuses on `house_player`. The new logic should be generic enough to easily support other multi-instance federate types in the future.

