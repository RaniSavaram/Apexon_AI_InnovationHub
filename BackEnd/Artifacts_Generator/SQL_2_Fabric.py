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
import re
from pathlib import Path

import docx
import pyarrow as pa
from azure.identity import DefaultAzureCredential
# pyrefly: ignore [missing-import]
# type: ignore
from deltalake import write_deltalake, DeltaTable, Schema as DeltaSchema

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_DOC_PATH = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output" / "sqlserver_Assessment_Report.docx"
DOC_PATH = Path(os.environ.get("ASSESSMENT_REPORT_PATH", DEFAULT_DOC_PATH))

DEFAULT_WORKSPACE_ID = "bae3b540-d044-45e0-8c52-3cf4ee3dcb31"   # Fabric Insights
DEFAULT_LAKEHOUSE_ID = "87ddccfe-cfa3-47d6-92ab-b638ce379319"    # SQL_Lakehouse

WORKSPACE_ID = os.environ.get("FABRIC_WORKSPACE_ID", DEFAULT_WORKSPACE_ID)
LAKEHOUSE_ID = os.environ.get("FABRIC_LAKEHOUSE_ID", DEFAULT_LAKEHOUSE_ID)

BULLET = "•"  # the actual column-list bullet character used by the report template


def get_onelake_token():
    credential = DefaultAzureCredential()
    return credential.get_token("https://storage.azure.com/.default").token


def parse_report(doc_path):
    doc = docx.Document(str(doc_path))
    
    tables = []
    current = None
    in_columns_old = False
    
    for element in doc.element.body:
        if element.tag.endswith('p'):
            p = docx.text.paragraph.Paragraph(element, doc)
            text = p.text.strip()
            if not text:
                continue
                
            # --- OLD FORMAT CHECK ---
            if text.startswith("Table Name:"):
                if current:
                    tables.append(current)
                t_name = text.split(":", 1)[1].strip()
                current = {"schema": "dbo", "table": t_name, "columns": []}
                in_columns_old = False
                continue
                
            if current and text.startswith("Schema:"):
                current["schema"] = text.split(":", 1)[1].strip()
                continue
                
            if current and text.startswith("- Columns:"):
                in_columns_old = True
                continue
                
            if current and in_columns_old:
                if text.startswith("-") and not text.startswith(BULLET):
                    # ends the columns block
                    in_columns_old = False
                elif text.endswith(":") and not text.startswith(BULLET):
                    in_columns_old = False
                elif text.startswith(BULLET) or text.startswith("-") or text.startswith("*"):
                    col_part = text.lstrip("•-*").strip()
                    if "(" in col_part and ")" in col_part:
                        name_part, type_part = col_part.split("(", 1)
                        col_name = name_part.strip()
                        data_type_raw = type_part.rsplit(")", 1)[0].strip()
                    else:
                        col_name = col_part.strip()
                        data_type_raw = "string"
                    current["columns"].append((col_name, data_type_raw))
                continue

            # --- NEW FORMAT CHECK ---
            is_heading2 = False
            if p.style and p.style.name == 'Heading 2':
                is_heading2 = True
            elif p.paragraph_format and p.style and p.style.name.startswith('Heading 2'):
                is_heading2 = True
                
            if (is_heading2 or text.startswith("5.")) and re.match(r"^5\.\d+\s+", text):
                if current:
                    tables.append(current)
                parts = text.split(" ", 1)
                t_name = parts[1].strip()
                current = {"schema": "dbo", "table": t_name, "columns": []}
                in_columns_old = False
                continue
                
            if current:
                match = re.search(r"The table ([a-zA-Z0-9_]+)\." + re.escape(current["table"]) + r" is mapped", text)
                if match:
                    current["schema"] = match.group(1)
                    
        elif element.tag.endswith('tbl') and current:
            t = docx.table.Table(element, doc)
            if len(t.rows) > 0 and len(t.columns) >= 2:
                header_text = t.rows[0].cells[0].text.strip().lower()
                if "column" in header_text:
                    for row in t.rows[1:]:
                        if len(row.cells) >= 2:
                            c_name = row.cells[0].text.strip()
                            c_type = row.cells[1].text.strip()
                            if c_name and c_type:
                                current["columns"].append((c_name, c_type))
                                
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


def Generator(doc_path=None, workspace_id=None, lakehouse_id=None):
    ws_id = workspace_id or os.environ.get("FABRIC_WORKSPACE_ID") or WORKSPACE_ID
    lh_id = lakehouse_id or os.environ.get("FABRIC_LAKEHOUSE_ID") or LAKEHOUSE_ID

    if doc_path is None:
        if DOC_PATH.exists():
            target_doc = DOC_PATH
        else:
            output_dir = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output"
            reports = list(output_dir.glob("*Assessment_Report.docx")) if output_dir.exists() else []
            if not reports and output_dir.exists():
                reports = list(output_dir.glob("*.docx"))
            if reports:
                reports.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                target_doc = reports[0]
            else:
                target_doc = DOC_PATH
    else:
        target_doc = Path(doc_path)

    if not target_doc.exists():
        msg = f"Assessment Report not found at {target_doc}. Please run a scan first."
        print(f"ERROR: {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    print(f"[INFO] Using assessment report: {target_doc}")
    logs_list = [
        f"[INFO] Target Workspace: {ws_id}",
        f"[INFO] Target Lakehouse: {lh_id}",
        f"[INFO] Using assessment report: {target_doc.name}"
    ]

    try:
        token = get_onelake_token()
        logs_list.append("[INFO] OneLake token acquired via DefaultAzureCredential.")
    except Exception as e:
        msg = f"Azure OneLake Authentication failed: {e}. Please ensure Azure credentials (e.g. AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID) are configured."
        print(f"ERROR: {msg}", file=sys.stderr)
        logs_list.append(f"[ERROR] {msg}")
        return {"status": "error", "message": msg, "logs": logs_list}

    storage_options = {
        "bearer_token": token,
        "use_fabric_endpoint": "true",
        "allow_unsafe_rename": "true",
    }

    try:
        parsed = parse_report(target_doc)
    except Exception as e:
        msg = f"Failed to parse report {target_doc.name}: {e}"
        print(f"ERROR: {msg}", file=sys.stderr)
        logs_list.append(f"[ERROR] {msg}")
        return {"status": "error", "message": msg, "logs": logs_list}

    print(f"Parsed {len(parsed)} table(s) from report:\n")
    logs_list.append(f"[INFO] Parsed {len(parsed)} table(s) from {target_doc.name}.")

    if not parsed:
        msg = f"No tables found in assessment report {target_doc.name}."
        print(f"WARN: {msg}")
        logs_list.append(f"[WARN] {msg}")
        return {"status": "warning", "message": msg, "tables": [], "logs": logs_list}

    synced_tables = []
    tables_info = []
    errors = []

    for t in parsed:
        schema_name = clean_identifier(t["schema"] or "dbo")
        table_name = clean_identifier(t["table"])
        cols = t["columns"]

        table_meta = {
            "schema": schema_name,
            "table": table_name,
            "columns_count": len(cols),
            "columns": [{"name": c[0], "type": c[1]} for c in cols]
        }
        tables_info.append(table_meta)

        log_hdr = f"=== {schema_name}.{table_name} ({len(cols)} columns) ==="
        print(log_hdr)
        logs_list.append(log_hdr)

        fields = []
        for col_name, dt_raw in cols:
            arrow_type = map_arrow_type(dt_raw)
            fields.append(pa.field(clean_identifier(col_name), arrow_type, nullable=True))
            col_log = f"  {col_name:30s} {dt_raw:20s} -> {arrow_type}"
            print(col_log)
            logs_list.append(col_log)

        schema = pa.schema(fields)

        table_uri = (
            f"abfss://{ws_id}@onelake.dfs.fabric.microsoft.com/"
            f"{lh_id}/Tables/{schema_name}/{table_name}"
        )

        try:
            sync_table(table_uri, schema_name, table_name, schema, storage_options)
            synced_tables.append(f"{schema_name}.{table_name} ({len(cols)} columns)")
            logs_list.append(f"  [SUCCESS] Delta Table synced: {schema_name}.{table_name}")
        except Exception as exc:
            raw_err = str(exc).strip().replace("\u21b3", "->")
            # Extract first line or clean summary
            first_line = raw_err.split("\n")[0] if raw_err else "Sync error"
            err_msg = f"{schema_name}.{table_name}: {first_line}"
            try:
                print(f"  [ERROR] {err_msg}")
            except Exception:
                pass
            logs_list.append(f"  [ERROR] {err_msg}")
            errors.append(err_msg)
        print()

    print("Done.")
    logs_list.append(f"[INFO] Execution completed. Synced: {len(synced_tables)}, Errors: {len(errors)}")

    if errors:
        return {
            "status": "partial" if synced_tables else "error",
            "message": f"Synced {len(synced_tables)} table(s) to Fabric Lakehouse, {len(errors)} error(s).",
            "tables": synced_tables,
            "tables_info": tables_info,
            "errors": errors,
            "logs": logs_list,
            "target": {
                "workspace_id": ws_id,
                "lakehouse_id": lh_id,
                "report_name": target_doc.name
            }
        }

    return {
        "status": "success",
        "message": f"Successfully created {len(synced_tables)} Delta table(s) directly in Microsoft Fabric OneLake Lakehouse.",
        "tables": synced_tables,
        "tables_info": tables_info,
        "logs": logs_list,
        "target": {
            "workspace_id": ws_id,
            "lakehouse_id": lh_id,
            "report_name": target_doc.name
        }
    }


if __name__ == "__main__":
    Generator()