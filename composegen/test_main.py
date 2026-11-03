"""Unit tests for composegen's pure ETL helpers (extract/transform/load/monkeypatch)."""

from types import SimpleNamespace

from pathlib import Path

import pytest
from treelib import Tree

from extract import add_to_tree, validate_location_queries
from transform import (
    process_general_config,
    validate_timeseries_coverage,
    validate_tree,
    wire_pub_sub,
)
from load import (
    _expand_child_instances,
    _grid_command,
    _grid_waits_for_current_time,
    _resolve_load_placement,
    _resolve_placement,
    map_params_to_class,
)
from monkeypatch import _federate_docker, _service


# ---------------------------------------------------------------------------
# extract.add_to_tree
# ---------------------------------------------------------------------------


def test_add_to_tree_creates_root_node() -> None:
    tree = Tree()
    add_to_tree(tree, {"id": "grid1", "class": "grid", "name": "My Grid"})

    node = tree.get_node("grid1")
    assert node is not None
    assert node.tag == "My Grid"
    assert node.data["class"] == "grid"


def test_add_to_tree_nests_sub_federates_with_dotted_ids() -> None:
    tree = Tree()
    add_to_tree(
        tree,
        {
            "id": "grid1",
            "class": "grid",
            "sub_federates": [{"id": "load1", "class": "load"}],
        },
    )

    child = tree.get_node("grid1.load1")
    assert child is not None
    assert child.data["class"] == "load"
    assert tree.parent("grid1.load1").identifier == "grid1"


def test_add_to_tree_requires_id() -> None:
    tree = Tree()
    with pytest.raises(ValueError, match="'id'"):
        add_to_tree(tree, {"class": "grid"})


def test_add_to_tree_defaults_type_to_value() -> None:
    tree = Tree()
    add_to_tree(tree, {"id": "grid1", "class": "grid"})

    assert tree.get_node("grid1").data["type"] == "value"


def test_add_to_tree_turns_empty_strings_into_none() -> None:
    tree = Tree()
    add_to_tree(tree, {"id": "grid1", "class": "grid", "config": {"layout": ""}})

    assert tree.get_node("grid1").data["layout"] is None


# ---------------------------------------------------------------------------
# extract.validate_location_queries
# ---------------------------------------------------------------------------


def test_validate_location_queries_accepts_plz_only() -> None:
    validate_location_queries([{"plz": 91359}], "grid1")


def test_validate_location_queries_accepts_plz_kcid_bcid() -> None:
    validate_location_queries([{"plz": 91359, "kcid": 1, "bcid": 4}], "grid1")


def test_validate_location_queries_rejects_non_list() -> None:
    with pytest.raises(ValueError, match="list of query dicts"):
        validate_location_queries({"plz": 91359}, "grid1")


def test_validate_location_queries_rejects_non_dict_entry() -> None:
    with pytest.raises(ValueError, match="must be a dict"):
        validate_location_queries(["not-a-dict"], "grid1")


def test_validate_location_queries_requires_plz() -> None:
    with pytest.raises(ValueError, match="'plz'"):
        validate_location_queries([{"kcid": 1, "bcid": 4}], "grid1")


def test_validate_location_queries_requires_kcid_and_bcid_together() -> None:
    with pytest.raises(ValueError, match="both be specified or both omitted"):
        validate_location_queries([{"plz": 91359, "kcid": 1}], "grid1")


# ---------------------------------------------------------------------------
# transform.validate_tree
# ---------------------------------------------------------------------------


def _tree_with_root(data: dict) -> Tree:
    tree = Tree()
    tree.create_node(tag="root", identifier="root", data=data)
    return tree


def test_validate_tree_requires_class() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({}))


def test_validate_tree_rejects_unsupported_class() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({"class": "spaceship"}))


def test_validate_tree_grid_requires_layout_or_location() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({"class": "grid"}))


def test_validate_tree_grid_rejects_both_layout_and_location() -> None:
    with pytest.raises(ValueError):
        validate_tree(
            _tree_with_root(
                {"class": "grid", "layout": "a.xlsx", "location": [{"plz": 1}]}
            )
        )


def test_validate_tree_grid_with_layout_only_is_valid() -> None:
    validate_tree(_tree_with_root({"class": "grid", "layout": "a.xlsx"}))


def test_validate_tree_load_requires_electrical_or_heat_load() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({"class": "load"}))


def test_validate_tree_load_rejects_heat_load_only(capsys) -> None:
    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(_tree_with_root({"class": "load", "heat_load": "x.csv"}))

    assert "not implemented" in capsys.readouterr().out


def test_validate_tree_load_rejects_non_csv_electrical_load(capsys) -> None:
    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(_tree_with_root({"class": "load", "electrical_load": "H25"}))

    assert "Standard load" in capsys.readouterr().out


def test_validate_tree_load_with_csv_is_valid() -> None:
    validate_tree(_tree_with_root({"class": "load", "electrical_load": "x.csv"}))


def test_validate_tree_house_requires_model_and_exogenous_data() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({"class": "house"}))


def test_validate_tree_house_with_required_fields_is_valid() -> None:
    validate_tree(
        _tree_with_root(
            {"class": "house", "model": {"class": "1R1C"}, "exogenous_data": "x.csv"}
        )
    )


def test_validate_tree_pv_requires_capacity_and_max_production() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({"class": "pv"}))


def test_validate_tree_battery_requires_capacity_and_power() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({"class": "battery"}))


def test_validate_tree_hems_requires_control_strategy() -> None:
    with pytest.raises(ValueError):
        validate_tree(_tree_with_root({"class": "hems"}))


def _mv_lv_tree(lv_data: dict, mv_data: dict | None = None) -> Tree:
    """An MV grid with one LV grid nested under it."""
    tree = Tree()
    tree.create_node(
        tag="mv",
        identifier="mv-grid_0",
        data=mv_data or {"class": "grid", "layout": "mv.xlsx"},
    )
    tree.create_node(
        tag="lv", identifier="mv-grid_0.lv-grid_0", parent="mv-grid_0", data=lv_data
    )
    return tree


def test_validate_tree_nested_grid_requires_placement(capsys) -> None:
    tree = _mv_lv_tree({"class": "grid", "layout": "lv.xlsx"})

    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(tree)

    printed = capsys.readouterr().out
    assert "mv-grid_0.lv-grid_0" in printed
    assert "requires 'placement'" in printed


def test_validate_tree_root_grid_rejects_placement(capsys) -> None:
    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(
            _tree_with_root({"class": "grid", "layout": "a.xlsx", "placement": 3})
        )

    assert "root grid" in capsys.readouterr().out


def test_validate_tree_nested_grid_rejects_location(capsys) -> None:
    tree = _mv_lv_tree({"class": "grid", "location": [{"plz": 91359}], "placement": 3})

    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(tree)

    assert "must use a 'layout'" in capsys.readouterr().out


def test_validate_tree_grid_under_non_grid_is_rejected(capsys) -> None:
    tree = Tree()
    tree.create_node(
        tag="house",
        identifier="house_0",
        data={"class": "house", "model": {"class": "1R1C"}, "exogenous_data": "x.csv"},
    )
    tree.create_node(
        tag="lv",
        identifier="house_0.lv-grid_0",
        parent="house_0",
        data={"class": "grid", "layout": "lv.xlsx", "placement": 3},
    )

    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(tree)

    printed = capsys.readouterr().out
    assert "house_0.lv-grid_0" in printed
    assert "never be wired" in printed


@pytest.mark.parametrize("placement", ["all", [-1], [], [1, 1], [1, "a"], -1, True])
def test_validate_tree_nested_grid_rejects_invalid_placement_shape(
    placement, capsys
) -> None:
    tree = _mv_lv_tree({"class": "grid", "layout": "lv.xlsx", "placement": placement})

    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(tree)

    assert "invalid placement" in capsys.readouterr().out


@pytest.mark.parametrize("placement", [3, 0, [3, 7], "fill"])
def test_validate_tree_nested_grid_with_valid_placement_is_valid(placement) -> None:
    validate_tree(
        _mv_lv_tree({"class": "grid", "layout": "lv.xlsx", "placement": placement})
    )


def test_validate_tree_reports_every_grid_nesting_problem_together(capsys) -> None:
    tree = _mv_lv_tree(
        {"class": "grid", "location": [{"plz": 1}]},
        mv_data={"class": "grid", "layout": "mv.xlsx", "placement": 2},
    )

    with pytest.raises(ValueError, match="Configuration validation failed"):
        validate_tree(tree)

    printed = capsys.readouterr().out
    assert "root grid" in printed
    assert "requires 'placement'" in printed
    assert "must use a 'layout'" in printed


def test_validate_tree_collects_every_error_before_raising(capsys) -> None:
    tree = Tree()
    tree.create_node(tag="grid1", identifier="grid1", data={"class": "grid"})
    tree.create_node(
        tag="hems1",
        identifier="hems1",
        parent="grid1",
        data={"class": "hems"},
    )

    with pytest.raises(ValueError):
        validate_tree(tree)

    printed = capsys.readouterr().out
    assert "[Grid]" in printed
    assert "[HEMS]" in printed


# ---------------------------------------------------------------------------
# transform.validate_timeseries_coverage
# ---------------------------------------------------------------------------


def _player_covering(tmp_path: Path, seconds: int) -> Tree:
    """A load player whose CSV runs from 0 to ``seconds`` in 900 s rows."""
    rows = "\n".join(f"{t},1000.0,0.0" for t in range(0, seconds + 1, 900))
    (tmp_path / "profile.csv").write_text(f"timestamp,power,reactive\n{rows}\n")
    return _tree_with_root({"class": "load", "electrical_load": "profile.csv"})


@pytest.mark.parametrize(
    "general",
    [
        # 5400 s from 00:30: a CSV covering 3600 s is short however the times
        # are spelled. Both forms used to slip through: the numeric one was
        # sized as end - start of a relative end, the ISO one never checked.
        {"start_time": 1800, "end_time": 7200},
        {"start_time": "2023-01-01T00:30:00", "end_time": "2023-01-01T02:00:00"},
        {"start_time": 1800, "duration": 5400},
    ],
)
def test_coverage_check_uses_the_resolved_duration(
    tmp_path: Path, general: dict
) -> None:
    tree = _player_covering(tmp_path, 3600)
    cfg = process_general_config({**general, "time_step": 900})

    with pytest.raises(ValueError, match="coverage"):
        validate_timeseries_coverage(tree, cfg, tmp_path)

    validate_timeseries_coverage(_player_covering(tmp_path, 5400), cfg, tmp_path)


# ---------------------------------------------------------------------------
# transform.process_general_config
# ---------------------------------------------------------------------------


def test_process_general_config_requires_end_time_or_duration() -> None:
    with pytest.raises(ValueError, match="'end_time' or 'duration'"):
        process_general_config({})
    with pytest.raises(ValueError, match="'end_time' or 'duration'"):
        process_general_config({"start_time": 0})


def test_process_general_config_defaults_start_time() -> None:
    cfg = process_general_config({"end_time": 3600})

    assert cfg["start_time"] == "2023-01-01T00:00:00"


def test_process_general_config_converts_numeric_start_time_to_iso() -> None:
    cfg = process_general_config({"start_time": 3600, "end_time": 7200})

    assert cfg["start_time"] == "2023-01-01T01:00:00"


def test_process_general_config_defaults_time_step_to_one() -> None:
    cfg = process_general_config({"end_time": 3600})

    assert cfg["time_step"] == 1


def test_process_general_config_narrows_whole_number_float_time_step() -> None:
    cfg = process_general_config({"end_time": 3600, "time_step": 60.0})

    assert cfg["time_step"] == 60
    assert isinstance(cfg["time_step"], int)


def test_process_general_config_rejects_fractional_time_step() -> None:
    with pytest.raises(ValueError, match="whole number"):
        process_general_config({"end_time": 3600, "time_step": 1.5})


def test_process_general_config_numeric_end_time_is_absolute() -> None:
    cfg = process_general_config({"start_time": 1800, "end_time": 7200})

    assert cfg["start_time"] == "2023-01-01T00:30:00"
    assert cfg["end_time"] == "2023-01-01T02:00:00"
    assert cfg["duration"] == 5400


def test_process_general_config_duration_alone_starts_at_zero() -> None:
    cfg = process_general_config({"duration": 3600})

    assert cfg["start_time"] == "2023-01-01T00:00:00"
    assert cfg["end_time"] == "2023-01-01T01:00:00"
    assert cfg["duration"] == 3600


@pytest.mark.parametrize(
    "general",
    [
        {"start_time": 1800, "duration": 5400},
        {"end_time": 7200, "duration": 5400},
        {"start_time": "2023-01-01T00:30:00", "end_time": "2023-01-01T02:00:00"},
    ],
)
def test_process_general_config_any_two_fix_the_third(general: dict) -> None:
    cfg = process_general_config(general)

    assert (cfg["start_time"], cfg["end_time"], cfg["duration"]) == (
        "2023-01-01T00:30:00",
        "2023-01-01T02:00:00",
        5400,
    )


def test_process_general_config_refuses_all_three() -> None:
    # Even a consistent triple: two always fix the third, so a third can only
    # agree or silently contradict.
    with pytest.raises(ValueError, match="at most two"):
        process_general_config({"start_time": 0, "end_time": 3600, "duration": 3600})


@pytest.mark.parametrize(
    "general",
    [
        {"start_time": 3600, "end_time": 3600},
        {"start_time": 7200, "end_time": 3600},
        {"duration": 0},
    ],
)
def test_process_general_config_refuses_an_empty_or_reversed_run(
    general: dict,
) -> None:
    with pytest.raises(ValueError, match="end after it starts"):
        process_general_config(general)


def test_process_general_config_refuses_a_utc_offset() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        process_general_config({"end_time": "2023-01-01T02:00:00+01:00"})


def test_process_general_config_normalises_a_bare_date() -> None:
    cfg = process_general_config({"end_time": "2023-01-02"})

    assert cfg["end_time"] == "2023-01-02T00:00:00"
    assert cfg["duration"] == 86400


def test_process_general_config_accepts_iso_end_time() -> None:
    cfg = process_general_config({"end_time": "2023-01-01T02:00:00"})

    assert cfg["end_time"] == "2023-01-01T02:00:00"


# ---------------------------------------------------------------------------
# transform.wire_pub_sub
# ---------------------------------------------------------------------------


def _grid_load_tree() -> Tree:
    tree = Tree()
    tree.create_node(tag="grid1", identifier="grid1", data={"class": "grid"})
    tree.create_node(
        tag="load1", identifier="grid1.load1", parent="grid1", data={"class": "load"}
    )
    return tree


def test_wire_pub_sub_wires_grid_and_load_power_and_voltage() -> None:
    tree = _grid_load_tree()
    wire_pub_sub(tree)

    grid_data = tree.get_node("grid1").data
    load_data = tree.get_node("grid1.load1").data

    assert grid_data["subscriptions"]["grid1.load1/active_power"] == "W"
    assert grid_data["subscriptions"]["grid1.load1/reactive_power"] == "VAr"
    assert grid_data["publications"]["grid1.load1/voltage"] == "pu"

    assert load_data["publications"]["grid1.load1/active_power"] == "W"
    assert load_data["publications"]["grid1.load1/reactive_power"] == "VAr"
    assert load_data["subscriptions"]["grid1.load1/voltage"] == "pu"


def test_wire_pub_sub_wires_house_control_topic() -> None:
    tree = Tree()
    tree.create_node(tag="grid1", identifier="grid1", data={"class": "grid"})
    tree.create_node(
        tag="house1",
        identifier="grid1.house1",
        parent="grid1",
        data={"class": "house"},
    )

    wire_pub_sub(tree)

    grid_data = tree.get_node("grid1").data
    house_data = tree.get_node("grid1.house1").data

    assert grid_data["publications"]["grid1.house1/control"] == "json"
    assert house_data["subscriptions"]["grid1.house1/control"] == "json"


def test_wire_pub_sub_is_idempotent_across_calls() -> None:
    tree = _grid_load_tree()
    wire_pub_sub(tree)
    wire_pub_sub(tree)

    grid_data = tree.get_node("grid1").data
    assert len(grid_data["subscriptions"]) == 2
    assert len(grid_data["publications"]) == 1


# ---------------------------------------------------------------------------
# load._resolve_placement / _resolve_load_placement
# ---------------------------------------------------------------------------

_LOADS = [(0, "load_0", 1), (1, "load_1", 2), (2, "load_2", 3)]


def test_resolve_placement_fill_returns_all_indices() -> None:
    assert _resolve_placement("fill", _LOADS) == [0, 1, 2]


def test_resolve_placement_fill_excludes_claimed_indices() -> None:
    assert _resolve_placement("fill", _LOADS, exclude={1}) == [0, 2]


def test_resolve_placement_single_int() -> None:
    assert _resolve_placement(1, _LOADS) == [1]


def test_resolve_placement_list_of_ints() -> None:
    assert _resolve_placement([0, 2], _LOADS) == [0, 2]


def test_resolve_placement_rejects_unknown_index() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        _resolve_placement([99], _LOADS)


def test_resolve_placement_rejects_unsupported_value() -> None:
    with pytest.raises(ValueError, match="Unsupported placement"):
        _resolve_placement("bogus", _LOADS)


def test_resolve_load_placement_rejects_grid_with_no_loads() -> None:
    with pytest.raises(ValueError, match="has no loads"):
        _resolve_load_placement("fill", [], "child1", "load", "grid1")


def test_resolve_load_placement_rejects_fill_with_everything_claimed() -> None:
    with pytest.raises(ValueError, match="resolved to no pandapower load index"):
        _resolve_load_placement(
            "fill", _LOADS, "child1", "load", "grid1", exclude={0, 1, 2}
        )


def test_resolve_load_placement_returns_resolved_indices() -> None:
    assert _resolve_load_placement(1, _LOADS, "child1", "load", "grid1") == [1]


# ---------------------------------------------------------------------------
# load._expand_child_instances
# ---------------------------------------------------------------------------


def test_expand_child_instances_keeps_load_player_as_one_instance() -> None:
    assert _expand_child_instances("loadhouse_0", "load", [0, 1, 2]) == [
        ("loadhouse_0", [0, 1, 2])
    ]


def test_expand_child_instances_splits_grid_per_load_index() -> None:
    result = _expand_child_instances("lv-grid_0", "grid", [3, 7])

    assert result == [("lv-grid_3", [3]), ("lv-grid_7", [7])]


def test_expand_child_instances_grid_requires_load_indices() -> None:
    with pytest.raises(ValueError, match="Grid 'lv-grid_0'.*no pandapower load"):
        _expand_child_instances("lv-grid_0", "grid", None)


def test_expand_child_instances_splits_house_per_load_index() -> None:
    result = _expand_child_instances("house_0", "house", [4, 6])

    assert result == [("house_4", [4]), ("house_6", [6])]


def test_expand_child_instances_house_requires_load_indices() -> None:
    with pytest.raises(ValueError, match="no pandapower load index"):
        _expand_child_instances("house_0", "house", [])


def test_expand_child_instances_house_requires_load_indices_none() -> None:
    with pytest.raises(ValueError):
        _expand_child_instances("house_0", "house", None)


# ---------------------------------------------------------------------------
# load.map_params_to_class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "federate_class,expected_image",
    [
        ("grid", "grid"),
        ("house", "house"),
        ("load", "house_player"),
        ("house_player", "house_player"),
        ("pv", "house"),
        ("battery", "house"),
        ("hems", "controller"),
        ("controller", "controller"),
        ("recorder", "recorder"),
    ],
)
def test_map_params_to_class_known_classes(federate_class, expected_image) -> None:
    assert map_params_to_class(federate_class)["image"] == expected_image


def test_map_params_to_class_unknown_class_falls_back_to_default_image() -> None:
    mapped = map_params_to_class("spaceship")

    assert mapped == {"image": "cosim-cst:latest", "command": "python3 main.py"}


# ---------------------------------------------------------------------------
# load._grid_waits_for_current_time
# ---------------------------------------------------------------------------


def test_grid_waits_for_current_time_only_with_exactly_one_root_grid() -> None:
    assert _grid_waits_for_current_time(1) is True
    assert _grid_waits_for_current_time(0) is False
    assert _grid_waits_for_current_time(2) is False


# ---------------------------------------------------------------------------
# load._grid_command
# ---------------------------------------------------------------------------


def test_grid_command_passes_net_key_separately_from_federate_name() -> None:
    cmd = _grid_command("python3 main.py", "Scn_20260101_000000", "lv-grid_3", "lv")

    assert cmd == (
        "python3 main.py --scenario Scn_20260101_000000 "
        "--federate_name lv-grid_3 --net_key lv"
    )


# ---------------------------------------------------------------------------
# monkeypatch._service / _federate_docker
# ---------------------------------------------------------------------------


def test_service_uses_federates_subdir_context_for_regular_images() -> None:
    svc = _service("lv-grid", "grid", ["", "python3 main.py"], 3, depends="helics")

    assert "  lv-grid:\n" in svc
    assert '    image: "grid"\n' in svc
    assert "      context: ../federates/grid\n" in svc
    assert "      dockerfile: Dockerfile\n" in svc
    assert "    depends_on:\n      - helics\n" in svc
    assert '    command: /bin/bash -c "python3 main.py"\n' in svc


def test_service_uses_repo_root_context_for_house_and_controller() -> None:
    svc = _service("house_4", "house", ["", "python3 house/main.py"], 3)

    assert "      context: ..\n" in svc
    assert "      dockerfile: federates/house/Dockerfile\n" in svc


def test_service_omits_environment_block_when_params_empty() -> None:
    svc = _service("helics", "broker", ["", "helics_broker"], 2)

    assert "environment:" not in svc


def test_service_includes_environment_block_when_params_given() -> None:
    svc = _service("lv-grid", "grid", ["      FOO: bar\n", "python3 main.py"], 3)

    assert "    environment:\n      FOO: bar\n" in svc


def test_service_omits_depends_on_when_none() -> None:
    svc = _service("helics", "broker", ["", "helics_broker"], 2, depends=None)

    assert "depends_on" not in svc


def test_federate_docker_points_broker_address_at_compose_service_name() -> None:
    calls: dict[str, str] = {}
    stub = SimpleNamespace(
        helics=SimpleNamespace(config=lambda key, value: calls.setdefault(key, value))
    )

    _federate_docker(stub)

    assert calls == {"broker_address": "helics"}
