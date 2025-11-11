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

## TODO: Rework composegen/main.py

- [x] **`load_and_prepare_config` function**:
    - [x] Load `experiment.yml` using OmegaConf.
    - [x] Implement a sub-function to read the grid file and determine `num_nodes`.
    - [x] Inject `num_nodes` into the configuration object.
    - [x] Calculate the number of federates for each type based on placement rules (`placement`, `fill_remaining`) and inject it into the config.
    - [x] Add a validation check to ensure all nodes are assigned, raising an error if `fill_remaining` is not used and assignments are incomplete.
    - [x] Calculate the total number of federates for the broker and inject it into the config.
    - [x] Register the `eval` resolver.
    - [x] Resolve all interpolations to create a final, static configuration.
- [x] **`create_docker_compose` function**:
    - [x] Generate `docker-compose.yml` from the prepared configuration, dynamically creating a service for each federate defined in `experiment.yml`.
- [x] **`create_runner_files` function**:
    - [x] Generate all `_runner.json` files using the now static configuration.
    - [x] This includes the logic for creating federate instances based on the pre-calculated placement map.
- [x] **`create_grid_config` function**:
    - [x] Generate the `grid_config.json` file for the grid federate.
- [x] **Main Orchestration**:
    - [x] Implement the main function to call the above creation functions in sequence.
- [x] **Final Steps**:
    - [x] Replace the old `main.py` with the new, reworked script.
    - [x] Test the entire system with `./run.sh` to ensure correctness.
