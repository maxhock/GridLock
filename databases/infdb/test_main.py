"""Unit tests for infdb's config resolution and pylovo query helpers."""

import json

import pandapower as pp
import pytest

import main as infdb_main
from main import extract_grid_config, load_local_layout, write_grid_to_metadata
from src.infdb_data import _net_table_count, get_pylovo_grid, resolve_grid_queries

# ---------------------------------------------------------------------------
# main.extract_grid_config
# ---------------------------------------------------------------------------


def _write_experiment(tmp_path, federation_config: dict, general: dict | None = None):
    path = tmp_path / "experiment.yml"
    doc = {
        "general": general or {},
        "federation": {"id": "lv-grid", "config": federation_config},
    }
    path.write_text(json.dumps(doc))
    return str(path)


def test_extract_grid_config_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_grid_config(str(tmp_path / "nope.yml"))


def test_extract_grid_config_location_source(tmp_path) -> None:
    path = _write_experiment(tmp_path, {"location": [{"plz": 91359}]})

    (cfg,) = extract_grid_config(path)

    assert cfg == {
        "grid_id": "lv-grid",
        "source": "infdb",
        "location": [{"plz": 91359}],
        "use_meta_db": "json",
    }


def test_extract_grid_config_layout_source(tmp_path) -> None:
    path = _write_experiment(tmp_path, {"layout": "kerber.xlsx"})

    (cfg,) = extract_grid_config(path)

    assert cfg == {
        "grid_id": "lv-grid",
        "source": "layout",
        "layout": "kerber.xlsx",
        "use_meta_db": "json",
    }


def test_extract_grid_config_rejects_both_location_and_layout(tmp_path) -> None:
    path = _write_experiment(
        tmp_path, {"location": [{"plz": 91359}], "layout": "kerber.xlsx"}
    )

    with pytest.raises(ValueError, match="not both"):
        extract_grid_config(path)


def test_extract_grid_config_requires_a_source(tmp_path) -> None:
    path = _write_experiment(tmp_path, {})

    with pytest.raises(ValueError, match="No grid source"):
        extract_grid_config(path)


def test_extract_grid_config_uses_declared_use_meta_db(tmp_path) -> None:
    path = _write_experiment(
        tmp_path, {"layout": "kerber.xlsx"}, general={"use_meta_db": "mongo"}
    )

    (cfg,) = extract_grid_config(path)

    assert cfg["use_meta_db"] == "mongo"


# ---------------------------------------------------------------------------
# main.extract_grid_config: nested grids
# ---------------------------------------------------------------------------


def _write_tree(tmp_path, federation: dict) -> str:
    path = tmp_path / "experiment.yml"
    path.write_text(json.dumps({"general": {}, "federation": federation}))
    return str(path)


def _mv_lv_tree(lv_config: dict, lv_class: str = "grid", mv_class: str = "grid"):
    return {
        "id": "mv-grid_0",
        "class": mv_class,
        "config": {"layout": "mv.xlsx"},
        "sub_federates": [
            {
                "id": "lv-grid_0",
                "class": lv_class,
                "config": lv_config,
                "sub_federates": [{"id": "house_0", "class": "house", "config": {}}],
            }
        ],
    }


def test_extract_grid_config_collects_nested_layout_grid_with_dotted_id(
    tmp_path,
) -> None:
    path = _write_tree(tmp_path, _mv_lv_tree({"layout": "lv.xlsx", "placement": [3]}))

    configs = extract_grid_config(path)

    assert [(c["grid_id"], c["source"]) for c in configs] == [
        ("mv-grid_0", "layout"),
        ("mv-grid_0.lv-grid_0", "layout"),
    ]
    assert configs[1]["layout"] == "lv.xlsx"


def _three_level_tree() -> dict:
    return {
        "id": "a",
        "class": "grid",
        "config": {"layout": "a.xlsx"},
        "sub_federates": [
            {
                "id": "b",
                "class": "grid",
                "config": {"layout": "b.xlsx"},
                "sub_federates": [
                    {"id": "c", "class": "grid", "config": {"layout": "c.xlsx"}},
                    {"id": "house_0", "class": "house", "config": {}},
                ],
            }
        ],
    }


def test_extract_grid_config_third_level_gets_full_dotted_id(tmp_path) -> None:
    configs = extract_grid_config(_write_tree(tmp_path, _three_level_tree()))

    assert [c["grid_id"] for c in configs] == ["a", "a.b", "a.b.c"]


def test_extract_grid_config_dotted_ids_match_composegen_rule(tmp_path) -> None:
    # composegen identifies tree nodes by this rule, in
    # composegen/extract.py:add_to_tree, and reads each net back under that id:
    #   node_id = f"{parent}.{node_dict.get('id')}" if parent else node_dict.get("id")
    # infdb cannot import composegen (separate images), so the rule is restated here
    # and the ids infdb writes under are pinned to it.
    def composegen_ids(node: dict, parent: str | None = None) -> list[str]:
        node_id = f"{parent}.{node.get('id')}" if parent else node.get("id")
        ids = [node_id] if node.get("class") == "grid" else []
        for sub in node.get("sub_federates", []):
            ids.extend(composegen_ids(sub, node_id))
        return ids

    tree = _three_level_tree()

    configs = extract_grid_config(_write_tree(tmp_path, tree))

    assert [c["grid_id"] for c in configs] == composegen_ids(tree)
    assert [c["grid_id"] for c in configs] == ["a", "a.b", "a.b.c"]


def test_extract_grid_config_nested_location_is_not_implemented(tmp_path) -> None:
    path = _write_tree(tmp_path, _mv_lv_tree({"location": [{"plz": 91359}]}))

    with pytest.raises(NotImplementedError, match="mv-grid_0.lv-grid_0.*layout"):
        extract_grid_config(path)


def test_extract_grid_config_root_location_with_nested_layout(tmp_path) -> None:
    tree = _mv_lv_tree({"layout": "lv.xlsx"})
    tree["config"] = {"location": [{"plz": 91359}]}

    configs = extract_grid_config(_write_tree(tmp_path, tree))

    assert [c["source"] for c in configs] == ["infdb", "layout"]


def test_extract_grid_config_grid_under_non_grid_parent_raises(tmp_path) -> None:
    # composegen only finds grids whose ancestors are all grids, so this one would
    # get a net and never be wired
    tree = {
        "id": "root",
        "class": "grid",
        "config": {"layout": "root.xlsx"},
        "sub_federates": [
            {
                "id": "mid",
                "class": "house",
                "config": {},
                "sub_federates": [
                    {"id": "lv", "class": "grid", "config": {"layout": "lv.xlsx"}}
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match=r"'root\.mid\.lv'.*not a grid"):
        extract_grid_config(_write_tree(tmp_path, tree))


def test_extract_grid_config_nested_grid_requires_a_source(tmp_path) -> None:
    path = _write_tree(tmp_path, _mv_lv_tree({}))

    with pytest.raises(ValueError, match="No grid source.*mv-grid_0.lv-grid_0"):
        extract_grid_config(path)


def test_extract_grid_config_nested_grid_rejects_both_sources(tmp_path) -> None:
    path = _write_tree(
        tmp_path, _mv_lv_tree({"layout": "lv.xlsx", "location": [{"plz": 1}]})
    )

    with pytest.raises(ValueError, match="not both"):
        extract_grid_config(path)


def test_extract_grid_config_rejects_duplicate_grid_ids(tmp_path) -> None:
    tree = _three_level_tree()
    tree["sub_federates"].append(
        {"id": "b", "class": "grid", "config": {"layout": "b2.xlsx"}}
    )

    with pytest.raises(ValueError, match="Duplicate grid id"):
        extract_grid_config(_write_tree(tmp_path, tree))


# ---------------------------------------------------------------------------
# main.write_grid_to_metadata
# ---------------------------------------------------------------------------


class _StubMetaManager:
    def __init__(self) -> None:
        self.writes: dict[str, dict] = {}

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def write(self, collection: str, name: str, data: dict, overwrite: bool) -> None:
        assert collection == "custom_metadata"
        self.writes[name] = data


def test_write_grid_to_metadata_writes_every_grids_nets(monkeypatch) -> None:
    manager = _StubMetaManager()
    monkeypatch.setattr("main.create_metadata_manager", lambda **_: manager)
    mv_net = json.dumps(_net_json(bus_rows=3))
    lv_net = json.dumps(_net_json(bus_rows=2, load_rows=1))

    names = write_grid_to_metadata(
        {
            "mv-grid_0": [("mv-grid_0", mv_net)],
            "mv-grid_0.lv-grid_0": [("mv-grid_0.lv-grid_0", lv_net)],
        },
        "generated",
        "json",
    )

    assert names == ["mv-grid_0", "mv-grid_0.lv-grid_0"]
    assert manager.writes["mv-grid_0.lv-grid_0"]["grid_id"] == "mv-grid_0.lv-grid_0"
    assert manager.writes["mv-grid_0.lv-grid_0"]["load_count"] == 1
    assert manager.writes["mv-grid_0"]["bus_count"] == 3


def test_main_resolves_layout_grids_without_connecting_to_infdb(
    tmp_path, monkeypatch
) -> None:
    def no_infdb(*_a, **_k):
        raise AssertionError("InfDB must only be contacted for 'location' grids")

    loaded: list[str] = []

    def fake_layout(grid_id: str, layout: str) -> list[tuple[str, str]]:
        loaded.append(grid_id)
        return [(grid_id, json.dumps(_net_json(bus_rows=1)))]

    written: dict = {}

    def fake_write(grids, meta_store_path, use_meta_db) -> list[str]:
        written.update(grids)
        return list(grids)

    monkeypatch.setattr(infdb_main, "InfDB", no_infdb)
    monkeypatch.setattr(infdb_main, "prepare_infdb_config", no_infdb)
    monkeypatch.setattr(infdb_main, "load_local_layout", fake_layout)
    monkeypatch.setattr(infdb_main, "write_grid_to_metadata", fake_write)
    path = _write_tree(tmp_path, _mv_lv_tree({"layout": "lv.xlsx"}))

    infdb_main.main(path, "generated")

    assert loaded == ["mv-grid_0", "mv-grid_0.lv-grid_0"]
    assert list(written) == loaded


# ---------------------------------------------------------------------------
# infdb_data._net_table_count
# ---------------------------------------------------------------------------


def _net_json(bus_rows: int = 0, load_rows: int = 0) -> dict:
    net: dict = {"_object": {}}
    if bus_rows:
        net["_object"]["bus"] = {
            "_object": json.dumps({"index": list(range(bus_rows))})
        }
    if load_rows:
        net["_object"]["load"] = {
            "_object": json.dumps({"index": list(range(load_rows))})
        }
    return net


def test_net_table_count_counts_rows() -> None:
    net = _net_json(bus_rows=3, load_rows=1)

    assert _net_table_count(net, "bus") == 3
    assert _net_table_count(net, "load") == 1


def test_net_table_count_missing_table_is_zero() -> None:
    net = _net_json(bus_rows=3)

    assert _net_table_count(net, "ext_grid") == 0


def test_net_table_count_accepts_json_string() -> None:
    net = _net_json(bus_rows=2)

    assert _net_table_count(json.dumps(net), "bus") == 2


# ---------------------------------------------------------------------------
# infdb_data.get_pylovo_grid / resolve_grid_queries
# ---------------------------------------------------------------------------


class _StubLog:
    def debug(self, *a, **k) -> None: ...
    def info(self, *a, **k) -> None: ...
    def warning(self, *a, **k) -> None: ...
    def error(self, *a, **k) -> None: ...


class _StubDB:
    def __init__(self, infdb: "_StubInfDB") -> None:
        self._infdb = infdb

    def execute_query(self, sql: str):
        self._infdb.queries.append(sql)
        return self._infdb.rows


class _StubConnection:
    def __init__(self, infdb: "_StubInfDB") -> None:
        self._infdb = infdb

    def __enter__(self):
        return _StubDB(self._infdb)

    def __exit__(self, *exc) -> None:
        return None


class _StubInfDB:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def connect(self):
        return _StubConnection(self)


def test_get_pylovo_grid_builds_dataframe_from_list_result() -> None:
    grid = _net_json(bus_rows=2)
    stub = _StubInfDB([{"kcid": 1, "bcid": 4, "grid": grid}])

    df = get_pylovo_grid(stub, _StubLog(), plz=91359)

    assert list(df.columns) == ["kcid", "bcid", "grid"]
    assert len(df) == 1
    assert "AND kcid" not in stub.queries[-1]


def test_get_pylovo_grid_filters_by_kcid_and_bcid_when_given() -> None:
    stub = _StubInfDB([])

    get_pylovo_grid(stub, _StubLog(), plz=91359, kcid=1, bcid=4)

    assert "AND kcid=1 AND bcid=4" in stub.queries[-1]


def test_resolve_grid_queries_names_federate_by_plz_kcid_bcid() -> None:
    grid = _net_json(bus_rows=2)
    stub = _StubInfDB([{"kcid": 1, "bcid": 4, "grid": grid}])

    net_list = resolve_grid_queries(
        infdb=stub, log=_StubLog(), grid_id="lv-grid", queries=[{"plz": 91359}]
    )

    assert net_list == [("lv-grid_91359_1_4", json.dumps(grid))]


def test_resolve_grid_queries_rejects_duplicate_federate_names() -> None:
    grid = _net_json(bus_rows=1)
    stub = _StubInfDB(
        [
            {"kcid": 1, "bcid": 4, "grid": grid},
            {"kcid": 1, "bcid": 4, "grid": grid},
        ]
    )

    with pytest.raises(ValueError, match="Duplicate resolved federate name"):
        resolve_grid_queries(
            infdb=stub, log=_StubLog(), grid_id="lv-grid", queries=[{"plz": 91359}]
        )


def test_resolve_grid_queries_raises_when_nothing_resolves() -> None:
    stub = _StubInfDB([])

    with pytest.raises(ValueError, match="No grids found"):
        resolve_grid_queries(
            infdb=stub, log=_StubLog(), grid_id="lv-grid", queries=[{"plz": 91359}]
        )


# ---------------------------------------------------------------------------
# main.load_local_layout
# ---------------------------------------------------------------------------


def _tiny_net() -> pp.pandapowerNet:
    net = pp.create_empty_network()
    bus = pp.create_bus(net, vn_kv=20.0)
    pp.create_ext_grid(net, bus)
    pp.create_load(net, bus, p_mw=0.1)
    return net


def _layout_dir(tmp_path, monkeypatch):
    """Point load_local_layout's fixed /data/input base at a temp directory."""
    monkeypatch.setattr(infdb_main, "LAYOUT_DIR", tmp_path)
    return tmp_path


def test_load_local_layout_reads_json_export(tmp_path, monkeypatch) -> None:
    pp.to_json(_tiny_net(), str(_layout_dir(tmp_path, monkeypatch) / "net.json"))

    ((grid_id, net_json),) = load_local_layout("grid", "net.json")

    assert grid_id == "grid"
    assert _net_table_count(net_json, "bus") == 1
    assert _net_table_count(net_json, "load") == 1
    assert len(pp.from_json_string(net_json).load) == 1


def test_load_local_layout_rejects_unsupported_suffix(tmp_path, monkeypatch) -> None:
    (_layout_dir(tmp_path, monkeypatch) / "net.xls").write_text("stub")

    with pytest.raises(ValueError, match=r"net\.xls.*\.xlsx.*\.json"):
        load_local_layout("grid", "net.xls")


def test_extract_grid_config_expands_filename_placed_grids(
    tmp_path, monkeypatch
) -> None:
    layouts = _layout_dir(tmp_path, monkeypatch)
    for name in ("mv.json", "mv/load_2.json", "mv/load_10.json"):
        (layouts / name).parent.mkdir(parents=True, exist_ok=True)
        (layouts / name).write_text("{}")
    lv = {"id": "lv_0", "class": "grid", "config": {"placement": "filename"}}
    path = tmp_path / "experiment.yml"
    path.write_text(
        json.dumps(
            {
                "federation": {
                    "id": "mv",
                    "class": "grid",
                    "config": {"layout": "mv.json"},
                    "sub_federates": [lv],
                }
            }
        )
    )

    configs = extract_grid_config(str(path))

    assert [(c["grid_id"], c["layout"]) for c in configs] == [
        ("mv", "mv.json"),
        ("mv.lv_2", "mv/load_2.json"),
        ("mv.lv_10", "mv/load_10.json"),
    ]
