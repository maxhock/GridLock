"""Entry point of the composegen container: run the extract → transform → load ETL."""

import argparse
import os
from pathlib import Path

from extract import extract
from transform import transform
from load import load

def main() -> None:
    """Turn the experiment YAML into a CST federation and a docker-compose file."""
    parser = argparse.ArgumentParser(description="Generate federation configuration.")
    parser.add_argument(
        "--config", "-c", type=str,
        help="Path to the experiment configuration YAML file",
    )
    parser.add_argument(
        "--output", "-o", type=str,
        help="Directory for output files",
    )
    parser.add_argument(
        "--data-input", "-d", type=str,
        help="Path to the data input directory",
    )
    args = parser.parse_args()

    config_path = Path(args.config if args.config else os.environ.get("CONFIG_PATH", "/config/experiment-LV.yml"))
    output_dir = Path(args.output if args.output else os.environ.get("OUTPUT_DIR", "/config/tmp"))
    data_input_dir = Path(args.data_input if args.data_input else os.environ.get("DATA_INPUT_DIR", "/data/input"))

    output_dir.mkdir(parents=True, exist_ok=True)

    extracted = extract(
        config_path=config_path,
        data_input_path=data_input_dir,
        output_path=output_dir,
    )

    transformed = transform(extracted)
    load(transformed)

    print("composegen ETL finished successfully.")

if __name__ == "__main__":
    main()