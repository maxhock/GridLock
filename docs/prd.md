# GridLock Product Requirements Document (PRD)

## Goals and Background Context

### Goals

*   Secure Funding: Leverage GridLock as a core technological asset in the next major research grant proposal.
*   Increase Research Output: Enable the publication of at least 3 scientific papers that rely on the framework's capabilities within the next 3 years.
*   Reduced Onboarding Time: A new researcher can independently set up and run a baseline simulation-to-HIL experiment within one week of being introduced to the tool.
*   Enable Autonomous Research: A single researcher can manage the full project lifecycle from pure simulation to HIL validation, eliminating the need for a dedicated HIL specialist for setup and execution.

### Background Context

Researchers currently face significant inefficiencies when combining power grid simulations with Hardware-in-the-Loop (HIL) testing, requiring manual creation of separate setups and expertise in different environments. GridLock addresses this by providing a modular, containerized co-simulation platform using HELICS, allowing flexible integration of various simulation tools and seamless transition between pure simulation and HIL testing. This enables a single researcher to manage the entire workflow, breaking down knowledge silos and accelerating research.

### Change Log

| Date | Version | Description | Author |
|---|---|---|---|
|      |         |             |        |

## Requirements

### Functional Requirements

**Simulation Runner**
*   **FR1:** The system shall read a configuration file to determine the simulation setup. `[COMPLETED]`
*   **FR2:** The system shall dynamically generate the necessary `docker-compose` configuration based on the experiment configuration. `[COMPLETED]`
*   **FR3:** The system shall use `docker-compose` to launch the HELICS broker and the correct number of instances of each federate. `[COMPLETED]`
*   **FR4:** The system shall use `docker-compose` to shut down all components at the end of the simulation. `[COMPLETED]`

**Grid Simulator**
*   **FR5:** The Grid Simulator shall load a grid model from a file. `[PARTIAL]`
*   **FR6:** The Grid Simulator shall create HELICS subscriptions for active (P) and reactive (Q) power at each node in the grid, allowing other federates to connect. `[PARTIAL]`

**House Simulator**
*   **FR7:** The system shall provide a simple house simulator that reads a power demand profile from a file. `[COMPLETED]`
*   **FR8:** The system shall provide a detailed, physics-based house simulator that models components like heat pumps. (Note: The specific inputs for this model are TBD and depend on another team). `[NEW]`
*   **FR9:** Both simple and detailed house simulators must publish their P and Q power demand to the grid via HELICS. `[PARTIAL]`

**Co-simulation Bus**
*   **FR10:** The HELICS co-simulation bus must be configured to accept remote connections to allow for future Hardware-in-the-Loop (HIL) integration. `[PARTIAL]`

**Configuration**
*   **FR11:** The main configuration file shall allow a user to specify: `[PARTIAL]`
    *   The types and number of federates.
    *   The mapping of federates to grid nodes.
    *   The grid model file to be used.
    *   The load profile files for the simple house models.
    *   The total simulation time.

**Results/Output**
*   **FR12:** The system shall include a 'recorder' component to capture and store time-series data from the simulation. `[COMPLETED]`
*   **FR13:** The recorder component shall be configurable, allowing the user to specify which data points to record (e.g., node voltages, transformer power flow). `[COMPLETED]`

### Non-Functional Requirements

*   **NFR1:** The system shall be operating system neutral, with the ability to run on Linux, Windows, and macOS. `[COMPLETED]`
*   **NFR2:** All new custom code in the project must be covered by a comprehensive suite of automated tests, with a target of >80% code coverage. `[NEW]`
*   **NFR3:** All documentation must be updated to reflect the latest changes in the system. `[PARTIAL]`
*   **NFR4:** The simulation speed shall be comparable to the `urbs` optimization model. `[NEW]`
*   **NFR5:** The system shall be scalable to handle up to 100,000 houses. `[NEW]`
*   **NFR6:** A full-year simulation with 10% detailed house models shall complete within one hour. `[NEW]`

### Post-MVP Functional Requirements

*   **FR14:** The system shall provide a Graphical User Interface (GUI) to allow users to control, inspect, and retrieve the results of simulations. `[NEW]`

## Technical Assumptions

*   **Repository Structure**: Monorepo. (Note: A strategy for integrating another team's repository will be needed in the future, which may present a challenge.)
*   **Service Architecture**: The system is based on a modular, service-oriented architecture. This is a key product decision to ensure flexibility (allowing components to be swapped) and long-term maintainability, especially with rotating teams.
*   **Testing Requirements**: The project will aim for a comprehensive test suite (Full Testing Pyramid). The implementation will be prioritized, likely starting with a strong foundation of unit and integration tests.
*   **Additional Technical Assumptions and Requests**:
    *   **Languages/Frameworks**: The primary language is Python. Key libraries include `pandapower`, `helics`, and `omegaconf`.
    *   **Infrastructure**: The system is built on Docker and Docker Compose.
    *   **Database (MVP)**: For the MVP, simulation results will be stored in flat files only. The implementation of a database for results storage is a post-MVP goal.

## Epic List

*   **Epic 1: Foundational Simulation Framework.**
    *   **Goal:** Establish the core, configurable simulation framework, **with a solid foundation of unit and integration tests**, that can be executed from the command line, produces results, and is OS neutral.

*   **Epic 2: Advanced Simulation Capabilities.**
    *   **Goal:** Enhance the framework with support for a detailed house model and performance/scalability improvements, **expanding the test suite to cover all new functionality.**

*   **Epic 3: Full Test Automation and Documentation.**
    *   **Goal:** Achieve a comprehensive, fully automated test suite (including end-to-end tests) and complete, public-facing user and developer documentation.

*   **Epic 4 (Post-MVP): Graphical User Interface.**
    *   **Goal:** Develop a user-friendly graphical interface, **with its own dedicated test suite.**

## Epic 1 Details: Foundational Simulation Framework

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