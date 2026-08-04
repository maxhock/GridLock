"""Translate a pandapower-style Excel workbook into a PyPSA network."""

import pandas as pd
import pypsa
import numpy as np


class PyPSANetworkBuilder:
    """
    Build and simulate a PyPSA electrical network from an Excel file.

    Required Excel sheets: 'bus', 'ext_grid', 'line', 'load'.
    Optional sheets: 'trafo', 'gen', 'sgen', 'shunt'.

    Note: Input data from Excel sheets uses 'input_' prefix (e.g., input_bus).
    PyPSA network components use standard names (e.g., net.buses, net.lines).
    This naming convention prevents confusion between input data and simulation results.
    """

    def __init__(self, file_path: str) -> None:
        """Read the workbook and check it holds the sheets a network needs."""
        self.network_file: str = file_path
        self._load_data_from_excel()
        # Check for required sheets using original (user-facing) sheet names
        required_sheets = ["bus", "ext_grid", "line", "load"]
        missing_sheets = [sheet for sheet in required_sheets if sheet not in self.data]
        if missing_sheets:
            raise ValueError(
                f"Required sheet(s) {missing_sheets} not found in {file_path}. "
                f"Expected the following sheets: {required_sheets}."
            )
        self.net: pypsa.Network = pypsa.Network()

    def _load_data_from_excel(self):
        """Expose every sheet as an `input_<sheet>` attribute."""
        self.data = pd.read_excel(self.network_file, sheet_name=None)
        for sheet, df in self.data.items():
            clean_name = "input_" + sheet.strip().replace(" ", "_")
            setattr(self, clean_name, df)

    def _add_buses(self):
        """Add every bus at its nominal voltage."""
        for row in self.input_bus.itertuples():
            self.net.add("Bus", name=str(row.Index), v_nom=row.vn_kv)

    def _add_ext_grid(self):
        """Model the external grid as the network's slack generator."""
        bus = str(self.input_ext_grid.bus[0])
        self.net.add("Generator", name="Slack", bus=bus, control="Slack")
        self.net.buses.loc[bus, "v_mag_pu_set"] = self.input_ext_grid.vm_pu[0]
        self.ext_grid_idx = bus

    def _add_trafos(self):
        """Add each transformer, converting its percentages to impedances."""
        for row in self.input_trafo.itertuples():
            bus_from = str(row.hv_bus)
            bus_to = str(row.lv_bus)
            r_pu = row.vkr_percent/100
            x_pu = np.sqrt(row.vk_percent**2 -
                           row.vkr_percent**2)/100
            self.net.add(
                "Transformer", name=str(row.Index),
                bus0=bus_from, bus1=bus_to,
                s_nom=row.sn_mva,
                r=r_pu, x=x_pu,
                model="t",
            )

    def _add_lines(self):
        """Add each line, scaling its per-km impedance by its length."""
        for row in self.input_line.itertuples():
            self.net.add(
                "Line", name=str(row.Index),
                bus0=str(row.from_bus),
                bus1=str(row.to_bus),
                length=row.length_km,
                r=row.length_km * row.r_ohm_per_km,
                x=row.length_km * row.x_ohm_per_km,
            )

    def _add_loads(self):
        """Add each load at its workbook setpoint; HELICS overwrites these per step."""
        for row in self.input_load.itertuples():
            self.net.add(
                "Load", name=str(row.Index),
                bus=str(row.bus),
                p_set=row.p_mw,  q_set=-row.q_mvar
            )

    def _add_gens(self):
        """Add each voltage-controlled generator and set its bus's voltage target."""
        for row in self.input_gen.itertuples():
            self.net.add(
                "Generator", name=str(row.Index),
                bus=str(row.bus),
                control="PV", p_set=row.p_mw
            )
            # voltage is a bus property so it is set there
            self.net.buses.loc[str(row.bus),
                               "v_mag_pu_set"] = row.vm_pu

    def _add_sgens(self):
        """Add each static generator, prefixed so it cannot clash with a generator."""
        for row in self.input_sgen.itertuples():
            self.net.add(
                "Generator", name=f"s_gen{str(row.Index)}",
                bus=str(row.bus),
                control="PV", p_set=row.p_mw
            )

    def _add_shunts(self):
        """Add each shunt, converting its power rating to an admittance."""
        for row in self.input_shunt.itertuples():
            self.net.add(
                "ShuntImpedance", name=str(row.Index),
                bus=str(row.bus),
                g=row.p_mw/row.vn_kv**2,
                b=-row.q_mvar/row.vn_kv**2
            )

    # Properties for convenient access to PyPSA network components.
    # Additional components can be accessed similarly if needed.

    @property
    def loads(self) -> pd.DataFrame:
        """Access PyPSA load objects (input data)."""
        return self.net.loads

    @property
    def buses(self) -> pd.DataFrame:
        """Access PyPSA bus objects and their properties."""
        return self.net.buses

    @property
    def lines(self) -> pd.DataFrame:
        """Access PyPSA transmission line objects."""
        return self.net.lines

    @property
    def generators(self) -> pd.DataFrame:
        """Access PyPSA generator objects (power plants)."""
        return self.net.generators

    def create_network(self):
        """Build the network, adding the optional components only where sheets exist."""
        self._add_buses()
        self._add_ext_grid()
        self._add_lines()
        self._add_loads()
        if hasattr(self, "input_trafo"):
            self._add_trafos()
        if hasattr(self, "input_gen"):
            self._add_gens()
        if hasattr(self, "input_shunt"):
            self._add_shunts()
        if hasattr(self, "input_sgen"):
            self._add_sgens()

    def run_pf(self):
        """Solve the power flow."""
        self.net.pf()

    def res_ext_grid(self) -> float:
        """Report the power drawn through the external grid connection, in MW.

        Any load sitting on the slack bus is added back in, since the bus result nets it
        off against the infeed.
        """
        if self.net.buses_t.p.empty:
            raise RuntimeError(
                "Power flow must be run before accessing results.")

        if not self.net.loads[self.net.loads.bus == str(self.ext_grid_idx)].empty:
            return (
                self.net.buses_t.p[str(self.ext_grid_idx)].iloc[0]
                + self.net.loads[self.net.loads.bus ==
                                 str(self.ext_grid_idx)]["p_set"].sum()
            )
        else:
            return self.net.buses_t.p[str(self.ext_grid_idx)].iloc[0]
