"""
Parses ../output/Assesment Report.docx (the AI-generated table summary report)
and creates the corresponding schemas/tables as empty Delta tables directly in
a Fabric Lakehouse via OneLake, using deltalake (delta-rs) — no Spark/Fabric
notebook required.

Fixes a real bug present in the original pasted Fabric-notebook snippet: that
version treated ANY line starting with "-" as a column bullet, which caused
"- Foreign Keys: None" to be parsed as a bogus column named
"Foreign Keys: None" for every table. Here, only lines that start with the
actual bullet character (U+2022) inside the "- Columns:" block are treated
as columns.

Re-running this script is safe: existing tables are only ever extended with
new columns found in the report. Columns removed from the report, or whose
type changed, are reported as warnings and left untouched.
"""
import os
import sys
from pathlib import Path

import docx
import pyarrow as pa
from azure.identity import DefaultAzureCredential
from deltalake import write_deltalake, DeltaTable
from deltalake.schema import Schema as DeltaSchema

DEFAULT_DOC_PATH = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output" / "sqlserver_Assessment_Report.docx"
DOC_PATH = Path(os.environ.get("ASSESSMENT_REPORT_PATH", DEFAULT_DOC_PATH))

WORKSPACE_ID = "bae3b540-d044-45e0-8c52-3cf4ee3dcb31"   # Fabric Insights
LAKEHOUSE_ID = "a65899d4-3f39-4af6-b696-ae1903f4500a"    # sai_fabric_artifcats

BULLET = "•"  # the actual column-list bullet character used by the report template


def get_onelake_token():
    credential = DefaultAzureCredential()
    return credential.get_token("https://storage.azure.com/.default").token


def parse_report(doc_path):
    doc = docx.Document(str(doc_path))
    lines = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]

    tables = []  # list of dicts: {schema, table, columns: [(name, type_raw)]}
    current = None
    in_columns = False

    for line in lines:
        if line.startswith("Table Name:"):
            if current:
                tables.append(current)
            current = {"schema": None, "table": line.split(":", 1)[1].strip(), "columns": []}
            in_columns = False
            continue

        if current is None:
            continue

        if line.startswith("Schema:"):
            current["schema"] = line.split(":", 1)[1].strip()
            continue

        if line.startswith("- Columns:"):
            in_columns = True
            continue

        # Any other "- Label:" line inside Structure/General Info/etc. ends the columns block
        if in_columns and line.startswith("-"):
            in_columns = False
            continue

        # Section headers like "Dependencies:", "Usage:", "Summary:" also end it
        if in_columns and line.endswith(":") and not line.startswith(BULLET):
            in_columns = False
            continue

        if in_columns and line.startswith(BULLET):
            col_part = line.lstrip(BULLET).strip()
            if "(" in col_part and ")" in col_part:
                name_part, type_part = col_part.split("(", 1)
                col_name = name_part.strip()
                data_type_raw = type_part.rsplit(")", 1)[0].strip()
            else:
                col_name = col_part.strip()
                data_type_raw = "string"
            current["columns"].append((col_name, data_type_raw))

    if current:
        tables.append(current)

    return tables


def map_arrow_type(dt_raw):
    dt = (dt_raw or "").strip().lower()
    if dt in ("bigint",):
        return pa.int64()
    if dt in ("tinyint",):
        return pa.int8()
    if dt in ("smallint",):
        return pa.int16()
    if dt in ("int", "integer"):
        return pa.int32()
    if "decimal" in dt or "numeric" in dt or "money" in dt:
        if "(" in dt and ")" in dt:
            inner = dt.split("(", 1)[1].split(")", 1)[0]
            try:
                p_str, s_str = [x.strip() for x in inner.split(",")]
                return pa.decimal128(int(p_str), int(s_str))
            except Exception:
                pass
        return pa.decimal128(38, 10)
    if "float" in dt or "real" in dt:
        return pa.float64()
    if "bit" in dt or "bool" in dt:
        return pa.bool_()
    if dt == "date":
        return pa.date32()
    if "datetime" in dt or "smalldatetime" in dt or "timestamp" in dt:
        return pa.timestamp("us")
    # varchar/nvarchar/char/nchar/text/ntext/varchar(MAX)/string/etc.
    return pa.string()


def clean_identifier(name):
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in name.strip())


def sync_table(table_uri, schema_name, table_name, target_pa_schema, storage_options):
    """
    Creates the table if it doesn't exist. If it does, adds any columns present
    in the report but missing from the table (safe schema evolution). Columns
    that exist in the table but were dropped from the report, or whose type
    changed, are only reported — never auto-altered, since that could destroy
    existing data.
    """
    target_fields = {f.name: f for f in DeltaSchema.from_arrow(target_pa_schema).fields}

    try:
        dt = DeltaTable(table_uri, storage_options=storage_options)
    except Exception:
        empty_table = pa.Table.from_pylist([], schema=target_pa_schema)
        write_deltalake(table_uri, empty_table, storage_options=storage_options, mode="error")
        print(f"  [CREATE] Created {schema_name}.{table_name}")
        return

    existing_fields = {f.name: f for f in dt.schema().fields}

    new_names = [n for n in target_fields if n not in existing_fields]
    removed_names = [n for n in existing_fields if n not in target_fields]
    changed_names = [
        n for n in target_fields
        if n in existing_fields and str(existing_fields[n].type) != str(target_fields[n].type)
    ]

    if new_names:
        dt.alter.add_columns([target_fields[n] for n in new_names])
        print(f"  [UPDATE] {schema_name}.{table_name}: added columns {new_names}")

    if removed_names:
        print(f"  [WARN] {schema_name}.{table_name}: columns no longer in report, NOT dropped: {removed_names}")

    if changed_names:
        print(f"  [WARN] {schema_name}.{table_name}: type changed in report, NOT altered: {changed_names}")

    if not new_names and not removed_names and not changed_names:
        print(f"  [OK] {schema_name}.{table_name} already in sync")


def Generator():
    if not DOC_PATH.exists():
        print(f"ERROR: report not found at {DOC_PATH}", file=sys.stderr)
        sys.exit(1)

    token = get_onelake_token()
    storage_options = {
        "bearer_token": token,
        "use_fabric_endpoint": "true",
        "allow_unsafe_rename": "true",
    }

    parsed = parse_report(DOC_PATH)
    print(f"Parsed {len(parsed)} table(s) from report:\n")

    for t in parsed:
        schema_name = clean_identifier(t["schema"] or "dbo")
        table_name = clean_identifier(t["table"])
        cols = t["columns"]

        print(f"=== {schema_name}.{table_name} ({len(cols)} columns) ===")
        fields = []
        for col_name, dt_raw in cols:
            arrow_type = map_arrow_type(dt_raw)
            fields.append(pa.field(clean_identifier(col_name), arrow_type, nullable=True))
            print(f"  {col_name:30s} {dt_raw:20s} -> {arrow_type}")

        schema = pa.schema(fields)

        table_uri = (
            f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/"
            f"{LAKEHOUSE_ID}/Tables/{schema_name}/{table_name}"
        )

        sync_table(table_uri, schema_name, table_name, schema, storage_options)
        print()

    print("Done.")