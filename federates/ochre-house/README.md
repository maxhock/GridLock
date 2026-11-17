# ochre-house federate

A Gridlock house backed by an OCHRE `Dwelling`, stepped externally once per
Gridlock timestep.

The Dwelling is built from three files the experiment names for each house:
`hpxml` (the building), `schedule` (occupants and loads) and `weather`
(an EPW, or a CSV in the `house` federate's `exogenous_data` shape). composegen
stores them in the CST metadata store; `main.py` writes them to a scratch
directory and hands the paths to OCHRE. See
[`docs/experiment-reference.md`](../../docs/experiment-reference.md) for the keys,
and `config/experiment-ochre-house.yml` for an example next to a `house`.

Each step the federate publishes `active_power`, `reactive_power` and a `state` JSON
in the same envelope as the `house` federate's, so the two can be compared from the
results DB.

`data/input/ochre/house-example.xml` is the reference building: the detailed counterpart
to the `house` federate's simple one, small enough to read. It is the file the
tests run against, and what a building derived from INF-DB data should be
checked against.

PV is optional and not part of the HPXML (OCHRE 0.9.2 does not read it from there):
set `pv_capacity_kw` on the house in the experiment YAML and the federate adds a
south-facing array tilted 30 degrees, like the `house` federate's default one. It is
published as `loads_w.pv_generation` (positive watts, as the `house` does) and is
already netted into the total power.

## Why runs start in 2018 and CSV weather may not contain 29 February

OCHRE reads weather and schedules as one annual table of exactly 365 days
(`ochre/utils/schedule.py:set_annual_index`). It derives the time resolution from the
row count (8760 hours, 525,600 minutes) and raises `Cannot parse data for a leap
year.` when the start year has 366 days. I found no stated reason in OCHRE's docs; it
follows from its inputs being typical-year data (ResStock, EnergyPlus-style) that is
always 365 days long. For an `.epw` with 8784 rows OCHRE drops 29 February itself.

For this federate that means two things:

- The Dwelling starts in `SIM_YEAR = 2018`, whatever year the dataset is from. Whole
  years are shifted, so the calendar date and the dataset's order are kept. 2018 starts
  on a Monday, like 2024 (the year of `sample_data.csv`), so the weekday pattern of the
  schedule stays aligned.
- A CSV weather file with a 29 February is rejected with an error instead of being
  trimmed like an `.epw`, so a run never silently drops a day of data. Delete the day
  from the dataset, or use another year.

Not done yet: the equipment (PV, battery, heat pump, HEMS) is not split into
subfederates, and the building is not derived from INF-DB data.
