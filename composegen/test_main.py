import os
import yaml
import json
import pytest
from omegaconf import OmegaConf
from main import (
    create_docker_compose,
    create_grid_config,
    create_helics_runner_configs,
    main,
    safe_eval,
)
import pandas as pd

MINIMAL_CONF = {
    "general": {"time_step": 1, "start_time": 0, "end_time": 10},
    "federates": {
        "broker": {"name": "broker", "build_folder": "broker", "command": "run-broker"},
        "grid": {
            "name": "grid",
            "build_folder": "grid",
            "command": "run-grid",
            "grid_file": "test.xlsx",
        },
        "house": {
            "name": "house",
            "build_folder": "house",
            "command": "run-house",
            "num_houses": 1,
            "input_file": "/data/input/sample.csv",
        },
        "recorder": {
            "name": "recorder",
            "build_folder": "recorder",
            "command": "run-recorder",
            "target": "grid",
            "output_file": "/data/output/grid.log",
        },
    },
}


def test_create_docker_compose(tmp_path):
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        compose = yaml.safe_load(f)
    # Should have one service per federate CLASS (not per instance)
    services = compose["services"]
    assert set(services.keys()) == {"broker", "grid", "house", "recorder"}
    # Check a field for one service
    assert services["grid"]["container_name"] == "grid"
    # Check that services use helics_runner
    assert "helics_runner" in services["grid"]["command"]
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
    df = pd.DataFrame({"bus": [1, 2, 3, 4, 5], "p_mw": [0, 0, 0, 0, 0]})
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
    df = pd.DataFrame({"foo": [1, 2, 3]})
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


def test_create_helics_runner_configs(tmp_path):
    """Test generation of separate helics_runner configs per federate class."""
    conf = OmegaConf.create(MINIMAL_CONF)
    
    output_dir = tmp_path / "out"
    os.makedirs(output_dir, exist_ok=True)
    
    configs = create_helics_runner_configs(conf, str(output_dir))
    
    # Should create 4 config files (broker, grid, house, recorder)
    assert len(configs) == 4
    
    # Check broker config
    broker_path = output_dir / "helics_runner_broker.json"
    assert broker_path.exists()
    with open(broker_path) as f:
        broker_conf = json.load(f)
    assert broker_conf["broker"] is True
    assert "broker_args" in broker_conf
    
    # Check grid config
    grid_path = output_dir / "helics_runner_grid.json"
    assert grid_path.exists()
    with open(grid_path) as f:
        grid_conf = json.load(f)
    assert grid_conf["broker"] is False
    assert len(grid_conf["federates"]) == 1
    assert "python main.py" in grid_conf["federates"][0]["exec"]
    
    # Check house config
    house_path = output_dir / "helics_runner_house.json"
    assert house_path.exists()
    with open(house_path) as f:
        house_conf = json.load(f)
    assert house_conf["broker"] is False
    assert len(house_conf["federates"]) == 1
    assert "helics_player" in house_conf["federates"][0]["exec"]
    assert house_conf["federates"][0]["count"] == 1
    
    # Check recorder config
    recorder_path = output_dir / "helics_runner_recorder.json"
    assert recorder_path.exists()
    with open(recorder_path) as f:
        recorder_conf = json.load(f)
    assert recorder_conf["broker"] is False
    assert "helics_recorder" in recorder_conf["federates"][0]["exec"]


def test_main_integration(tmp_path):
    """Test that main() generates separate helics_runner configs and docker-compose."""
    # Write config to file
    config_path = tmp_path / "experiment.yml"
    with open(config_path, "w") as f:
        yaml.safe_dump(MINIMAL_CONF, f)
    output_dir = tmp_path / "out"
    os.makedirs(output_dir, exist_ok=True)
    
    main(str(config_path), str(output_dir))
    
    # Check separate helics_runner config files were created
    broker_path = output_dir / "helics_runner_broker.json"
    assert broker_path.exists()
    
    grid_path = output_dir / "helics_runner_grid.json"
    assert grid_path.exists()
    
    house_path = output_dir / "helics_runner_house.json"
    assert house_path.exists()
    
    recorder_path = output_dir / "helics_runner_recorder.json"
    assert recorder_path.exists()
    
    # Check docker-compose.yml has one service per federate class
    compose_path = output_dir / "docker-compose.yml"
    assert compose_path.exists()
    with open(compose_path) as f:
        compose = yaml.safe_load(f)
    assert set(compose["services"].keys()) == {"broker", "grid", "house", "recorder"}
    
    # Check grid config
    grid_config_path = tmp_path / "helics_grid_config.json"
    assert grid_config_path.exists()
    with open(grid_config_path) as f:
        grid_conf = json.load(f)
    assert grid_conf["name"] == "grid"


def test_safe_eval_valid():
    """Test safe_eval with valid expressions."""
    assert safe_eval("5 + 3") == 8
    assert safe_eval("10 - 2") == 8
    assert safe_eval("4 * 2") == 8
    assert safe_eval("16 / 2") == 8
    assert safe_eval("(5 + 3) * 2") == 16
    assert safe_eval("13+2") == 15  # Used in experiment.yml


def test_safe_eval_invalid():
    """Test safe_eval rejects dangerous expressions."""
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('ls')")
    with pytest.raises(ValueError):
        safe_eval("open('/etc/passwd')")
    with pytest.raises(ValueError):
        safe_eval("exec('print(1)')")
    with pytest.raises(ValueError):
        safe_eval("eval('1+1')")
