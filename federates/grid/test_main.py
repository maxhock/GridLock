"""Tests for the grid federate main module.

Tests cover CLI parsing, metadata-based net loading, the
federate lifecycle wrapper, and the main entry point.
"""

import json
import sys
import pytest
from unittest.mock import MagicMock
import main as grid_main


# Auto-mock helics, pandapower, and cosim_toolbox modules for all tests
@pytest.fixture(autouse=True)
def patch_modules(mocker):
    modules_patch = {
        "helics": mocker.MagicMock(),
        "pandapower": mocker.MagicMock(),
        "pandapower.networks": mocker.MagicMock(),
        "cosim_toolbox": mocker.MagicMock(),
        "cosim_toolbox.sims": mocker.MagicMock(),
        "cosim_toolbox.dbms": mocker.MagicMock(),
    }
    mocker.patch.dict(sys.modules, modules_patch)
    yield


def test_parse_args(monkeypatch):
    """Unit: parse_args extracts --scenario and --federate_name from CLI."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--scenario", "TestGridScenario", "--federate_name", "lv-grid_91301_0"],
    )
    args = grid_main.parse_args()
    assert args.scenario == "TestGridScenario"
    assert args.federate_name == "lv-grid_91301_0"


def test_load_net_from_metadata(mocker):
    """Unit: load_net_from_metadata reads from CST metadata store."""
    mock_net = mocker.MagicMock()
    mock_net.bus = [0, 1, 2]
    mock_net.load = [0]

    mock_md_mgr = mocker.MagicMock()
    mock_md_mgr.read.return_value = {"net_json": '{"_module": "pandapower"}'}

    mock_create_mm = mocker.patch(
        "main.create_metadata_manager", return_value=mock_md_mgr
    )
    mocker.patch("main.pp.from_json_string", return_value=mock_net)

    result = grid_main.load_net_from_metadata("lv-grid_91301_0")

    mock_create_mm.assert_called_once_with(backend="json", location="meta_store")
    mock_md_mgr.connect.assert_called_once()
    mock_md_mgr.read.assert_called_once_with("grid_data", "lv-grid_91301_0")
    mock_md_mgr.disconnect.assert_called_once()
    assert result == mock_net


def test_load_net_from_metadata_not_found(mocker):
    """Unit: load_net_from_metadata raises FileNotFoundError if no data."""
    mock_md_mgr = mocker.MagicMock()
    mock_md_mgr.read.return_value = None

    mocker.patch("main.create_metadata_manager", return_value=mock_md_mgr)

    with pytest.raises(FileNotFoundError, match="No grid data found"):
        grid_main.load_net_from_metadata("nonexistent_grid")

    mock_md_mgr.disconnect.assert_called_once()


def test_run_grid_federate(mocker):
    """Unit: run_grid_federate calls CST lifecycle correctly."""
    mock_federate = mocker.MagicMock()
    mocker.patch("main.GridFederate", return_value=mock_federate)

    mock_net = mocker.MagicMock()
    grid_main.run_grid_federate("lv-grid_91301_0", mock_net, "TestGridScenario")

    grid_main.GridFederate.assert_called_once_with("lv-grid_91301_0", mock_net)
    mock_federate.create_federate.assert_called_once_with(
        scenario_name="TestGridScenario",
    )
    mock_federate.run_cosim_loop.assert_called_once()
    mock_federate.destroy_federate.assert_called_once()


def test_run_grid_federate_calls_destroy_on_error(mocker):
    """Unit: run_grid_federate calls destroy_federate even on exception."""
    mock_federate = mocker.MagicMock()
    mock_federate.run_cosim_loop.side_effect = RuntimeError("sim failed")
    mocker.patch("main.GridFederate", return_value=mock_federate)

    mock_net = mocker.MagicMock()
    with pytest.raises(RuntimeError, match="sim failed"):
        grid_main.run_grid_federate("lv-grid_91301_0", mock_net, "TestGridScenario")

    mock_federate.destroy_federate.assert_called_once()


def test_main_loads_from_metadata_and_runs(mocker):
    """Unit: main loads net from metadata store and runs federate."""
    mock_net = mocker.MagicMock()
    mocker.patch("main.load_net_from_metadata", return_value=mock_net)
    mocker.patch("main.run_grid_federate")

    grid_main.main(
        scenario_name="TestGridScenario",
        federate_name="lv-grid_91301_0",
    )

    grid_main.load_net_from_metadata.assert_called_once_with("lv-grid_91301_0")
    grid_main.run_grid_federate.assert_called_once_with(
        "lv-grid_91301_0", mock_net, "TestGridScenario"
    )


def test_update_internal_model(mocker):
    """Unit: GridFederate.update_internal_model processes data and runs power flow."""
    mock_net = mocker.MagicMock()

    mock_load_at = mocker.MagicMock()
    mock_load_at.__setitem__ = mocker.MagicMock()
    mock_net.load.at = mock_load_at

    mock_res_ext_grid_at = mocker.MagicMock()
    mock_res_ext_grid_at.__getitem__ = mocker.MagicMock(return_value=1.5)
    mock_net.res_ext_grid.at = mock_res_ext_grid_at

    federate = grid_main.GridFederate("grid", mock_net)
    federate.load_indices = [0, 1]
    federate.ext_grid_indices = [0]
    federate.granted_time = 3600

    federate.data_from_federation = {
        "inputs": {
            "house_0/house_load": 1000.0,
            "house_1/house_load": 2000.0,
        }
    }
    federate.data_to_federation = {"publications": {}}

    mocker.patch.object(grid_main.pp, "runpp", return_value=None)

    federate.update_internal_model()

    grid_main.pp.runpp.assert_called_once()
    assert "Grid/transformer_0_power" in federate.data_to_federation["publications"]
    assert federate.data_to_federation["publications"]["Grid/transformer_0_power"] == 1.5
