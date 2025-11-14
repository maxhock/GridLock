import sys
import pytest
from unittest.mock import mock_open
import main as grid_pypsa_main


# Auto-mock helics, pypsa, and cosim_toolbox modules for all tests
@pytest.fixture(autouse=True)
def patch_modules(mocker):
    import sys

    modules_patch = {
        "helics": mocker.MagicMock(),
        "pypsa": mocker.MagicMock(),
        "cosim_toolbox": mocker.MagicMock(),
        "cosim_toolbox.sims": mocker.MagicMock(),
    }
    mocker.patch.dict(sys.modules, modules_patch)
    yield


def test_create_federate(mocker, tmp_path):
    """Unit: GridPyPSAFederate.create_federate loads config and initializes correctly."""
    # Mock file system for config file
    config_data = '{"period": 3600, "max_cosim_duration": 82800}'
    mocker.patch("builtins.open", mock_open(read_data=config_data))

    # Mock PyPSA network loading
    mock_network = mocker.MagicMock()
    mock_network.loads.index = [0, 1, 2]
    mock_network.generators.index = [0]
    mocker.patch.object(
        grid_pypsa_main, "load_pypsa_from_pandapower_excel", return_value=mock_network
    )

    # Mock HELICS registration
    mock_sub = mocker.MagicMock()
    mock_pub = mocker.MagicMock()
    mocker.patch.object(
        grid_pypsa_main.h, "helicsFederateRegisterSubscription", return_value=mock_sub
    )
    mocker.patch.object(
        grid_pypsa_main.h,
        "helicsFederateRegisterGlobalPublication",
        return_value=mock_pub,
    )

    # Create federate and call create_federate()
    federate = grid_pypsa_main.GridPyPSAFederate("grid", "/data/input/test.xlsx")

    # Mock the inherited create_helics_fed method
    federate.create_helics_fed = mocker.MagicMock()
    federate.hfed = mocker.MagicMock()

    federate.create_federate()

    # Verify PyPSA network loaded
    assert federate.network is mock_network

    # Verify dynamic subscriptions registered for all loads
    assert len(federate.load_indices) == 3
    assert grid_pypsa_main.h.helicsFederateRegisterSubscription.call_count == 3

    # Verify dynamic publications registered for generators
    assert len(federate.ext_grid_indices) == 1
    assert (
        grid_pypsa_main.h.helicsFederateRegisterGlobalPublication.call_count == 1
    )


def test_update_internal_model(mocker):
    """Unit: GridPyPSAFederate.update_internal_model processes data and runs power flow."""
    # Setup mock network with proper pandas .at behavior
    mock_network = mocker.MagicMock()

    # Mock loads.at to allow setting values
    mock_loads_at = mocker.MagicMock()
    mock_loads_at.__setitem__ = mocker.MagicMock()
    mock_network.loads.at = mock_loads_at

    # Mock generators_t.p to return power value
    mock_gen_p = mocker.MagicMock()
    mock_gen_p.__getitem__ = mocker.MagicMock(
        return_value=mocker.MagicMock(iloc=mocker.MagicMock(__getitem__=lambda x: 1.5))
    )
    mock_network.generators_t.p = mock_gen_p

    # Create federate instance
    federate = grid_pypsa_main.GridPyPSAFederate("grid", "/data/input/test.xlsx")
    federate.network = mock_network
    federate.load_indices = [0, 1]
    federate.ext_grid_indices = [0]
    federate.granted_time = 3600

    # Mock input data from federation
    federate.data_from_federation = {
        "inputs": {
            "node_0/P": 1.0,
            "node_1/P": 2.0,
        }
    }
    federate.data_to_federation = {"publications": {}}

    # Mock pypsa power flow
    mock_network.pf = mocker.MagicMock(return_value=None)

    # Run update
    federate.update_internal_model()

    # Verify power flow was called
    mock_network.pf.assert_called_once()

    # Verify publication was set
    assert (
        "Grid/transformer_0_power" in federate.data_to_federation["publications"]
    )
    assert federate.data_to_federation["publications"]["Grid/transformer_0_power"] == 1.5


def test_load_pypsa_from_pandapower_excel(mocker):
    """Unit: load_pypsa_from_pandapower_excel converts pandapower Excel to PyPSA network."""
    import pandas as pd

    # Mock pandas read_excel
    mock_buses = pd.DataFrame({"vn_kv": [20.0, 0.4]}, index=[0, 1])
    mock_loads = pd.DataFrame({"bus": [1, 1], "p_mw": [1.0, 2.0]}, index=[0, 1])
    mock_ext_grid = pd.DataFrame({"bus": [0]}, index=[0])
    mock_lines = pd.DataFrame(
        {
            "from_bus": [0],
            "to_bus": [1],
            "r_ohm_per_km": [0.1],
            "x_ohm_per_km": [0.2],
            "length_km": [1.0],
            "max_i_ka": [0.5],
            "df": [1.0],
        },
        index=[0],
    )

    def read_excel_side_effect(path, sheet_name):
        sheets = {
            "bus": mock_buses,
            "load": mock_loads,
            "ext_grid": mock_ext_grid,
            "line": mock_lines,
        }
        if sheet_name == "trafo":
            raise Exception("No trafo sheet")
        return sheets[sheet_name]

    mocker.patch("pandas.read_excel", side_effect=read_excel_side_effect)

    # Mock PyPSA Network
    mock_network = mocker.MagicMock()
    mocker.patch.object(grid_pypsa_main.pypsa, "Network", return_value=mock_network)
    mocker.patch.object(grid_pypsa_main.pd, "Timestamp", return_value="2025-01-01")

    # Call function
    result = grid_pypsa_main.load_pypsa_from_pandapower_excel("/data/input/test.xlsx")

    # Verify network was created
    assert result is mock_network

    # Verify components were added (2 buses, 2 loads, 1 generator, 1 line)
    assert mock_network.add.call_count >= 6

    # Verify snapshots were set
    mock_network.set_snapshots.assert_called_once()


def test_main_runs(mocker):
    """Unit: main orchestrates the federate lifecycle without error."""
    # Mock GridPyPSAFederate methods
    mock_federate = mocker.MagicMock()
    mocker.patch.object(
        grid_pypsa_main, "GridPyPSAFederate", return_value=mock_federate
    )

    # Mock sys.argv for argument parsing
    mocker.patch.object(sys, "argv", ["main.py", "--grid_file", "test.xlsx"])

    grid_pypsa_main.main()

    # Verify federate lifecycle methods called
    mock_federate.create_federate.assert_called_once()
    mock_federate.run_cosim_loop.assert_called_once()
    mock_federate.destroy_federate.assert_called_once()


def test_parse_args(monkeypatch):
    test_args = ["main.py", "--grid_file", "test.xlsx"]
    monkeypatch.setattr(sys, "argv", test_args)
    args = grid_pypsa_main.parse_args()
    assert args.grid_file == "test.xlsx"
