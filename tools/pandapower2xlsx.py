#!/usr/bin/env python3
"""Export pandapower's stock Kerber grids to `data/input/` for use as local layouts."""

import os
import pandapower as pp
import pandapower.networks as pn

net = pn.create_kerber_landnetz_freileitung_1()
excel_path = os.path.join(
    str(os.getenv("PWD")), "data", "input", "kerber_landnetz_freileitung_1.xlsx"
)
pp.to_excel(net, excel_path)
print(f"Exported pandapower network to {excel_path}")

net = pn.create_kerber_landnetz_kabel_1()
excel_path = os.path.join(
    str(os.getenv("PWD")), "data", "input", "kerber_landnetz_kabel_1.xlsx"
)
pp.to_excel(net, excel_path)
print(f"Exported pandapower network to {excel_path}")
