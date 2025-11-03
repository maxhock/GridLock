import os
import yaml
import json
from omegaconf import OmegaConf
from main import (
    create_docker_compose,
    create_grid_config,
    create_broker_config,
    create_house_player_config,
    create_recorder_config,
    create_broker_runner,
    create_grid_runner,
    create_house_runner,
    create_recorder_runner,
    main,
)
import pandas as pd

MINIMAL_CONF = {
    "general": {"time_step": 1, "start_time": 0, "end_time": 10},
    "federates": {
        "broker": {"name": "broker", "build_folder": "broker"},
        "grid": {
            "name": "grid",
            "build_folder": "grid",
            "grid_file": "test.xlsx",
            "num_nodes": 3,
        },
        "house": {
            "build_folder": "house_player",
            "num_houses": 3,
            "input_file": "sample_house.csv",
        },
        "recorder": {
            "name": "recorder",
            "build_folder": "recorder",
            "target": "grid",
            "output_file": "/data/output/grid.log",
        },
    },
}


def test_create_docker_compose(tmp_path):
    """Test docker-compose.yml generation with 4-service architecture"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        compose = yaml.safe_load(f)
    # Should have broker, grid, house (not house_0), recorder
    services = compose["services"]
    assert set(services.keys()) == {"broker", "grid", "house", "recorder"}
    # Check container names
    assert services["grid"]["container_name"] == "grid"
    assert services["house"]["container_name"] == "house"
    # Check build contexts
    assert services["broker"]["build"] == "${PWD}/broker"
    assert services["grid"]["build"] == "${PWD}/grid"
    assert services["house"]["build"] == "${PWD}/house_player"
    assert services["recorder"]["build"] == "${PWD}/recorder"
    # Check network
    assert "helics-net" in compose["networks"]
    # Check dependencies
    assert services["grid"]["depends_on"] == ["broker"]
    assert services["house"]["depends_on"] == ["broker"]
    assert services["recorder"]["depends_on"] == ["broker"]


def test_create_broker_runner(tmp_path):
    """Test broker_runner.json generation with config reference"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "broker_runner.json"
    create_broker_runner(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        runner = json.load(f)
    assert runner["name"] == "broker_federation"
    assert len(runner["federates"]) == 1
    fed = runner["federates"][0]
    assert fed["name"] == "broker"
    # Should calculate 5 federates: 3 houses + 1 grid + 1 recorder
    assert "--federates=5" in fed["exec"]
    # Should reference config file
    assert "--config=/config/tmp/broker_config.json" in fed["exec"]


def test_create_grid_runner(tmp_path):
    """Test grid_runner.json generation"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "grid_runner.json"
    create_grid_runner(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        runner = json.load(f)
    assert runner["name"] == "grid_federation"
    assert len(runner["federates"]) == 1
    fed = runner["federates"][0]
    assert fed["name"] == "grid"
    assert fed["directory"] == "/app"
    assert "python main.py" in fed["exec"]
    assert "--broker=broker" in fed["exec"]
    assert "--grid_file=test.xlsx" in fed["exec"]


def test_create_house_runner(tmp_path):
    """Test house_runner.json generation with multiple instances and config reference"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "house_runner.json"
    create_house_runner(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        runner = json.load(f)
    assert runner["name"] == "house_federation"
    # Should have 3 house instances (uses grid.num_nodes)
    assert len(runner["federates"]) == 3
    for i, fed in enumerate(runner["federates"]):
        assert fed["name"] == f"house_{i}"
        assert fed["directory"] == "."
        assert "helics_player" in fed["exec"]
        assert "sample_house.csv" in fed["exec"]
        assert "--config=/config/tmp/house_player_config.json" in fed["exec"]
        assert "--local" in fed["exec"]
        assert f"--name=house_{i}" in fed["exec"]


def test_create_recorder_runner(tmp_path):
    """Test recorder_runner.json generation with config reference"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "recorder_runner.json"
    create_recorder_runner(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        runner = json.load(f)
    assert runner["name"] == "recorder_federation"
    assert len(runner["federates"]) == 1
    fed = runner["federates"][0]
    assert fed["name"] == "recorder"
    assert fed["directory"] == "."
    assert "helics_recorder" in fed["exec"]
    assert "--capture=grid" in fed["exec"]
    assert "--output=/data/output/grid.log" in fed["exec"]
    # Should reference config file
    assert "--config=/config/tmp/recorder_config.json" in fed["exec"]


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
    """Test that num_nodes is detected from Excel file load sheet"""
    # Create a mock Excel file with a 'load' sheet of 5 rows
    df = pd.DataFrame({"bus": [1, 2, 3, 4, 5], "p_mw": [0, 0, 0, 0, 0]})
    excel_filename = "test_grid.xlsx"

    # Create /data/input directory structure that composegen expects
    data_input_dir = tmp_path / "data" / "input"
    os.makedirs(data_input_dir, exist_ok=True)
    excel_path = data_input_dir / excel_filename
    with pd.ExcelWriter(excel_path) as writer:
        df.to_excel(writer, sheet_name="load", index=False)

    # Config with grid_file
    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = excel_filename

    # Monkeypatch os.path.join to redirect /data/input to our tmp_path
    original_join = os.path.join

    def custom_join(*args):
        if len(args) >= 3 and args[0] == "/data" and args[1] == "input":
            return str(data_input_dir / args[2])
        return original_join(*args)

    monkeypatch.setattr(os.path, "join", custom_join)

    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    # num_nodes should be set to 5
    assert conf["federates"]["grid"]["num_nodes"] == 5


def test_excel_missing_file_fallback(tmp_path, capsys):
    """Test that missing grid_file falls back to config num_nodes"""
    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = "nonexistent.xlsx"
    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    # Should fallback to config value (3), print warning
    captured = capsys.readouterr()
    assert "No valid grid_file found" in captured.out
    assert (
        conf["federates"]["grid"]["num_nodes"] == 3
    )  # Uses existing value from MINIMAL_CONF


def test_excel_missing_load_sheet(tmp_path, capsys, monkeypatch):
    """Test that Excel file without 'load' sheet shows warning"""
    # Excel file with no 'load' sheet
    df = pd.DataFrame({"foo": [1, 2, 3]})
    excel_filename = "test_grid.xlsx"

    data_input_dir = tmp_path / "data" / "input"
    os.makedirs(data_input_dir, exist_ok=True)
    excel_path = data_input_dir / excel_filename
    with pd.ExcelWriter(excel_path) as writer:
        df.to_excel(writer, sheet_name="notload", index=False)

    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = excel_filename

    # Monkeypatch os.path.join
    original_join = os.path.join

    def custom_join(*args):
        if len(args) >= 3 and args[0] == "/data" and args[1] == "input":
            return str(data_input_dir / args[2])
        return original_join(*args)

    monkeypatch.setattr(os.path, "join", custom_join)

    output_path = tmp_path / "docker-compose.yml"
    create_docker_compose(conf, str(output_path))
    captured = capsys.readouterr()
    assert "Could not read load sheet" in captured.out or "WARNING" in captured.out


def test_excel_empty_load_sheet(tmp_path, capsys, monkeypatch):
    """Test that empty load sheet is detected as 0 nodes"""
    # Excel file with empty 'load' sheet
    df = pd.DataFrame({})
    excel_filename = "test_grid.xlsx"

    data_input_dir = tmp_path / "data" / "input"
    os.makedirs(data_input_dir, exist_ok=True)
    excel_path = data_input_dir / excel_filename
    with pd.ExcelWriter(excel_path) as writer:
        df.to_excel(writer, sheet_name="load", index=False)

    conf = OmegaConf.create(MINIMAL_CONF)
    conf["federates"]["grid"]["grid_file"] = excel_filename

    # Monkeypatch os.path.join
    original_join = os.path.join

    def custom_join(*args):
        if len(args) >= 3 and args[0] == "/data" and args[1] == "input":
            return str(data_input_dir / args[2])
        return original_join(*args)

    monkeypatch.setattr(os.path, "join", custom_join)

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
    # Check docker-compose.yml - should have "house" not "house_0"
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


def test_create_broker_config(tmp_path):
    """Test broker config.json generation"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "broker_config.json"
    create_broker_config(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        broker_conf = json.load(f)
    assert broker_conf["name"] == "broker"
    assert broker_conf["loglevel"] == "debug"
    assert broker_conf["logfile"] == "/data/output/broker.log"
    assert broker_conf["ipv4"] is True


def test_create_house_player_config(tmp_path):
    """Test house_player config.json generation"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "house_player_config.json"
    create_house_player_config(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        house_conf = json.load(f)
    assert house_conf["loglevel"] == "warning"
    assert house_conf["coreType"] == "zmq"
    assert house_conf["broker"] == "broker"
    assert house_conf["uninterruptible"] is False
    assert house_conf["terminate_on_error"] is True


def test_create_recorder_config(tmp_path):
    """Test recorder config.json generation"""
    conf = OmegaConf.create(MINIMAL_CONF)
    output_path = tmp_path / "recorder_config.json"
    create_recorder_config(conf, str(output_path))
    assert output_path.exists()
    with open(output_path) as f:
        recorder_conf = json.load(f)
    assert recorder_conf["name"] == "recorder"
    assert recorder_conf["loglevel"] == "warning"
    assert recorder_conf["coreType"] == "zmq"
    assert recorder_conf["broker"] == "broker"
