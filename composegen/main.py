import argparse
from pathlib import Path

from extract import extract
from transform import transform
from load import load


def main():
    parser = argparse.ArgumentParser(description="Generate federation configuration.")
    parser.add_argument(
        "config_file",
        nargs="?",
        help="Path to the experiment configuration YAML file",
    )
    args = parser.parse_args()

    # Resolve config path
    if args.config_file:
        config_path = Path(args.config_file)
    else:
        file_name = "experiment.yaml"
        config_path = Path("/config/" + file_name)
        # Fallback if experiment.yml logic from notebook was specific
        if not config_path.exists():
            config_path = Path(
                "../config/" + file_name
            )  # revert to default path for generic script
            if not config_path.exists():
                config_path = Path(
                    "./config/" + file_name
                )  # revert to default path for generic script

    # --- ETL: EXTRACT ---
    print("--- Extracting Configuration ---")
    cfg, tree, grid_nodes = extract(config_path)

    # --- ETL: TRANSFORM ---
    print("--- Transforming Configuration ---")
    tree, general_cfg = transform(cfg, tree, grid_nodes)

    # --- ETL: LOAD / GENERATE ---
    print("--- Generating CST Configuration ---")
    load(tree, general_cfg)


if __name__ == "__main__":
    main()
