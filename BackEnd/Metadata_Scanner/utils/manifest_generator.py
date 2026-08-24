import json
from datetime import datetime
from pathlib import Path


def generate_manifest(metadata, output_dir: str = None):
    """
    Generate a manifest JSON file from extracted metadata.

    Ported from app/BackEnd/Metadata_Scanner/utils/manifest_generator.py.
    Updated to accept a configurable output_dir (defaults to Django DATA_DIR).
    """

    # Use Django settings output dir if none provided
    if output_dir is None:
        try:
            from django.conf import settings
            output_path = Path(settings.OUTPUT_DIR)
        except Exception:
            output_path = Path("output")
    else:
        output_path = Path(output_dir)

    output_path.mkdir(parents=True, exist_ok=True)

    tables = {}

    for item in metadata:

        table = item["table"]

        if table not in tables:

            tables[table] = []

        tables[table].append({

            "column": item["column"],
            "datatype": item["datatype"],
            "nullable": item["nullable"]

        })

    manifest = {

        "created": datetime.now().isoformat(),

        "database_type": "SQLServer",

        "tables": []

    }

    for table, cols in tables.items():

        manifest["tables"].append({

            "table_name": table,
            "column_count": len(cols),
            "columns": cols

        })

    manifest_file = output_path / "manifest.json"

    with open(manifest_file, "w") as f:

        json.dump(manifest, f, indent=4)

    return str(manifest_file)
