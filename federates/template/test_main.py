"""Unit tests for the template federate's pure helpers and its CST contract.

These run inside the image's `test` build stage, so importing `main` at all is
half the point: it proves the pinned `cosim-toolbox` is installed and still
exposes `Federate` where this template imports it from.
"""

import pytest
from cosim_toolbox.sims import Federate

from main import TemplateFederate, _find_interface_key, get_db_backends_from_env


# ---------------------------------------------------------------------------
# _find_interface_key - the "discover your keys, never construct them" invariant
# ---------------------------------------------------------------------------


def test_find_interface_key_matches_suffix_after_slash() -> None:
    interfaces = {"local-grid/template_0/output": 0.0, "local-grid/grid/voltage": 0.0}

    assert (
        _find_interface_key(interfaces, "output", "template_0", "publication")
        == "local-grid/template_0/output"
    )


def test_find_interface_key_matches_bare_key() -> None:
    interfaces = {"output": 0.0}

    assert _find_interface_key(interfaces, "output", "template_0", "publication") == (
        "output"
    )


def test_find_interface_key_does_not_match_partial_segment() -> None:
    """`throughput` ends in `output` as a string, but is a different interface."""
    interfaces = {"local-grid/template_0/throughput": 0.0}

    with pytest.raises(ValueError, match="No publication ending in 'output'"):
        _find_interface_key(interfaces, "output", "template_0", "publication")


def test_find_interface_key_raises_when_missing() -> None:
    interfaces = {"local-grid/template_0/state": 0.0}

    with pytest.raises(ValueError, match="No subscription ending in 'input'"):
        _find_interface_key(interfaces, "input", "template_0", "subscription")


def test_find_interface_key_missing_names_the_registered_interfaces() -> None:
    """A failure has to say what *was* registered, or it cannot be acted on."""
    interfaces = {"local-grid/template_0/state": 0.0}

    with pytest.raises(ValueError, match="local-grid/template_0/state"):
        _find_interface_key(interfaces, "input", "template_0", "subscription")


def test_find_interface_key_raises_when_ambiguous() -> None:
    interfaces = {"a/output": 0.0, "b/output": 0.0}

    with pytest.raises(ValueError, match="Multiple publications"):
        _find_interface_key(interfaces, "output", "template_0", "publication")


# ---------------------------------------------------------------------------
# get_db_backends_from_env - fail loudly rather than defaulting to another store
# ---------------------------------------------------------------------------


def test_get_db_backends_from_env_returns_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CST_USE_META_DB", "mongo")
    monkeypatch.setenv("CST_USE_DATA_DB", "postgres")

    assert get_db_backends_from_env() == ("mongo", "postgres")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CST_USE_META_DB": "mongo"},
        {"CST_USE_DATA_DB": "postgres"},
        {"CST_USE_META_DB": "", "CST_USE_DATA_DB": "postgres"},
    ],
)
def test_get_db_backends_from_env_raises_when_incomplete(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    monkeypatch.delenv("CST_USE_META_DB", raising=False)
    monkeypatch.delenv("CST_USE_DATA_DB", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="CST_USE_META_DB"):
        get_db_backends_from_env()


# ---------------------------------------------------------------------------
# The CST contract a copy of this template has to keep
# ---------------------------------------------------------------------------


def test_template_federate_subclasses_cst_federate() -> None:
    assert issubclass(TemplateFederate, Federate)


def test_template_federate_overrides_update_internal_model() -> None:
    """The one method CST requires a federate to provide."""
    assert TemplateFederate.update_internal_model is not Federate.update_internal_model


def test_template_federate_keeps_the_name_it_was_given() -> None:
    federate = TemplateFederate("local-grid.template_0")

    assert federate.federate_name == "local-grid.template_0"


def test_template_federate_resolves_no_keys_before_create_federate() -> None:
    """Interface keys exist only after `create_federate`; nothing may guess them."""
    federate = TemplateFederate("local-grid.template_0")

    assert federate.input_key == ""
    assert federate.output_key == ""
