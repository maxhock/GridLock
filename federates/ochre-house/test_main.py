"""Unit tests for the ochre-house federate's helpers, its CST contract and one real step.

These run inside the image's `test` build stage, so importing `main` at all is
half the point: it proves the pinned `cosim-toolbox` is installed and still
exposes `Federate` where this federate imports it from.
"""

import json
import os
from pathlib import Path

import pandas as pd
import pytest
from cosim_toolbox.sims import Federate

import main
from main import (
    END_USE_KEYS,
    PV_KEY,
    SIM_YEAR,
    OchreHouseFederate,
    pv_equipment,
    exogenous_to_ochre_weather,
    get_db_backends_from_env,
    serialize_dwelling_status,
    setup_dwelling,
    write_ochre_inputs,
)


def _status() -> dict[str, float]:
    status = {
        "Temperature - Indoor (C)": 20.5,
        "Temperature - Outdoor (C)": -3.0,
        "Total Electric Power (kW)": 1.5,
        "Total Reactive Power (kVAR)": 0.25,
    }
    status.update({key: 0.1 for key in END_USE_KEYS})
    return status


def _federate_with_publications(*keys: str) -> OchreHouseFederate:
    federate = OchreHouseFederate("local-grid.ochre-house_6")
    federate.data_to_federation = {"publications": {key: 0.0 for key in keys}}
    return federate


# ---------------------------------------------------------------------------
# serialize_dwelling_status - OCHRE's status dict to the published `state` record
# ---------------------------------------------------------------------------


def test_serialize_dwelling_status_converts_kilowatts_to_watts() -> None:
    record = serialize_dwelling_status(_status(), "ochre-house_6", 3600.0, 3600)

    assert record["grid_exchange"]["total_load_w"] == pytest.approx(1500.0)
    assert record["grid_exchange"]["reactive_power_var"] == pytest.approx(250.0)
    assert record["loads_w"]["grid_total"] == pytest.approx(1500.0)
    assert record["loads_w"]["heating"] == pytest.approx(100.0)


def test_serialize_dwelling_status_publishes_pv_as_positive_generation() -> None:
    status = _status() | {PV_KEY: -2.0}

    record = serialize_dwelling_status(status, "ochre-house_6", 3600.0, 3600)

    assert record["loads_w"]["pv_generation"] == pytest.approx(2000.0)


def test_serialize_dwelling_status_without_pv_publishes_zero_generation() -> None:
    record = serialize_dwelling_status(_status(), "ochre-house_6", 3600.0, 3600)

    assert record["loads_w"]["pv_generation"] == 0.0


def test_pv_equipment_is_empty_without_a_capacity() -> None:
    assert pv_equipment({}) == {}


def test_pv_equipment_sizes_the_array_and_fixes_the_orientation() -> None:
    assert pv_equipment({"pv_capacity_kw": 4}) == {
        "PV": {"capacity": 4.0, "tilt": 30.0, "azimuth": 0.0}
    }


def test_serialize_dwelling_status_carries_identity_and_temperatures() -> None:
    record = serialize_dwelling_status(_status(), "ochre-house_6", 3600.0, 3600)

    assert record["federate"] == "ochre-house_6"
    assert record["time"] == 3600.0
    assert record["dt_s"] == 3600
    assert record["state"] == {"t_indoor_c": 20.5, "t_outdoor_c": -3.0}


def test_serialize_dwelling_status_is_json_serializable() -> None:
    record = serialize_dwelling_status(_status(), "ochre-house_6", 0.0, 3600)

    assert json.loads(json.dumps(record)) == record


def test_serialize_dwelling_status_names_the_missing_ochre_key() -> None:
    status = _status()
    del status["Temperature - Outdoor (C)"]

    with pytest.raises(KeyError, match="Temperature - Outdoor"):
        serialize_dwelling_status(status, "ochre-house_6", 0.0, 3600)


# ---------------------------------------------------------------------------
# _find_pub_key - the "discover your keys, never construct them" invariant
# ---------------------------------------------------------------------------


def test_find_pub_key_returns_the_key_with_that_suffix() -> None:
    federate = _federate_with_publications(
        "local-grid/ochre-house_6/load_6/active_power",
        "local-grid/ochre-house_6/state",
    )

    assert federate._find_pub_key("active_power") == (
        "local-grid/ochre-house_6/load_6/active_power"
    )


def test_find_pub_key_ignores_keys_that_only_end_with_the_same_letters() -> None:
    federate = _federate_with_publications("local-grid/ochre-house_6/throughput")

    with pytest.raises(ValueError, match="No publication ending in 'put'"):
        federate._find_pub_key("put")


def test_find_pub_key_raises_when_nothing_matches() -> None:
    federate = _federate_with_publications("local-grid/ochre-house_6/state")

    with pytest.raises(ValueError, match="local-grid/ochre-house_6/state"):
        federate._find_pub_key("active_power")


def test_find_pub_key_raises_when_more_than_one_matches() -> None:
    federate = _federate_with_publications(
        "local-grid/ochre-house_6/load_6/active_power",
        "local-grid/ochre-house_6/load_7/active_power",
    )

    with pytest.raises(ValueError, match="Multiple publications"):
        federate._find_pub_key("active_power")


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
# The CST contract this federate has to keep
# ---------------------------------------------------------------------------


def test_ochre_house_federate_subclasses_cst_federate() -> None:
    assert issubclass(OchreHouseFederate, Federate)


def test_ochre_house_federate_overrides_update_internal_model() -> None:
    """The one method CST requires a federate to provide."""
    assert (
        OchreHouseFederate.update_internal_model is not Federate.update_internal_model
    )


def test_ochre_house_federate_keeps_the_name_it_was_given() -> None:
    federate = OchreHouseFederate("local-grid.ochre-house_6")

    assert federate.federate_name == "local-grid.ochre-house_6"


def test_ochre_house_federate_resolves_no_keys_before_create_federate() -> None:
    """Interface keys exist only after `create_federate`; nothing may guess them."""
    federate = OchreHouseFederate("local-grid.ochre-house_6")

    assert federate.load_key == ""
    assert federate.reactive_power_key == ""
    assert federate.state_key == ""
    assert federate.dwelling is None


# ---------------------------------------------------------------------------
# exogenous_to_ochre_weather - a Gridlock dataset becomes OCHRE's weather table
# ---------------------------------------------------------------------------


def _exogenous_csv(days: int = 3, start: str = "2024-01-01") -> str:
    """A few days of 15-minute data in the house's `sample_data.csv` shape."""
    times = pd.date_range(start, periods=days * 96, freq="15min")
    return pd.DataFrame(
        {
            "timestamp": times,
            "ambient_temp": 5.0,
            "solar_gains_w": 400.0,
            "wind_speed_m_s": 2.0,
        }
    ).to_csv(index=False)


def test_exogenous_to_ochre_weather_has_the_columns_ochre_reads() -> None:
    weather = exogenous_to_ochre_weather(_exogenous_csv())

    assert list(weather.columns) == [
        "temp_air",
        "relative_humidity",
        "pressure",
        "ghi",
        "dni",
        "dhi",
        "wind_speed",
        "sky_temperature",
    ]
    assert weather.index.name == "Time"
    assert weather["temp_air"].iloc[0] == 5.0
    assert weather["wind_speed"].iloc[0] == 2.0


def test_exogenous_to_ochre_weather_treats_solar_as_diffuse() -> None:
    weather = exogenous_to_ochre_weather(_exogenous_csv())

    assert (weather["ghi"] == 400.0).all()
    assert (weather["dhi"] == 400.0).all()
    assert (weather["dni"] == 0.0).all()


def test_exogenous_to_ochre_weather_sky_is_colder_than_air() -> None:
    weather = exogenous_to_ochre_weather(_exogenous_csv())

    assert (weather["sky_temperature"] < weather["temp_air"]).all()


def test_exogenous_to_ochre_weather_moves_the_data_into_the_sim_year() -> None:
    weather = exogenous_to_ochre_weather(_exogenous_csv(start="2024-01-01"))

    assert weather.index[0] == pd.Timestamp(SIM_YEAR, 1, 1)
    assert weather.index[0].weekday() == pd.Timestamp("2024-01-01").weekday()
    assert weather.index[1] - weather.index[0] == pd.Timedelta(minutes=15)


def test_exogenous_to_ochre_weather_names_the_missing_column() -> None:
    csv = _exogenous_csv().replace("wind_speed_m_s", "gust")

    with pytest.raises(ValueError, match="wind_speed_m_s"):
        exogenous_to_ochre_weather(csv)


def test_exogenous_to_ochre_weather_rejects_a_leap_day() -> None:
    with pytest.raises(ValueError, match="leap day"):
        exogenous_to_ochre_weather(_exogenous_csv(days=2, start="2024-02-28"))


# ---------------------------------------------------------------------------
# write_ochre_inputs - what composegen stored becomes files OCHRE can load
# ---------------------------------------------------------------------------


def _document(weather_file: str, weather_text: str, **extra: object) -> dict:
    return {
        "hpxml_xml": "<HPXML/>",
        "schedule_csv": "occupants\n1.0\n",
        "weather_text": weather_text,
        "weather_file": weather_file,
        **extra,
    }


def test_write_ochre_inputs_writes_an_epw_as_it_is(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "SCRATCH_DIR", tmp_path)

    inputs = write_ochre_inputs(
        "ochre-house_6", _document("ochre/denver.epw", "LOCATION,Denver")
    )

    assert Path(inputs.weather_file).read_text() == "LOCATION,Denver"
    assert inputs.weather_file.endswith(".epw")
    assert inputs.weather_metadata is None
    assert inputs.extra_args == {}
    assert Path(inputs.hpxml_file).read_text() == "<HPXML/>"
    assert Path(inputs.schedule_file).read_text() == "occupants\n1.0\n"


def test_write_ochre_inputs_converts_csv_weather_and_keeps_the_location(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "SCRATCH_DIR", tmp_path)
    location = {"latitude": 48.14, "longitude": 11.58, "timezone": 1.0}

    inputs = write_ochre_inputs(
        "ochre-house_6",
        _document("sample_data.csv", _exogenous_csv(), weather_location=location),
    )

    written = pd.read_csv(inputs.weather_file, index_col="Time", parse_dates=True)
    assert "temp_air" in written
    assert inputs.weather_metadata == location
    assert inputs.extra_args == {
        "Mains Temperature (C)": 5.0,
        "Ground Temperature (C)": 5.0,
    }
    assert inputs.start_time == pd.Timestamp(SIM_YEAR, 1, 1)


def test_write_ochre_inputs_csv_weather_without_location_raises(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "SCRATCH_DIR", tmp_path)

    with pytest.raises(ValueError, match="no location"):
        write_ochre_inputs(
            "ochre-house_6", _document("sample_data.csv", _exogenous_csv())
        )


def test_write_ochre_inputs_names_what_is_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "SCRATCH_DIR", tmp_path)

    with pytest.raises(ValueError, match="hpxml_xml"):
        write_ochre_inputs("ochre-house_6", {})


# ---------------------------------------------------------------------------
# One real Dwelling step - the loop update_internal_model runs
# ---------------------------------------------------------------------------


def _bundled_document(weather: str) -> dict:
    """OCHRE's own example files, shaped like what composegen stores."""
    from ochre.utils import default_input_path

    def read(*parts: str) -> str:
        with open(os.path.join(default_input_path, *parts)) as handle:
            return handle.read()

    return _document(
        "bundled.epw",
        read("Weather", "USA_CO_Denver.Intl.AP.725650_TMY3.epw"),
        hpxml_xml=read("Input Files", "bldg0112631-up11.xml"),
        schedule_csv=read("Input Files", "bldg0112631_schedule.csv"),
    ) | (
        {"weather_file": "sample_data.csv", "weather_text": weather} if weather else {}
    )


def _step(inputs: main.OchreInputs) -> list[float]:
    dwelling = setup_dwelling(inputs, 3600, 3 * 3600)
    return [
        serialize_dwelling_status(
            dwelling.update(), "ochre-house_6", step * 3600.0, 3600
        )["grid_exchange"]["total_load_w"]
        for step in range(3)
    ]


def test_setup_dwelling_steps_on_epw_weather(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Needs the pinned OCHRE; it is in requirements.txt, so the image has it."""
    pytest.importorskip("ochre")
    monkeypatch.setattr(main, "SCRATCH_DIR", tmp_path)

    loads = _step(write_ochre_inputs("ochre-house_6", _bundled_document("")))

    assert all(load >= 0.0 for load in loads)
    assert any(load > 0.0 for load in loads)


def test_setup_dwelling_steps_on_csv_weather(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("ochre")
    monkeypatch.setattr(main, "SCRATCH_DIR", tmp_path)
    document = _bundled_document(_exogenous_csv(days=4)) | {
        "weather_location": {"latitude": 48.14, "longitude": 11.58, "timezone": 1.0}
    }

    loads = _step(write_ochre_inputs("ochre-house_6", document))

    assert all(load >= 0.0 for load in loads)
    assert any(load > 0.0 for load in loads)


# Inside the Docker test stage the file sits in a two-level path with no repo above it.
EXAMPLE_HPXML = next(
    (
        parent / "data" / "input" / "ochre" / "house-example.xml"
        for parent in Path(__file__).resolve().parents
        if (parent / "data" / "input" / "ochre" / "house-example.xml").exists()
    ),
    None,
)


@pytest.mark.skipif(
    EXAMPLE_HPXML is None,
    reason="data/ is outside the image's build context; runs from a repo checkout",
)
def test_example_hpxml_house_heats_itself_on_csv_weather(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repo's compact all-electric HPXML house loads and holds its setpoint."""
    pytest.importorskip("ochre")
    monkeypatch.setattr(main, "SCRATCH_DIR", tmp_path)
    document = _bundled_document(_exogenous_csv(days=4)) | {
        "pv_capacity_kw": 4.0,
        "hpxml_xml": EXAMPLE_HPXML.read_text(),
        "weather_location": {
            "latitude": 48.14,
            "longitude": 11.58,
            "timezone": 1.0,
            "altitude": 520.0,
        },
    }
    inputs = write_ochre_inputs("ochre-house_6", document)
    dwelling = setup_dwelling(inputs, 3600, 24 * 3600)

    records = [
        serialize_dwelling_status(
            dwelling.update(), "ochre-house_6", hour * 3600.0, 3600
        )
        for hour in range(24)
    ]

    assert max(r["loads_w"]["heating"] for r in records) > 0.0
    assert all(15.0 < r["state"]["t_indoor_c"] < 25.0 for r in records)
    assert max(r["loads_w"]["pv_generation"] for r in records) > 0.0
