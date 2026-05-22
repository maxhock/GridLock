import os
from pathlib import Path

from extract import extract
from transform import transform
from load import load


def main() -> None:
    config_path = Path(os.environ.get("CONFIG_PATH", "/config/experiment.yml"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/config/tmp"))
    data_input_dir = Path(os.environ.get("DATA_INPUT_DIR", "/data/input"))

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