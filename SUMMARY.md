# PyPSA Grid Federate Implementation Summary

## Overview
This document summarizes the implementation of the PyPSA grid federate as an alternative to the Pandapower grid federate in the GridLock co-simulation platform.

## Objective
Add PyPSA as an alternative power flow simulation engine that serves as a drop-in replacement for Pandapower, using the same input format and HELICS interface.

## Implementation

### New Components

#### 1. grid_pypsa/main.py
- **GridPyPSAFederate class**: Extends CoSim Toolbox Federate
- **load_pypsa_from_pandapower_excel()**: Converts Pandapower Excel to PyPSA network
- **Component mapping**:
  - Pandapower bus → PyPSA Bus
  - Pandapower load → PyPSA Load
  - Pandapower ext_grid → PyPSA Generator (Slack)
  - Pandapower line → PyPSA Line
  - Pandapower trafo → PyPSA Transformer

#### 2. grid_pypsa/test_main.py
- Unit tests matching the structure of grid/test_main.py
- Tests for federate initialization, power flow execution, and lifecycle

#### 3. grid_pypsa/Dockerfile
- Python 3.11-slim base image
- Multi-stage build with test stage
- Same structure as grid/Dockerfile

#### 4. grid_pypsa/requirements.txt
- cosim-toolbox
- helics[cli]
- pypsa
- openpyxl

#### 5. grid_pypsa/README.md
- Comprehensive documentation
- Usage examples
- Component mapping table
- Testing instructions

#### 6. config/experiment_pypsa.yml
- Example configuration using grid_pypsa federate

### Modified Components

#### 1. composegen/main.py
- Added "grid_pypsa" to SKIP_NODE_ASSIGNMENT set
- Added grid_pypsa command template
- Updated load_and_prepare_config() to detect either grid or grid_pypsa
- Updated create_grid_config() to accept grid_fed_name parameter
- Updated main() to determine grid federate type dynamically

#### 2. composegen/test_main.py
- Updated test_create_grid_config() to pass grid_fed_name parameter

#### 3. README.md
- Added grid_pypsa to project structure
- Added PyPSA usage documentation
- Updated configuration examples

## HELICS Interface

### Subscriptions (Inputs)
- `node_{idx}/P` - Active power for load at node idx (MW)

### Publications (Outputs)
- `Grid/transformer_{idx}_power` - Active power from generator at idx (MW)

This interface is identical to the Pandapower grid federate.

## Technical Details

### Power Flow Execution
PyPSA's `network.pf()` method is used for AC power flow calculation.

### Power Extraction
For slack generators, power is extracted from `network.buses_t.p[bus]` which contains the bus power injection. This is necessary because PyPSA stores slack generator power at the bus level rather than in `generators_t.p`.

### Validation Results

#### Network Loading Test
- Grid file: kerber_landnetz_freileitung_1.xlsx
- Successfully loaded: 15 buses, 13 loads, 1 generator, 13 lines, 1 transformer

#### Power Flow Test
- Total load: 0.104 MW
- Generator power: 0.108216 MW
- Difference: ~4% due to line losses (correct behavior)

## Testing

### Unit Tests
- test_create_federate(): Validates federate initialization
- test_update_internal_model(): Validates power flow execution
- test_load_pypsa_from_pandapower_excel(): Validates conversion function
- test_main_runs(): Validates federate lifecycle
- test_parse_args(): Validates command-line argument parsing

### Security
- CodeQL scan: 0 alerts
- No vulnerabilities detected

### Code Quality
- Formatted with black
- Linted with ruff
- All checks passed

## Usage

### Switch from Pandapower to PyPSA

In `config/experiment.yml`, replace:
```yaml
grid:
  name: grid
  build_folder: grid
  grid_file: kerber_landnetz_freileitung_1.xlsx
```

With:
```yaml
grid_pypsa:
  name: grid
  build_folder: grid_pypsa
  grid_file: kerber_landnetz_freileitung_1.xlsx
```

### Run Experiment
```bash
./run.sh
```

The composegen will automatically:
1. Detect grid_pypsa instead of grid
2. Generate appropriate docker-compose.yml
3. Generate grid_config.json for the PyPSA federate
4. Build and launch the PyPSA grid container

## Benefits of PyPSA

1. **Optimization capabilities**: PyPSA has built-in optimization for unit commitment and economic dispatch
2. **Multi-period support**: Native support for time series optimization
3. **Extensibility**: Easier to add generation/storage optimization
4. **Performance**: Can be faster for larger networks
5. **Active development**: PyPSA is actively maintained with regular updates

## Future Enhancements

Possible future additions leveraging PyPSA's capabilities:
- Multi-period optimization
- Unit commitment
- Economic dispatch
- Storage optimization
- Renewable energy integration scenarios
- Network expansion planning

## Compatibility

### Input Files
- Uses same Pandapower Excel format
- No changes needed to existing grid files

### Output Format
- Same HELICS publications
- Compatible with existing recorder and visualization tools

### Configuration
- Minimal changes to experiment.yml
- Same general configuration structure

## Files Changed

### New Files (7)
1. grid_pypsa/main.py (222 lines)
2. grid_pypsa/Dockerfile (40 lines)
3. grid_pypsa/requirements.txt (7 lines)
4. grid_pypsa/test_main.py (200 lines)
5. grid_pypsa/README.md (115 lines)
6. config/experiment_pypsa.yml (23 lines)
7. SUMMARY.md (this file)

### Modified Files (3)
1. composegen/main.py (21 lines changed)
2. composegen/test_main.py (5 lines changed)
3. README.md (41 lines changed)

**Total**: 657 lines added, 17 lines modified

## Conclusion

The PyPSA grid federate has been successfully implemented as a drop-in replacement for the Pandapower grid federate. It provides the same interface and functionality while offering additional capabilities for future enhancements. The implementation follows the project's coding standards and has been validated with actual grid data.
