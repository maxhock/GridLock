# Grid PyPSA Federate

PyPSA-based grid federate for GridLock co-simulation platform. This is a drop-in replacement for the pandapower grid federate.

## Overview

This federate uses [PyPSA](https://pypsa.org/) for power flow simulation instead of pandapower. It provides the same HELICS interface and can be used interchangeably with the pandapower grid federate.

## Features

- **Same input format**: Uses pandapower Excel files (with sheets: bus, load, ext_grid, line, trafo)
- **Same HELICS interface**: Compatible pub/sub topics for seamless integration
- **Drop-in replacement**: Switch by changing federate in experiment.yml
- **PyPSA power flow**: Uses PyPSA's native power flow solver

## Usage

### In experiment.yml

Replace `grid:` with `grid_pypsa:`:

```yaml
federates:
  broker:
    name: broker
    build_folder: ${.name}
  grid_pypsa:
    name: grid
    build_folder: grid_pypsa
    grid_file: kerber_landnetz_freileitung_1.xlsx
  # ... other federates
```

See `config/experiment_pypsa.yml` for a complete example.

### HELICS Interface

**Subscriptions** (inputs from other federates):
- `node_{idx}/P` - Active power for load at node idx (MW)

**Publications** (outputs to other federates):
- `Grid/transformer_{idx}_power` - Active power from generator/external grid at idx (MW)

This matches the pandapower grid federate interface exactly.

## Implementation

### Key Components

- `GridPyPSAFederate`: Main federate class extending CoSim Toolbox Federate
- `load_pypsa_from_pandapower_excel()`: Converts pandapower Excel to PyPSA network
- `update_internal_model()`: Runs PyPSA power flow each timestep

### pandapower to PyPSA Mapping

| pandapower Component | PyPSA Component |
|---------------------|-----------------|
| bus                 | Bus             |
| load                | Load            |
| ext_grid            | Generator (Slack) |
| line                | Line            |
| trafo               | Transformer     |

### Power Flow

Uses PyPSA's `network.pf()` method for AC power flow calculation.

## Testing

Run tests during Docker build:
```bash
docker build --target test -t grid-pypsa-test ./grid_pypsa
```

Or run tests locally:
```bash
cd grid_pypsa
pip install -r requirements.txt
pip install pytest pytest-mock
pytest -v test_main.py
```

## Dependencies

- cosim-toolbox: HELICS federate framework
- helics[cli]: HELICS Python bindings and CLI tools
- pypsa: Power System Analysis library
- openpyxl: Excel file reading
- pandas: Data manipulation

## Differences from pandapower Grid

While the interface is identical, there are some internal differences:

1. **Power flow solver**: Uses PyPSA's native solver instead of pandapower's
2. **Component representation**: Internal network structure follows PyPSA conventions
3. **Timestep handling**: PyPSA uses snapshot-based approach

Both federates should produce similar results for standard power flow calculations.

## Advantages of PyPSA

- **Optimization support**: PyPSA has built-in optimization capabilities
- **Time series**: Native support for multi-period optimization
- **Extensibility**: Easier to add generation/storage optimization in the future
- **Performance**: Can be faster for larger networks with PyPSA's optimized solvers

## Future Enhancements

Possible future additions:
- Multi-period optimization
- Unit commitment
- Economic dispatch
- Storage optimization
- Renewable energy integration scenarios
