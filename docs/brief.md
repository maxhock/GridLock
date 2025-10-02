# Project Brief: GridLock

## Executive Summary

GridLock is a Docker-based co-simulation framework designed for integrated power grid and residential energy simulations. It utilizes pandapower for Optimal Power Flow (OPF) and detailed house modeling, featuring a unique capability for transparent Hardware-in-the-Loop (HIL) integration from our lab. As a core part of a project to estimate the effect of Home Energy Management Systems (HEMS) on grid expansion, GridLock aims to demonstrate cost reduction and investment deferment in grid infrastructure.

The primary users are researchers testing various scenarios with or without hardware. The key benefit of the framework is enabling a seamless transition from pure simulation to HIL testing within a single environment, eliminating the need to transfer models or data between different tools.

## Problem Statement

Currently, researchers face significant inefficiencies when combining power grid simulations with Hardware-in-the-Loop (HIL) testing. The workflow requires a complete simulation to be developed in one tool, followed by the manual creation of an entirely new setup for HIL testing. This separation is time-consuming, error-prone, and creates a knowledge silo, demanding expertise in disparate environments like Python for simulation and MATLAB/Simulink for HIL.

Existing solutions are inadequate because our lab is a custom-built environment. We need the flexibility to integrate best-in-class tools for each simulation domain and leverage modern data processing and machine learning libraries from the Python ecosystem, which monolithic commercial tools do not allow. The development of an integrated framework is time-sensitive, as it is a critical requirement for an ongoing scientific project.

## Proposed Solution

The core concept of GridLock is a modular co-simulation platform where each class of simulation (e.g., grid model, house models) operates within its own containerized environment. These simulations are interconnected using HELICS, which manages standardized interfaces, message exchange, and time synchronization. This architecture allows any simulation component to be swapped out with an alternative tool as needed.

The key differentiator is the framework's high degree of flexibility, accommodating a wide variety of simulation tools and enabling seamless interaction between simulated and Hardware-in-the-Loop (HIL) components. Its decoupled structure ensures long-term maintenance is viable, even with rotating teams of students.

The ultimate vision for GridLock is to be the one-stop simulation platform for the chair, supporting research ranging from the impact of DERs and Vehicle-to-Grid, to HEMS algorithm validation and full HIL experiment verification.

## Target Users

### Primary User Segment: PhD & Research Students

*   **Profile:** The primary users are PhD students from diverse backgrounds (Electrical Engineering, Informatics, Physics, Mechanical Engineering) with varied programming skills.
*   **Goals:** They aim to assess the impact of policy changes or new technologies on the power grid, and to validate control algorithms on real hardware for their research and publications.
*   **Pain Points:** Their biggest challenge is the current divide in skillsets required for simulation and HIL testing, which prevents a single researcher from managing a project end-to-end and creates knowledge silos.

### Secondary User Segment: Visiting Researchers & Guests

*   **Profile:** This group includes visiting academics, industry partners, and other guests.
*   **Goals:** They need to quickly familiarize themselves with the lab's capabilities before a visit or see a demonstration of its work. The tool serves as an effective demonstrator.

## Goals & Success Metrics

### Business Objectives

*   **Secure Funding:** Leverage GridLock as a core technological asset in the next major research grant proposal.
*   **Increase Research Output:** Enable the publication of at least 3 scientific papers that rely on the framework's capabilities within the next 3 years.

### User Success Metrics

*   **Reduced Onboarding Time:** A new researcher can independently set up and run a baseline simulation-to-HIL experiment within one week of being introduced to the tool.
*   **Enable Autonomous Research:** A single researcher can manage the full project lifecycle from pure simulation to HIL validation, eliminating the need for a dedicated HIL specialist for setup and execution.

### Key Performance Indicators (KPIs)

*   Time to first successful simulation run.
*   Time to first successful Hardware-in-the-Loop (HIL) run.
*   Total number of simulation runs per month.
*   User satisfaction score (via internal survey).

## MVP Scope

This is a brownfield project, and the initial MVP is focused on delivering a core co-simulation workflow.

### Core Features (Must Have)

*   **Grid Simulation:** Integration of a `pandapower` simulation for grid modeling.
*   **Basic House Simulation:** A simple house simulator capable of replaying load profiles from CSV files.
*   **Simplified Configuration:** The ability to configure a complete simulation scenario (e.g., selecting the grid, and distribution of houses) via a single configuration file.

### Out of Scope for MVP

*   **Detailed House Simulation:** A complex, physics-based house model is not required for the MVP and is being handled by another team.
*   **Hardware-in-the-Loop (HIL) Integration:** HIL capabilities are not a priority for the initial MVP.

### MVP Success Criteria

The MVP will be considered successful when a researcher can select a grid file and have all grid connections populated with CSV-based house simulations, with the ability to specify which of two house simulation types is attached to each node.

## Post-MVP Vision

### Phase 2 Features

*   **Detailed House Model Integration:** Integrate the advanced, physics-based house simulation model being developed by the external team.
*   **GUI for Visualization & Interaction:** Develop a graphical user interface to visualize the simulation as it runs and potentially allow for real-time interaction with simulation parameters.

### Long-term Vision

The long-term vision is for GridLock to become the one-stop shop for all simulation needs at the chair, supporting everything from testing the effects of DERs and Vehicle-to-Grid, to HEMS algorithm validation and, eventually, full HIL validation.

### Expansion Opportunities

*   **Multi-Energy Grids:** Expand the framework to include other energy vectors, such as integrating heat grid simulations alongside the electrical grid.
*   **External Collaboration:** The tool could potentially be licensed or shared with other universities or commercial research partners.

## Technical Considerations

### Platform Requirements

*   **Target Platforms:** The framework must run on Linux, Windows, and macOS. Docker will be the primary mechanism to ensure cross-platform compatibility.
*   **Performance Requirements:**
    *   **Benchmark:** The simulation speed should be, at a minimum, on par with the `urbs` optimization model.
    *   **Scalability:** Must remain performant with up to 100,000 houses.
    *   **Target Speed:** A full-year simulation should complete in one hour or less, even with 10% of houses running detailed models.

### Technology Preferences

*   **Backend:** The core backend is built on Python, utilizing `pandapower`, `helics` for co-simulation, and `omegaconf` for configuration.
*   **Frontend:** The initial plan for the Phase 2 GUI is to leverage and extend the GUI already integrated with HELICS.
*   **Database:** Simulation results should be stored in both a database and flat files. The specific database technology is TBD.
*   **Infrastructure:** The system uses Docker and Docker Compose. The Compose file is dynamically generated from a custom YAML configuration processed by `omegaconf`.

### Architecture Considerations

*   **Repository Structure:** The project is currently in a single repository (monorepo), which is sufficient for now. A strategy for integrating the other team's repository will be needed in the future.
*   **Integration Requirements:** No external system integrations are required at this stage.
*   **Security:** Standard security measures such as HTTPS for the GUI and leveraging HELICS's built-in encryption are considered sufficient for now.

## Constraints & Assumptions

### Constraints

*   **Timeline:** The MVP must be completed within 3 months (by end of year). The final version should be ready in approximately 9 months.
*   **Resources:** The project is currently resourced with one person working 20-25 hours per week.
*   **Budget:** There is no specific budget allocated for this project..
*   **Technical Constraints:** HIL components must run on a dedicated PC with exclusive access to lab hardware.

### Key Assumptions

*   The other team will deliver the detailed house simulation on schedule for Phase 2.
*   Existing software licenses are sufficient for all planned work.
*   Docker will adequately handle all cross-platform compatibility issues.
*   The performance of the containerized HELICS co-simulation architecture will be sufficient to meet the project's performance targets.

## Risks & Open Questions

### Key Risks

*   **Data Dependency Risk:** The final version requires a specific grid model from a project partner. The student responsible has not yet received this data and will be unavailable for 5 weeks, posing a significant threat to the timeline.
*   **Integration Risk:** Unforeseen challenges may arise when integrating the external team's detailed house simulation in Phase 2.
*   **Performance Risk:** The containerized HELICS architecture may not meet performance targets, potentially forcing a major redesign.
*   **Resource Risk:** As a solo project, any competing priorities could jeopardize the tight 3-month MVP deadline.

### Open Questions

*   What is the optimal long-term strategy for configuration management as the project complexity grows?

## Appendices

### C. References

*   [HELICS Documentation](https://docs.helics.org/)
*   [PandaPower Documentation](https://pandapower.readthedocs.io/en/latest/)


## Next Steps

### Immediate Actions

1.  Continue with the creation of tests.

### PM Handoff

This Project Brief provides the full context for GridLock. Please start in 'PRD Generation Mode', review the brief thoroughly to work with the user to create the PRD section by section as the template indicates, asking for any necessary clarification or suggesting improvements.
