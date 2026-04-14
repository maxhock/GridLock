import sys
import pytest
from unittest.mock import mock_open
import main as grid_main


# Auto-mock helics, pandapower, and cosim_toolbox modules for all tests
@pytest.fixture(autouse=True)
def patch_modules(mocker):
    import sys

    modules_patch = {
        "helics": mocker.MagicMock(),
        "pandapower": mocker.MagicMock(),
        "pandapower.networks": mocker.MagicMock(),
        "cosim_toolbox": mocker.MagicMock(),
        "cosim_toolbox.sims": mocker.MagicMock(),
    }
    mocker.patch.dict(sys.modules, modules_patch)
    yield


def test_create_federate(mocker, tmp_path):
    """Unit: GridFederate.create_federate loads config and initializes correctly."""
    # Mock file system for config file
    config_data = '{"period": 3600, "max_cosim_duration": 82800}'
    mocker.patch("builtins.open", mock_open(read_data=config_data))

    # Mock pandapower network loading
    mock_net = mocker.MagicMock()
    mock_net.load.index = [0, 1, 2]
    mock_net.ext_grid.index = [0]
    mocker.patch.object(grid_main.pp, "from_excel", return_value=mock_net)

    # Mock HELICS registration
    mock_sub = mocker.MagicMock()
    mock_pub = mocker.MagicMock()
    mocker.patch.object(
        grid_main.h, "helicsFederateRegisterSubscription", return_value=mock_sub
    )
    mocker.patch.object(
        grid_main.h, "helicsFederateRegisterGlobalPublication", return_value=mock_pub
    )

    # Create federate and call create_federate()
    federate = grid_main.GridFederate("grid", "/data/input/test.xlsx")

    # Mock the inherited create_helics_fed method
    federate.create_helics_fed = mocker.MagicMock()
    federate.hfed = mocker.MagicMock()

    federate.create_federate()

    # Verify pandapower network loaded
    assert federate.net is mock_net

    # Verify dynamic subscriptions registered for all loads
    assert len(federate.load_indices) == 3
    assert grid_main.h.helicsFederateRegisterSubscription.call_count == 3

    # Verify dynamic publications registered for ext_grids
    assert len(federate.ext_grid_indices) == 1
    assert grid_main.h.helicsFederateRegisterGlobalPublication.call_count == 1


def test_update_internal_model(mocker):
    """Unit: GridFederate.update_internal_model processes data and runs power flow."""
    # Setup mock network with proper pandas .at behavior
    mock_net = mocker.MagicMock()

    # Mock load.at to allow setting values
    mock_load_at = mocker.MagicMock()
    mock_load_at.__setitem__ = mocker.MagicMock()
    mock_net.load.at = mock_load_at

    # Mock res_ext_grid.at to return power value
    mock_res_ext_grid_at = mocker.MagicMock()
    mock_res_ext_grid_at.__getitem__ = mocker.MagicMock(return_value=1.5)
    mock_net.res_ext_grid.at = mock_res_ext_grid_at

    # Create federate instance
    federate = grid_main.GridFederate("grid", "/data/input/test.xlsx")
    federate.net = mock_net
    federate.load_indices = [0, 1]
    federate.ext_grid_indices = [0]
    federate.granted_time = 3600

    # Mock input data from federation
    federate.data_from_federation = {
        "inputs": {
            "house_0/house_load": 1000.0,
            "house_1/house_load": 2000.0,
        }
    }
    federate.data_to_federation = {"publications": {}}

    # Mock pandapower
    mocker.patch.object(grid_main.pp, "runpp", return_value=None)

    # Run update
    federate.update_internal_model()

    # Verify power flow was called
    grid_main.pp.runpp.assert_called_once()

    # Verify publication was set
    assert "Grid/transformer_power" in federate.data_to_federation["publications"]
    assert federate.data_to_federation["publications"]["Grid/transformer_power"] == 1.5


def test_main_runs(mocker):
    """Unit: main orchestrates the federate lifecycle without error."""
    # Mock GridFederate methods
    mock_federate = mocker.MagicMock()
    mocker.patch.object(grid_main, "GridFederate", return_value=mock_federate)

    # Mock sys.argv for argument parsing
    mocker.patch.object(sys, "argv", ["main.py", "--grid_file", "test.xlsx"])

    grid_main.main()

    # Verify federate lifecycle methods called
    mock_federate.create_federate.assert_called_once()
    mock_federate.run_cosim_loop.assert_called_once()
    mock_federate.destroy_federate.assert_called_once()


def test_parse_args(monkeypatch):
    test_args = ["main.py", "--grid_file", "test.xlsx"]
    monkeypatch.setattr(sys, "argv", test_args)
    args = grid_main.parse_args()
    assert args.grid_file == "test.xlsx"
