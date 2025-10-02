# Contributing to GridLock

This document outlines the basic development process and conventions for the GridLock project.

## Development Workflow

All new features, bug fixes, or other changes should be developed in a separate feature branch. The `main` branch should be kept in a stable, working state at all times.

When a feature is complete, please open a Pull Request (PR) to merge the feature branch into `main`.

## Rollback Strategy

While this project is a local simulation tool without live deployments, we still need a clear process for rolling back changes that introduce problems.

Our rollback strategy is as follows:

1.  **Primary Mechanism:** If a recently merged PR is found to cause a critical issue (e.g., breaking the main simulation workflow, causing significant performance degradation), the primary rollback mechanism is to use the **'Revert'** feature on the Pull Request in GitHub.

2.  **Developer Responsibility:** The developer who performs the revert is also responsible for ensuring that any associated changes are rolled back as well. This includes, but is not limited to:
    *   Changes to the format of configuration files (e.g., `experiment.yml`).
    *   Changes to the format or content of input data files.
    *   Changes to the Docker environment (`Dockerfile`, `docker-compose` generation logic).

By following this process, we can quickly recover from any problematic changes and maintain the stability of the `main` branch.
