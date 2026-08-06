"""Starting point for a new federate class: the minimum a CST federate has to provide.

CST owns the HELICS lifecycle, the time loop and the data exchange. A new federate
subclasses `Federate`, must override `update_internal_model`, and may override
`create_federate` and `on_enter_executing_mode` for setup and for the t=0 state.

The example below is deliberately physics-free: it discovers one subscription and one
publication, scales what it reads, and publishes the result. Replace
`update_internal_model` with your model; keep the shape around it. See README.md in
this directory for the walkthrough, including the places composegen has to learn about
a new class.

Usage:
    python main.py --scenario TestGridScenario --federate_name local-grid.template_0
"""

import argparse
import os
from typing import Any

from cosim_toolbox.sims import Federate


# ---------------------------------------------------------------------------
# TemplateFederate
# ---------------------------------------------------------------------------

# The suffixes this federate looks for among the interfaces composegen registered
# for it. They are *suffixes*, not keys: composegen derives the full key from the
# experiment tree (e.g. `local-grid/house_4/state`), so the only stable part is the
# last segment your class agreed on in `_wire_grid_child`.
_INPUT_SUFFIX = "input"
_OUTPUT_SUFFIX = "output"


def _find_interface_key(
    interfaces: dict[str, Any], suffix: str, federate_name: str, kind: str
) -> str:
    """Find the one registered interface key ending in ``suffix``.

    Federates never construct their HELICS keys - composegen derives key names from
    the experiment tree, not from a federate's own name, so a federate that builds
    ``f"{self.federate_name}/active_power"`` registers a key nobody publishes to.
    Look the key up in the config CST handed you instead.

    Raises when there is no match or more than one, because both mean the federation
    was generated against a different wiring than this federate expects.
    """
    matches = [key for key in interfaces if key == suffix or key.endswith(f"/{suffix}")]

    if not matches:
        raise ValueError(
            f"No {kind} ending in '{suffix}' found for federate '{federate_name}'. "
            f"Registered {kind}s: {sorted(interfaces)}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Multiple {kind}s ending in '{suffix}' found for federate "
            f"'{federate_name}': {sorted(matches)}"
        )

    return matches[0]


class TemplateFederate(Federate):
    """Example federate: republishes what it reads, scaled by a constant gain."""

    def __init__(self, federate_name: str, gain: float = 1.0) -> None:
        """Bind this federate to whatever it simulates.

        The HELICS interfaces do not exist yet here - resolve anything that depends
        on them in `create_federate`.
        """
        super().__init__(federate_name)
        self.gain = gain
        self.input_key = ""
        self.output_key = ""

    def create_federate(
        self,
        scenario_name: str,
        use_meta_db: str = "mongo",
        use_data_db: str = "postgres",
    ) -> None:
        """Create the CST federate, then resolve the interface keys it registered.

        The base call reads the federation config from the CST metadata store and
        registers this federate's publications and subscriptions. Override to load
        data, build a model, or resolve keys - always after `super()`. A federate
        that needs none of that can leave this out entirely, as the grid does.
        """
        super().create_federate(scenario_name, use_meta_db, use_data_db)

        self.input_key = _find_interface_key(
            self.data_from_federation["inputs"],
            _INPUT_SUFFIX,
            self.federate_name,
            "subscription",
        )
        self.output_key = _find_interface_key(
            self.data_to_federation["publications"],
            _OUTPUT_SUFFIX,
            self.federate_name,
            "publication",
        )

        print(
            f"Federate '{self.federate_name}' reading {self.input_key}, "
            f"publishing {self.output_key}."
        )

    def update_internal_model(self) -> None:
        """Advance the simulation by one step - the one method a federate must provide.

        Called every step once CST has granted the time and filled
        `self.data_from_federation`; whatever is left in `self.data_to_federation`
        is published afterwards. `self.granted_time` is simulation seconds,
        `self.period` the time step.
        """
        value = float(self.data_from_federation["inputs"][self.input_key])

        # Your model goes here. Read from self.data_from_federation["inputs"],
        # advance whatever state you kept on self, and write the results into
        # self.data_to_federation["publications"].
        result = value * self.gain

        self.data_to_federation["publications"][self.output_key] = result

        print(f"t={self.granted_time:.0f}s  in={value:.2f}  out={result:.2f}")

    def on_enter_executing_mode(self) -> None:
        """Publish an initial state at t=0, before the first time step.

        Optional, but usually not: an unpublished `double` input reads as 0.0, which
        no subscriber can tell apart from a real zero. Whoever depends on this
        federate would spend the first step simulating against a value it invented.
        """
        self.data_to_federation["publications"][self.output_key] = 0.0
        self.send_data_to_federation()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Read the scenario and federate name composegen put on the command line.

    Both are required: composegen always passes them, and the federate name is what
    selects this federate's entry in the federation config. Add your own arguments
    for anything else composegen has to tell your class - see the `--timeseries` of
    the load player or the `--horizon` of the controller.
    """
    parser = argparse.ArgumentParser(description="Template federate using CST")
    parser.add_argument(
        "--scenario",
        type=str,
        required=True,
        help="CST scenario name (e.g. TestGridScenario)",
    )
    parser.add_argument(
        "--federate_name",
        type=str,
        required=True,
        help="Federate name matching meta_store entry (e.g. local-grid.template_0)",
    )
    args, _ = parser.parse_known_args()
    return args


def get_db_backends_from_env() -> tuple[str, str]:
    """Read DB backends from environment variables set by composegen.

    Raises rather than defaulting: the backends come from the experiment's
    ``general:`` block, and a federate that quietly falls back to a different store
    than the rest of the federation writes its results where nobody looks for them.
    """
    use_meta_db = os.getenv("CST_USE_META_DB")
    use_data_db = os.getenv("CST_USE_DATA_DB")

    if not use_meta_db or not use_data_db:
        raise ValueError(
            "Missing DB backend env vars. Expected CST_USE_META_DB and "
            "CST_USE_DATA_DB from experiment general config."
        )

    return use_meta_db, use_data_db


def run_template_federate(
    federate_name: str,
    scenario_name: str,
    use_meta_db: str,
    use_data_db: str,
) -> None:
    """Run one template federate: CST's ``run()`` does create, loop, destroy."""
    federate = TemplateFederate(federate_name)
    federate.run(
        scenario_name,
        use_meta_db=use_meta_db,
        use_data_db=use_data_db,
    )


def main(
    scenario_name: str | None = None,
    federate_name: str | None = None,
) -> None:
    """Run one federate, taking the names from the CLI unless they are given."""
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name

    if scenario_name is None or federate_name is None:
        raise ValueError("scenario_name and federate_name are required")

    use_meta_db, use_data_db = get_db_backends_from_env()

    run_template_federate(
        federate_name,
        scenario_name,
        use_meta_db,
        use_data_db,
    )


if __name__ == "__main__":
    main()
