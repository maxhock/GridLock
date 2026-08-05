"""Unit tests for composegen's pure ETL helpers (extract/transform/load/monkeypatch)."""

from types import SimpleNamespace

import pytest
from treelib import Tree

from extract import add_to_tree, validate_location_queries
from transform import process_general_config, validate_tree, wire_pub_sub
from load import (
    _expand_child_instances,
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
# transform.process_general_config
# ---------------------------------------------------------------------------


def test_process_general_config_requires_end_time() -> None:
    with pytest.raises(ValueError, match="end_time"):
        process_general_config({})


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


def test_process_general_config_converts_numeric_end_time_relative_to_start() -> None:
    cfg = process_general_config({"start_time": 0, "end_time": 3600})

    assert cfg["end_time"] == "2023-01-01T01:00:00"


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
    assert grid_data["publications"]["grid1.load1/voltage"] == "V"

    assert load_data["publications"]["grid1.load1/active_power"] == "W"
    assert load_data["publications"]["grid1.load1/reactive_power"] == "VAr"
    assert load_data["subscriptions"]["grid1.load1/voltage"] == "V"


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


def test_expand_child_instances_leaves_non_house_classes_untouched() -> None:
    assert _expand_child_instances("loadhouse_0", "load", [0, 1, 2]) == [
        ("loadhouse_0", [0, 1, 2])
    ]


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


def test_grid_waits_for_current_time_only_with_exactly_one_grid() -> None:
    assert _grid_waits_for_current_time(1) is True
    assert _grid_waits_for_current_time(0) is False
    assert _grid_waits_for_current_time(2) is False


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
