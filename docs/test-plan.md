# Test Plan: GridLock

## 1. Introduction

This document outlines the high-level testing strategy for the GridLock project. The goal of this plan is to ensure that we build a high-quality, reliable, and maintainable simulation framework. This is a living document and will be updated as the project evolves.

## 2. Overall Testing Strategy

Our testing strategy is based on the principle of continuous testing and building quality in at every stage of the development process. We will follow a risk-based approach, focusing our testing efforts on the most critical and complex parts of the system.

Each new feature or piece of functionality will be delivered with its own set of automated tests, as defined in the user stories.

## 3. Testing Tools

The following tools will be used for testing:

*   **Test Framework:** `pytest` will be used as the primary framework for writing and running all automated tests.
*   **Code Coverage:** The `pytest-cov` plugin will be used to measure code coverage.
*   **Mocking:** The `pytest-mock` plugin will be used for creating mocks and stubs in unit tests. It provides a convenient `mocker` fixture.
*   **CI/CD:** Automated tests will be run in a Continuous Integration environment (e.g., GitHub Actions) on every commit.

## 4. Levels of Testing

We will employ a multi-layered testing approach, based on the "Testing Pyramid" concept.

### 4.1. Unit Tests

*   **Goal:** To test individual functions, methods, and classes in isolation.
*   **Scope:** All new business logic, utility functions, and data transformation code will be covered by unit tests.
*   **Responsibility:** Developers will be responsible for writing unit tests for the code they create.

### 4.2. Integration Tests

*   **Goal:** To test the interaction between different components of the system.
*   **Scope:** We will create integration tests for key workflows, such as:
    *   Verifying that federates can correctly join a co-simulation.
    *   Verifying that data is correctly passed between federates (e.g., from a House Simulator to the Grid Simulator).
    *   Verifying that the Recorder correctly captures data from a simulation.
*   **Strategy:** For Epic 1, we will focus on a small number of high-value integration "smoke tests". We will expand the integration test suite in later epics.

### 4.3. End-to-End (E2E) Tests

*   **Goal:** To test the entire system from start to finish, simulating a real user scenario.
*   **Scope:** An E2E test will involve running a complete simulation with a sample `experiment.yml` and verifying that the final results are correct.
*   **Strategy:** The development of a comprehensive, automated E2E test suite will be the primary focus of Epic 3.

### 4.4. Test Location and Execution

*   **Unit Tests:**
    *   **Location:** Unit tests will be located alongside the code they are testing (e.g., in a `tests/` subdirectory within each component's own directory).
    *   **Execution:** Unit tests will be run within a dedicated 'test' stage in each component's Dockerfile. By default, building the container will skip the test stage to ensure fast local build times for developers. The tests can be run on demand by explicitly targeting the 'test' stage during the build. The Continuous Integration (CI) server will always run the test stage.

*   **Integration and E2E Tests:**
    *   **Location:** Integration and End-to-End tests will be located in a dedicated, top-level `tests/` directory.
    *   **Execution:** These tests will be run from a separate 'testing container'. This container will be responsible for orchestrating the `docker-compose` environment and running the tests against the live services. It will also provide a command to trigger all of the individual component unit tests, providing a single entrypoint to run the entire project test suite.

## 5. Quality Metrics & Reporting

The following metrics will be used to track the quality of the project:

*   **Code Coverage:** We will aim for a target of >80% code coverage for all new code. This will be reported automatically by the CI system.
*   **Test Pass/Fail Rate:** The results of the automated test suite will be reported on every commit. A failing test will block the merging of a pull request.
*   **Bug Reports:** We will track the number of bugs reported by users and categorize them by severity.
