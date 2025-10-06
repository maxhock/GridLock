import os
import yaml
import json
import pytest
from omegaconf import OmegaConf
from main import create_docker_compose, create_grid_config, main

MINIMAL_CONF = {
    "general": {
        "time_step": 1,
        "start_time": 0,
        "end_time": 10
    },
    "federates": {
        "broker": {
            "name": "broker",
            "build_folder": "broker",
            "command": "run-broker"
        },
        "grid": {
            "name": "grid",
            "build_folder": "grid",
            "command": "run-grid"
        },
        "house": {
            "build_folder": "house",
            "command": "run-house",
            "num_houses": 1
        },
        "recorder": {
            "name": "recorder",
            "build_folder": "recorder",
            "command": "run-recorder"
        }
    }
}

def test_create_docker_compose(tmp_path):
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        compose = yaml.safe_load(f)
    # Should have broker, grid, house_0, recorder
    services = compose["services"]
    assert set(services.keys()) == {"broker", "grid", "house_0", "recorder"}
    # Check a field for one service
    assert services["grid"]["container_name"] == "grid"
    assert "helics-net" in compose["networks"]

def test_create_grid_config(tmp_path):
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "helics_grid_config.json"
    create_grid_config(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        grid_conf = json.load(f)
    assert grid_conf["name"] == "grid"
    assert grid_conf["period"] == 1
    assert grid_conf["offset"] == 0
    assert grid_conf["max_cosim_duration"] == 10
    assert grid_conf["broker"] == "broker"

def test_main_integration(tmp_path):
    # Write config to file
    config_path = tmp_path / "experiment.yml"
    with open(config_path, "w") as f:
        yaml.safe_dump(MINIMAL_CONF, f)
    output_dir = tmp_path / "out"
    os.makedirs(output_dir, exist_ok=True)
    main(str(config_path), str(output_dir))
    # Check docker-compose.yml
    compose_path = output_dir / "docker-compose.yml"
    assert compose_path.exists()
    with open(compose_path) as f:
        compose = yaml.safe_load(f)
    assert set(compose["services"].keys()) == {"broker", "grid", "house_0", "recorder"}
    # Check grid config
    grid_config_path = tmp_path / "helics_grid_config.json"
    assert grid_config_path.exists()
    with open(grid_config_path) as f:
        grid_conf = json.load(f)
    assert grid_conf["name"] == "grid"