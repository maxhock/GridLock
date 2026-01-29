# Composegen Module

Configuration generator for HELICS federation simulations following Google Python Style Guide.

## Module Structure

The composegen package is organized into focused, single-responsibility modules:

### Core Modules

- **`main.py`** - Entry point orchestrating the ETL (Extract, Transform, Load) pipeline
- **`config.py`** - Constants and configuration mappings (paths, federate types)
- **`tree_operations.py`** - Tree structure building and manipulation
- **`validation.py`** - Configuration validation logic
- **`grid_operations.py`** - Grid-specific operations (layout extraction)
- **`pubsub.py`** - HELICS publication/subscription setup
- **`config_processing.py`** - Configuration file loading and processing
- **`cst_generator.py`** - CoSim Toolbox (CST) output generation

## Usage

```bash
# Use default config (experiment.yml)
python main.py

# Use specific config file
python main.py /path/to/config.yml
```

## Architecture

The configuration generation follows a clear ETL pattern:

1. **Extract**: Load YAML configuration and build tree structure
2. **Transform**: Validate, expand grid nodes, setup pub/sub
3. **Load**: Generate CST configuration files

## Module Responsibilities

### config.py
- Default configuration paths
- Federate type to Docker image mappings
- Constant values used across modules

### tree_operations.py
- `add_to_tree()`: Build hierarchical tree from config dict
- `expand_grid_nodes()`: Place federates on grid buses based on placement rules

### validation.py
- `validate_node()`: Validate individual node configuration
- `validate_tree()`: Validate entire tree structure

### grid_operations.py
- `extract_grid_nodes()`: Extract grid nodes and bus information from layout files

### pubsub.py
- `add_pub_sub()`: Add publication/subscription to a node
- `setup_publications_subscriptions()`: Configure all pub/sub topics based on hierarchy

### config_processing.py
- `load_config_file()`: Load YAML configuration with fallback logic
- `process_general_config()`: Validate and process general configuration settings

### cst_generator.py
- `map_params_to_type()`: Map federate types to Docker parameters
- `generate_cst_config()`: Generate CST-compatible output files

## Design Principles

Following Google Python Style Guide:
- Clear separation of concerns
- Single responsibility per module
- Comprehensive docstrings with Args/Returns/Raises
- Type hints on all function signatures
- Module-level constants in config.py
- Import organization: standard library → third-party → local

## Testing

Run the module:
```bash
cd composegen
python main.py
```

Expected output structure:
```
meta_store/
├── federations/
│   └── *Federation.json
└── scenarios/
    └── *Scenario.json
*Scenario.yaml
```
