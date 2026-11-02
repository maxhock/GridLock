"""Tests for the grid-layout file check in extract (suffix and existence)."""

from pathlib import Path

import pytest
import yaml

from extract import extract


def _extract_layout(tmp_path: Path, layout: str, create: bool = True):
    if create:
        (tmp_path / layout).write_text("stub")
    config = tmp_path / "experiment.yml"
    config.write_text(
        yaml.safe_dump(
            {
                "general": {},
                "federation": {
                    "id": "grid",
                    "class": "grid",
                    "config": {"layout": layout},
                },
            }
        )
    )
    return extract(config, tmp_path, tmp_path)


@pytest.mark.parametrize("layout", ["net.xlsx", "net.json", "NET.JSON"])
def test_extract_accepts_supported_layout_suffixes(tmp_path, layout) -> None:
    extracted = _extract_layout(tmp_path, layout)

    assert extracted.mode == "tree"
    assert list(extracted.grid_nodes) == ["grid"]


def test_extract_rejects_unsupported_layout_suffix(tmp_path) -> None:
    with pytest.raises(ValueError, match=r"net\.xls.*\.xlsx.*\.json"):
        _extract_layout(tmp_path, "net.xls")


def test_extract_rejects_missing_layout_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match=r"net\.json"):
        _extract_layout(tmp_path, "net.json", create=False)
