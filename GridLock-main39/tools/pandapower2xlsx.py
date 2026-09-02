#!/usr/bin/env python3
import os
import pandapower as pp
import pandapower.networks as pn

# File: tools/pandapower2xlsx.py
# Purpose: load a pandapower default network and export it to ../data/input

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
