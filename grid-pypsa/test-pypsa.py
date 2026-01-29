"""
This is a test file for validating the PyPSANetwork implementation against pandapower.
It compares the active power at the external grid (slack bus) for several standard test cases.
To use: Place the required Excel grid files in the working directory and run this script.
"""

import pandas as pd
import numpy as np
import pandapower as pp
import pypsa

from utils import PyPSANetwork

# Reference results for reproducibility
reference_results = {
    'kerber_landnetz_freileitung_2.xlsx': np.float64(0.35),
    'case6ww.xlsx': np.float64(0.6751),
    'case9.xlsx': np.float64(0.1461),
    'case30.xlsx': np.float64(0.375),
    'case4gs.xlsx': np.float64(0.1477),
    'case5.xlsx': np.float64(1.1085),
    'case14.xlsx': np.float64(0.0934),
    'case118.xlsx': np.float64(0.1993)
}

paths = list(reference_results.keys())
results = []

for file_path in paths:
    pyp_net = PyPSANetwork(file_path)
    pp_net = pp.from_excel(file_path)
    pp.runpp(pp_net)
    pp_p = pp_net.res_ext_grid.p_mw.iloc[0]
    pyp_net.create_network()
    pyp_net.run_pf()
    pyp_p = pyp_net.res_ext_grid()
    error_pct = abs(pyp_p - pp_p) / abs(pp_p) * \
        100 if pp_p != 0 else float('inf')
    results.append({
        "Case": file_path,
        "PandaPower Active Power (MW)": pp_p,
        "PyPSA Active Power (MW)": pyp_p,
        "Error (%)": error_pct,
    })

# Print results in a table format
print("\nPyPSA vs. pandapower Power Flow Comparison")
print("-" * 90)
print(f"{'Case':<30} {'PP Power (MW)':>15} {'PyPSA (MW)':>12} {'RefPyPSA (MW)':>15} {'Err vs PP (%)':>13} {'Err vs Ref (%)':>13}")
print("-" * 90)
for res in results:
    print(f"{res['Case']:<30} {res['PandaPower Active Power (MW)']:>15.4f} {res['PyPSA Active Power (MW)']:>12.4f} {res['Error (%)']:>13.2f}")
print("-" * 90)
