"""Main entry point for the composegen configuration generator.

This module orchestrates the ETL (Extract, Transform, Load) process for
generating HELICS federation configurations from experiment YAML files,
following Google Python Style Guide.
"""

import argparse
import sys
from pathlib import Path

from treelib import Tree

# Handle both direct execution and package import
if __name__ == "__main__":
    # Allow direct execution by adding parent to path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from composegen.config_processing import load_config_file, process_general_config
    from composegen.cst_generator import generate_cst_config
    from composegen.grid_operations import extract_grid_nodes
    from composegen.pubsub import setup_publications_subscriptions
    from composegen.tree_operations import add_to_tree, expand_grid_nodes
    from composegen.validation import validate_tree
else:
    from .config_processing import load_config_file, process_general_config
    from .cst_generator import generate_cst_config
    from .grid_operations import extract_grid_nodes
    from .pubsub import setup_publications_subscriptions
    from .tree_operations import add_to_tree, expand_grid_nodes
    from .validation import validate_tree


def main() -> None:
    """Main entry point for the composegen configuration generator.

    This function orchestrates the entire configuration generation process:
    1. Extract: Load configuration and build tree structure
    2. Transform: Validate and expand the configuration
    3. Load: Generate CST output files
    """
    parser = argparse.ArgumentParser(description="Generate federation configuration.")
    parser.add_argument(
        "config_file", nargs="?", help="Path to the experiment configuration YAML file"
    )
    args = parser.parse_args()

    # --- ETL: EXTRACT ---
    print("--- Extracting Configuration ---")
    tree = Tree()

    config_path = Path(args.config_file) if args.config_file else None
    cfg = load_config_file(config_path)

    add_to_tree(tree, cfg["federation"])

    # Extract grid nodes
    grid_nodes = extract_grid_nodes(tree)

    # --- ETL: TRANSFORM ---
    print("--- Transforming Configuration ---")

    # Validate configuration
    validate_tree(tree)

    # Expand grid nodes based on placement rules
    expand_grid_nodes(tree, grid_nodes)

    # Process general configuration
    general_cfg = cfg.get("general", {})
    process_general_config(general_cfg)

    # Add publications and subscriptions
    setup_publications_subscriptions(tree)

    # --- ETL: LOAD / GENERATE ---
    generate_cst_config(tree, general_cfg)


if __name__ == "__main__":
    main()
