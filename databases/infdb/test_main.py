"""Unit tests for infdb's config resolution and pylovo query helpers."""

import json

import pytest

from main import extract_grid_config
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

    cfg = extract_grid_config(path)

    assert cfg == {
        "grid_id": "lv-grid",
        "source": "infdb",
        "location": [{"plz": 91359}],
        "use_meta_db": "json",
    }


def test_extract_grid_config_layout_source(tmp_path) -> None:
    path = _write_experiment(tmp_path, {"layout": "kerber.xlsx"})

    cfg = extract_grid_config(path)

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

    cfg = extract_grid_config(path)

    assert cfg["use_meta_db"] == "mongo"


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
