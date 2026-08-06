"""Starting point for a new federate class: the minimum a CST federate has to provide.

CST owns the HELICS lifecycle, the time loop and the data exchange. A new federate
subclasses `Federate`, must override `update_internal_model`, and may override
`create_federate` and `on_enter_executing_mode` for setup and for the t=0 state.

Copy this directory, rename the class, and register it in composegen - see README.md
in this directory for the walkthrough and for the four places composegen has to learn
about a new class.

The example below is deliberately physics-free: it discovers one subscription and one
publication, scales what it reads, and publishes the result. Replace
`update_internal_model` with your model; keep the shape around it.
"""

import argparse
import os
from typing import Any, Mapping

from cosim_toolbox.sims import Federate

# The suffixes this federate looks for among the interfaces composegen registered
# for it. They are *suffixes*, not keys: composegen derives the full key from the
# experiment tree (e.g. `local-grid/house_4/state`), so the only stable part is the
# last segment your class agreed on in `_wire_grid_child`.
INPUT_SUFFIX = "input"
OUTPUT_SUFFIX = "output"


def find_interface_key(
    interfaces: Mapping[str, Any], suffix: str, federate_name: str, kind: str
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


def read_db_backends(env: Mapping[str, str]) -> tuple[str, str]:
    """Read the CST backends composegen put in the environment.

    Raises rather than defaulting: the backends come from the experiment's
    ``general:`` block, and a federate that quietly falls back to a different store
    than the rest of the federation writes its results where nobody looks for them.
    """
    use_meta_db = env.get("CST_USE_META_DB")
    use_data_db = env.get("CST_USE_DATA_DB")

    if not use_meta_db or not use_data_db:
        raise ValueError(
            "Missing DB backend env vars. Expected CST_USE_META_DB and "
            "CST_USE_DATA_DB from the experiment's general config."
        )

    return use_meta_db, use_data_db


class TemplateFederate(Federate):
    """Example federate: republishes what it reads, scaled by a constant gain."""

    def __init__(self, federate_name: str, gain: float = 1.0) -> None:
        """Hold whatever state the simulation needs across steps.

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
        """Set up anything the simulation needs once the HELICS interfaces exist.

        The base call reads the federation config from the CST metadata store and
        registers this federate's publications and subscriptions. Override to load
        data, build a model, or resolve interface keys - always after `super()`.
        """
        super().create_federate(scenario_name, use_meta_db, use_data_db)

        self.input_key = find_interface_key(
            self.data_from_federation["inputs"],
            INPUT_SUFFIX,
            self.federate_name,
            "subscription",
        )
        self.output_key = find_interface_key(
            self.data_to_federation["publications"],
            OUTPUT_SUFFIX,
            self.federate_name,
            "publication",
        )

        print(
            f"Federate '{self.federate_name}' reading {self.input_key}, "
            f"publishing {self.output_key}."
        )

    def on_enter_executing_mode(self) -> None:
        """Publish an initial state at t=0, before the first time step.

        Optional, but usually not: an unpublished `double` input reads as 0.0, which
        no subscriber can tell apart from a real zero. Whoever depends on this
        federate would spend the first step simulating against a value it invented.
        """
        self.data_to_federation["publications"][self.output_key] = 0.0
        self.send_data_to_federation()

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


def parse_args() -> argparse.Namespace:
    """Read the scenario and federate name composegen put on the CLI.

    Both are required: composegen always passes them, and the federate name is what
    selects this federate's entry in the federation config. Add your own arguments
    here for anything composegen has to tell your class - see the `--timeseries` of
    the load player or the `--horizon` of the controller.
    """
    parser = argparse.ArgumentParser(description="Template CST/HELICS federate")
    parser.add_argument(
        "--scenario",
        type=str,
        required=True,
        help="CST scenario name (e.g. TestGrid_20260804_120000)",
    )
    parser.add_argument(
        "--federate_name",
        type=str,
        required=True,
        help="Federate name matching the federation config (e.g. local-grid.template_0)",
    )
    # parse_known_args, not parse_args: composegen appends arguments per class, and
    # an unrecognised one should not take the container down.
    args, _ = parser.parse_known_args()
    return args


def main(scenario_name: str | None = None, federate_name: str | None = None) -> None:
    """Run one federate, taking the names from the CLI unless they are given.

    `run()` is the whole lifecycle: create the federate, loop until stop time,
    destroy it. Call `create_federate` / `run_cosim_loop` / `destroy_federate`
    yourself only if you need to do something between those steps.
    """
    if scenario_name is None:
        args = parse_args()
        scenario_name = args.scenario
        federate_name = args.federate_name

    if scenario_name is None or federate_name is None:
        raise ValueError("scenario_name and federate_name are required")

    use_meta_db, use_data_db = read_db_backends(os.environ)

    federate = TemplateFederate(federate_name)
    federate.run(scenario_name, use_meta_db=use_meta_db, use_data_db=use_data_db)


if __name__ == "__main__":
    main()
