import os
import yaml
import json
import pytest
from omegaconf import OmegaConf
from main import create_docker_compose, create_grid_config, main
import pandas as pd

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

def test_excel_node_counting(tmp_path, monkeypatch):
    # Create a mock Excel file with a 'load' sheet of 5 rows
    df = pd.DataFrame({'bus': [1,2,3,4,5], 'p_mw': [0,0,0,0,0]})
    excel_path = tmp_path / "test_grid.xlsx"
    with pd.ExcelWriter(excel_path) as writer:
        df.to_excel(writer, sheet_name="load", index=False)
    # Config with grid_file
    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = os.path.basename(excel_path)
    # Patch data/input to tmp_path
    monkeypatch.chdir(tmp_path)
    os.makedirs("data/input", exist_ok=True)
    os.rename(excel_path, f"data/input/{os.path.basename(excel_path)}")
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    # num_nodes should be set to 5
    assert conf["federates"]["grid"]["num_nodes"] == 5

def test_excel_missing_file_fallback(tmp_path, capsys):
    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = "nonexistent.xlsx"
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    # Should fallback to config value, print warning
    captured = capsys.readouterr()
    assert "No valid grid_file found" in captured.out

def test_excel_missing_load_sheet(tmp_path, capsys):
    # Excel file with no 'load' sheet
    df = pd.DataFrame({'foo': [1,2,3]})
    excel_path = tmp_path / "test_grid.xlsx"
    with pd.ExcelWriter(excel_path) as writer:
        df.to_excel(writer, sheet_name="notload", index=False)
    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = os.path.basename(excel_path)
    os.makedirs("data/input", exist_ok=True)
    os.rename(excel_path, f"data/input/{os.path.basename(excel_path)}")
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    captured = capsys.readouterr()
    assert "Could not read load sheet" in captured.out

def test_excel_empty_load_sheet(tmp_path, capsys):
    # Excel file with empty 'load' sheet
    df = pd.DataFrame({})
    excel_path = tmp_path / "test_grid.xlsx"
    with pd.ExcelWriter(excel_path) as writer:
        df.to_excel(writer, sheet_name="load", index=False)
    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = os.path.basename(excel_path)
    os.makedirs("data/input", exist_ok=True)
    os.rename(excel_path, f"data/input/{os.path.basename(excel_path)}")
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    captured = capsys.readouterr()
    assert "Detected 0 nodes" in captured.out
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