# Requirements

## Functional Requirements

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

## Non-Functional Requirements

*   **NFR1:** The system shall be operating system neutral, with the ability to run on Linux, Windows, and macOS. `[COMPLETED]`
*   **NFR2:** All new custom code in the project must be covered by a comprehensive suite of automated tests, with a target of >80% code coverage. `[NEW]`
*   **NFR3:** All documentation must be updated to reflect the latest changes in the system. `[PARTIAL]`
*   **NFR4:** The simulation speed shall be comparable to the `urbs` optimization model. `[NEW]`
*   **NFR5:** The system shall be scalable to handle up to 100,000 houses. `[NEW]`
*   **NFR6:** A full-year simulation with 10% detailed house models shall complete within one hour. `[NEW]`

## Post-MVP Functional Requirements

*   **FR14:** The system shall provide a Graphical User Interface (GUI) to allow users to control, inspect, and retrieve the results of simulations. `[NEW]`
