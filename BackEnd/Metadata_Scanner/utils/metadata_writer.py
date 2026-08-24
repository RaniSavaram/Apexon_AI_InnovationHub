import json
from pathlib import Path


def write_metadata(metadata, output_dir: str = None):
    """
    Write extracted metadata to a JSON file.

    Ported from app/BackEnd/Metadata_Scanner/utils/metadata_writer.py.
    Updated to accept a configurable output_dir (defaults to Django DATA_DIR).
    """

    if output_dir is None:
        try:
            from django.conf import settings
            output_path = Path(settings.OUTPUT_DIR)
        except Exception:
            output_path = Path("output")
    else:
        output_path = Path(output_dir)

    output_path.mkdir(parents=True, exist_ok=True)

    metadata_file = output_path / "metadata.json"

    with open(
        metadata_file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(metadata, f, indent=4)

    return str(metadata_file)
