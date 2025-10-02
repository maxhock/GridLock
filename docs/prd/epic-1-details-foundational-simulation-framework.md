# Epic 1 Details: Foundational Simulation Framework

**Expanded Goal:** The goal of this epic is to build the minimum viable product (MVP) of the GridLock framework. By the end of this epic, a user will be able to define a complete simulation scenario in a configuration file, run the simulation from their command line, and get the results. This epic will deliver a fully functional, end-to-end workflow, complete with a solid foundation of automated tests to ensure reliability.

**Stories:**

*   **Story 1.0: Establish Project Standards.**
    *   As a developer, I want to have clear, documented standards for dependencies, code style, and code review, so that all contributions to the project are consistent and high-quality.
    *   *(This story will include creating a requirements.txt, configuring black and ruff, and updating CONTRIBUTING.md with coding/review standards.)*

*   **Story 1.1: Initial Project Setup & Configuration.**
    *   As a developer, I want to set up the basic project structure and a simple configuration file parser, so that we have a foundation for building the simulation runner.
    *   *(This story will include unit tests for the configuration parser.)*

*   **Story 1.2: Dynamic Docker Compose Generation.**
    *   As a developer, I want to dynamically generate a `docker-compose.yml` file based on the federates defined in the `experiment.yml`, so that we can launch custom simulation scenarios.
    *   *(This story will include unit tests to verify the generated `docker-compose.yml` content.)*

*   **Story 1.3: Basic Simulation Execution.**
    *   As a developer, I want to be able to launch and shut down the simulation using `docker-compose`, so that I can run a simple, end-to-end simulation.
    *   *(This story will include a basic integration test with dummy federates.)*

*   **Story 1.4: Grid Simulator Integration.**
    *   As a developer, I want to integrate the Grid Simulator federate into the framework, so that we can simulate a power grid.
    *   *(This story will include integration tests to verify the Grid Simulator joins the federation correctly.)*

*   **Story 1.5: Simple House Simulator Integration.**
    *   As a developer, I want to integrate the simple, CSV-based House Simulator, so that we can attach loads to the grid.
    *   *(This story will include an integration test to verify that a House Simulator can connect to a Grid Simulator.)*

*   **Story 1.6: Basic Results Recorder.**
    *   As a developer, I want to implement a basic, configurable recorder federate, so that we can capture and store simulation results.
    *   *(This story will include an integration test to verify the recorder captures data from a running simulation.)*

*   **Story 1.7: Setup CI/CD Pipeline.**
    *   As a developer, I want a basic CI/CD pipeline using GitHub Actions, so that our tests are run automatically on every code change.
    *   *(This story will include a simple workflow that runs the unit and integration tests.)*