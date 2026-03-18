import sys
from unittest.mock import MagicMock, mock_open

import main as grid_main


def test_create_federate_registers_dynamic_helics_interfaces(mocker):
    """Grid federate should register dynamic interfaces after CST creates the federate."""
    config_data = '{"period": 3600, "max_cosim_duration": 82800}'
    mocker.patch("builtins.open", mock_open(read_data=config_data))

    mock_net = MagicMock()
    mock_net.load.index = [0, 2, 5]
    mock_net.ext_grid.index = [0]
    mocker.patch.object(grid_main.pp, "from_excel", return_value=mock_net)
    register_sub = mocker.patch.object(
        grid_main.h,
        "helicsFederateRegisterSubscription",
        return_value=MagicMock(),
    )
    register_pub = mocker.patch.object(
        grid_main.h,
        "helicsFederateRegisterGlobalPublication",
        return_value=MagicMock(),
    )

    federate = grid_main.GridFederate("grid", "/data/input/test.xlsx")
    federate.create_helics_fed = MagicMock()
    federate.hfed = MagicMock()

    federate.create_federate()

    federate.create_helics_fed.assert_called_once()
    assert register_sub.call_count == 3
    register_sub.assert_any_call(federate.hfed, "node_0/P", "MW")
    register_sub.assert_any_call(federate.hfed, "node_2/P", "MW")
    register_sub.assert_any_call(federate.hfed, "node_5/P", "MW")
    register_pub.assert_called_once_with(
        federate.hfed,
        "Grid/transformer_0_power",
        grid_main.h.HELICS_DATA_TYPE_DOUBLE,
        "MW",
    )


def test_update_internal_model_uses_cst_input_cache(mocker):
    """Grid federate should read CST's cached federation inputs for the current step."""
    mock_net = MagicMock()
    mock_net.load.index = [0, 2]
    mock_net.ext_grid.index = [0]
    mock_net.res_ext_grid.at.__getitem__.return_value = 1.5

    federate = grid_main.GridFederate("grid", "/data/input/test.xlsx")
    federate.net = mock_net
    federate.load_indices = [0, 2]
    federate.ext_grid_indices = [0]
    federate.granted_time = 3600
    federate.data_from_federation = {
        "inputs": {
            "node_0/P": 0.005,
            "node_2/P": [],
        }
    }
    federate.data_to_federation = {"publications": {}}

    runpp = mocker.patch.object(grid_main.pp, "runpp", return_value=None)

    federate.update_internal_model()

    mock_net.load.at.__setitem__.assert_called_once_with((0, "p_mw"), 0.005)
    runpp.assert_called_once_with(mock_net, numba=False)
    assert federate.data_to_federation["publications"]["Grid/transformer_0_power"] == 1.5


def test_main_runs(mocker):
    """Unit: main orchestrates the federate lifecycle without error."""
    mock_federate = MagicMock()
    mocker.patch.object(grid_main, "GridFederate", return_value=mock_federate)
    mocker.patch.object(sys, "argv", ["main.py", "--grid_file", "test.xlsx"])

    grid_main.main()

    mock_federate.create_federate.assert_called_once()
    mock_federate.run_cosim_loop.assert_called_once()
    mock_federate.destroy_federate.assert_called_once()


def test_parse_args(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--grid_file", "test.xlsx"])
    args = grid_main.parse_args()
    assert args.grid_file == "test.xlsx"
