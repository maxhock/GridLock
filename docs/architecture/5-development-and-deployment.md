# 5. Development and Deployment

## 5.1. Local Development Setup

The development workflow is centered around the `composegen` script.

1.  Modify or create an `experiment.yml` in the `/config` directory to define the simulation parameters (number of houses, grid type, etc.).
2.  Run the `composegen/main.py` script. This generates the `docker-compose.yml` in `config/tmp/`.
3.  Run `docker-compose -f config/tmp/docker-compose.yml up` to start the simulation.

## 5.2. Build and Deployment Process

- **Build**: The build process is managed by Docker Compose, which builds the image for each service based on the `build` path specified in the generated `docker-compose.yml`.
- **Deployment**: This is a local simulation framework; "deployment" consists of running the Docker Compose command.
