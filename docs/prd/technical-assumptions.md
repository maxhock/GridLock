# Technical Assumptions

*   **Repository Structure**: Monorepo. (Note: A strategy for integrating another team's repository will be needed in the future, which may present a challenge.)
*   **Service Architecture**: The system is based on a modular, service-oriented architecture. This is a key product decision to ensure flexibility (allowing components to be swapped) and long-term maintainability, especially with rotating teams.
*   **Testing Requirements**: The project will aim for a comprehensive test suite (Full Testing Pyramid). The implementation will be prioritized, likely starting with a strong foundation of unit and integration tests.
*   **Additional Technical Assumptions and Requests**:
    *   **Languages/Frameworks**: The primary language is Python. Key libraries include `pandapower`, `helics`, and `omegaconf`.
    *   **Infrastructure**: The system is built on Docker and Docker Compose.
    *   **Database (MVP)**: For the MVP, simulation results will be stored in flat files only. The implementation of a database for results storage is a post-MVP goal.
