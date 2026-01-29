import pandas as pd
import pypsa
import numpy as np


class PyPSANetworkBuilder:
    """
    Utility class to create and simulate a PyPSA network from an Excel file.
    Requires at minimum the following sheets: 'bus', 'ext_grid', 'line', 'load'.
    """

    def __init__(self, file_path: str) -> None:
        self.network_file: str = file_path
        self._load_data()
        # Check for required sheets
        required = ["bus", "ext_grid", "line", "load"]
        for req in required:
            if not hasattr(self, req):
                raise ValueError(
                    f"Required sheet '{req}' not found in {file_path}.")
        self.net: pypsa.Network = pypsa.Network()

    def _load_data(self):
        self.data = pd.read_excel(self.network_file, sheet_name=None)
        for sheet, df in self.data.items():
            clean_name = sheet.strip().replace(" ", "_")
            setattr(self, clean_name, df)

    def _add_buses(self):
        if self.bus.name.isna().any():
            self.bus.name = range(self.bus.name.shape[0])
        for row in self.bus.itertuples():
            self.net.add(
                "Bus", name=str(row.Index), v_nom=row.vn_kv
            )

    def _add_ext_grid(self):
        bus = self.ext_grid.bus[0]
        self.net.add(
            "Generator", name="Slack",
            bus=str(self.net.buses.index[bus]), control="Slack")
        self.net.buses.loc[self.net.buses.index[bus],
                           "v_mag_pu_set"] = self.ext_grid.vm_pu[0]
        self.ext_grid_idx = bus

    def _add_trafos(self):
        if self.trafo.name.isna().any():
            self.trafo.name = range(self.trafo.name.shape[0])
        for row in self.trafo.itertuples(index=False):
            bus_from = self.net.buses.index[row.hv_bus]
            bus_to = self.net.buses.index[row.lv_bus]
            r_pu = row.vkr_percent/100
            x_pu = np.sqrt(row.vk_percent**2 -
                           row.vkr_percent**2)/100
            self.net.add(
                "Transformer", name=str(row.name),
                bus0=bus_from, bus1=bus_to,
                s_nom=row.sn_mva,
                r=r_pu, x=x_pu,
                model="t",
            )

    def _add_lines(self):
        if self.line.name.isna().any():
            self.line.name = range(self.line.name.shape[0])
        for row in self.line.itertuples(index=False):
            self.net.add(
                "Line", name=str(row.name),
                bus0=self.net.buses.index[row.from_bus],
                bus1=self.net.buses.index[row.to_bus],
                length=row.length_km,
                r=row.length_km * row.r_ohm_per_km,
                x=row.length_km * row.x_ohm_per_km,
            )

    def _add_loads(self):
        if self.load.name.isna().any():
            self.load.name = range(self.load.name.shape[0])
        for row in self.load.itertuples(index=False):
            self.net.add(
                "Load", name=str(row.name),
                bus=self.net.buses.index[row.bus],
                p_set=row.p_mw,  q_set=-row.q_mvar
            )

    def _add_gens(self):
        if self.gen.name.isna().any():
            self.gen.name = range(self.gen.name.shape[0])
        for row in self.gen.itertuples(index=False):
            self.net.add(
                "Generator", name=str(row.name),
                bus=self.net.buses.index[row.bus],
                control="PV", p_set=row.p_mw
            )
            # votlage is a bus property so it is set there
            self.net.buses.loc[self.net.buses.index[row.bus],
                               "v_mag_pu_set"] = row.vm_pu

    def _add_sgens(self):
        if self.sgen.name.isna().any():
            self.sgen.name = [f"sgen_{i}" for i in range(len(self.sgen))]
        for row in self.sgen.itertuples(index=False):
            self.net.add(
                "Generator", name=row.name,
                bus=self.net.buses.index[row.bus],
                control="PV", p_set=row.p_mw
            )

    def _add_shunts(self):
        if self.shunt.name.isna().any():
            self.shunt.name = range(self.shunt.name.shape[0])
        for row in self.shunt.itertuples(index=False):
            self.net.add(
                "ShuntImpedance", name=str(row.name),
                bus=self.net.buses.index[row.bus],
                g=row.p_mw/row.vn_kv**2,
                b=-row.q_mvar/row.vn_kv**2
            )

    def create_network(self):
        self._add_buses()
        self._add_ext_grid()
        self._add_lines()
        self._add_loads()
        if hasattr(self, "trafo"):
            self._add_trafos()
        if hasattr(self, "gen"):
            self._add_gens()
        if hasattr(self, "shunt"):
            self._add_shunts()
        if hasattr(self, "sgen"):
            self._add_sgens()

    def run_pf(self):
        self.net.pf()

    def res_ext_grid(self) -> float:
        """
        Returns the power at the external grid bus after power flow calculation.
        If there are loads at the ext_grid bus, their setpoint is added to the bus power.
        Returns:
            float: Power at the external grid bus (MW)
        """
        if not self.net.loads[self.net.loads.bus == str(self.ext_grid_idx)].empty:
            return (
                self.net.buses_t.p[str(self.ext_grid_idx)].iloc[0]
                + self.net.loads[self.net.loads.bus ==
                                 str(self.ext_grid_idx)]["p_set"].iloc[0]
            )
        else:
            return self.net.buses_t.p[str(self.ext_grid_idx)].iloc[0]
