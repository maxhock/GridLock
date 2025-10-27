import sys
import pytest
import main as grid_main


# Auto-mock helics and pandapower modules for all tests
@pytest.fixture(autouse=True)
def patch_helics_and_pandapower(mocker):
    import sys

    modules_patch = {
        "helics": mocker.MagicMock(),
        "pandapower": mocker.MagicMock(),
        "pandapower.networks": mocker.MagicMock(),
    }
    mocker.patch.dict(sys.modules, modules_patch)
    yield


def test_create_federate(mocker):
    """Unit: create_federate returns correct structure and uses HELICS API."""
    mock_fed = mocker.MagicMock()
    mock_sub = mocker.MagicMock()
    mock_pub = mocker.MagicMock()
    mocker.patch.object(
        grid_main.h, "helicsCreateValueFederateFromConfig", return_value=mock_fed
    )
    mocker.patch.object(
        grid_main.h, "helicsFederateRegisterSubscription", return_value=mock_sub
    )
    mocker.patch.object(
        grid_main.h, "helicsFederateRegisterGlobalPublication", return_value=mock_pub
    )
    mocker.patch.object(grid_main.h, "helicsInputGetName", return_value="mock_sub_key")
    mocker.patch.object(
        grid_main.h, "helicsPublicationGetName", return_value="mock_pub_key"
    )
    fed, net, load_subs, ext_grid_pubs = grid_main.create_federate()
    assert fed is mock_fed
    assert net is not None
    assert isinstance(load_subs, list)
    assert isinstance(ext_grid_pubs, list)


def test_run_federate_executes_loop(mocker):
    """Unit: run_federate executes main loop and calls HELICS APIs."""
    fed = mocker.MagicMock()
    net = mocker.MagicMock()
    load_subs = [(0, mocker.MagicMock())]
    ext_grid_pubs = [(0, mocker.MagicMock())]
    mocker.patch.object(
        grid_main.h, "helicsFederateEnterExecutingMode", return_value=None
    )
    times = [0, 3600, 7200, 82800, 86400]
    mocker.patch.object(
        grid_main.h,
        "helicsFederateRequestTime",
        side_effect=lambda fed, t: times.pop(0) if times else 86400,
    )
    mocker.patch.object(
        grid_main.h, "helicsInputIsUpdated", side_effect=lambda sub: False
    )
    mocker.patch.object(grid_main.h, "helicsInputGetDouble", return_value=0.0)
    mocker.patch.object(
        grid_main.pp, "runpp", side_effect=lambda net, numba=False: None
    )
    mocker.patch.object(
        grid_main.h, "helicsPublicationPublishDouble", side_effect=lambda pub, val: None
    )
    mocker.patch.object(grid_main.h, "helicsFederateFinalize", return_value=None)
    grid_main.run_federate(fed, net, load_subs, ext_grid_pubs)


def test_cleanup_federate(mocker):
    """Unit: cleanup_federate calls HELICS disconnect."""
    fed = mocker.MagicMock()
    mocker.patch.object(grid_main.h, "helicsFederateDisconnect", return_value=None)
    grid_main.cleanup_federate(fed)


def test_main_runs(mocker):
    """Unit: main orchestrates the federate lifecycle without error."""
    mocker.patch.object(
        grid_main,
        "create_federate",
        return_value=(mocker.MagicMock(), mocker.MagicMock(), [], []),
    )
    mocker.patch.object(grid_main, "run_federate", return_value=None)
    mocker.patch.object(grid_main, "cleanup_federate", return_value=None)
    grid_main.main()


def test_parse_args(monkeypatch):
    test_args = ["main.py", "--grid_file", "test.xlsx"]
    monkeypatch.setattr(sys, "argv", test_args)
    args = grid_main.parse_args()
    assert args.grid_file == "test.xlsx"
