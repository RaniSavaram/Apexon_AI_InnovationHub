"""
Reads migration_plan.json (produced by plan_to_json.py, which is itself
built from AI_Migration_Plan.docx + Assesment Report.docx), resolves (or
creates) a dedicated Fabric Lakehouse named "<source_system>_<database_name>"
- e.g. "databricks_sales_prod" - and creates the corresponding
schemas/tables as empty Delta tables inside it via OneLake, using
deltalake (delta-rs). No Spark/Fabric notebook required.

If a Lakehouse with that name already exists in the target workspace, it
is reused rather than duplicated. Only the Lakehouse and its Tables are
managed here; Warehouses, Views, and Pipelines are not yet created by this
script.

Despite the old "SQL_2_Fabric" name this used to go by, nothing here is
SQL-Server-specific. sqlserver.py, databricks_client.py, and
dynamics365.py (see Metadata_Scanner/extractors/) all normalize their scan
results into the identical {name, datatype, max_length, precision, scale,
nullable} column shape before the AI agents ever see them, so the same
migration_plan.json - and this same script - works unchanged whether the
source was SQL Server, Databricks/Unity Catalog, or Dynamics 365/
Dataverse. map_arrow_type() below understands all three type vocabularies
(e.g. SQL Server "varchar"/"datetime", Databricks "STRING"/"TIMESTAMP",
Dataverse "String"/"DateTime"/"Uniqueidentifier"/"Picklist").

This used to parse Assesment Report.docx directly. It's now JSON-driven so
that:
  - the docx-parsing logic lives in exactly one place (plan_to_json.py),
    shared by every target-specific generator instead of re-implemented here
  - this script no longer cares which source DB the metadata came from —
    it only understands the normalized JSON shape
  - each table's Medallion layer (Bronze/Silver/Gold), decided by the AI
    Migration Plan agent, is used to route the table into the matching
    Fabric Lakehouse zone instead of every table landing in one flat spot

Re-running this script is safe: existing tables are only ever extended with
new columns found in the JSON. Columns removed from the JSON, or whose type
changed, are reported as warnings and left untouched. Re-running against
the same source_system/database_name reuses the same Lakehouse instead of
creating a duplicate.

Usage
-----
    python DB_2_Fabric.py [--json path/to/migration_plan.json] [--dry-run] \\
        [--source-system databricks] [--database-name sales_prod]

--dry-run skips Azure auth, the Lakehouse get-or-create call, and OneLake
writes entirely; it just prints what would be created/updated and where.
Useful for validating the JSON and layer routing before touching Fabric.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import pyarrow as pa

# Rust-side errors from deltalake (e.g. OneLake auth failures) can contain
# Unicode box-drawing/arrow characters that crash a plain print() on
# Windows' default cp1252 console encoding, masking the real error behind
# a second, unrelated UnicodeEncodeError traceback.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

try:
    from Artifacts_Generator import fabric_api
except ImportError:
    # Fallback for running this script directly (e.g. `python DB_2_Fabric.py`
    # from inside Artifacts_Generator/) where BackEnd isn't on sys.path as
    # a package root the way Django's app loading puts it.
    import fabric_api

JSON_PATH = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output" / "migration_plan.json"

WORKSPACE_ID = "bae3b540-d044-45e0-8c52-3cf4ee3dcb31"   # Fabric Insights (target workspace)

# Pre-provisioned Lakehouses, one per source system - these take priority
# over dynamically creating a "<source>_<database>" Lakehouse. Keyed by
# lowercased source_system name. Any source system NOT listed here falls
# back to the dynamic get-or-create-by-name behavior in
# resolve_artifact_lakehouse() below.
SOURCE_LAKEHOUSE_MAP = {
    # Verified accessible: workspace "Fabric Insights" (bae3b540-...), lakehouse "Databricks_Lakehouse".
    "databricks": ("bae3b540-d044-45e0-8c52-3cf4ee3dcb31", "bc94c085-a651-46a6-96a1-0c1183ef78f9"),
    # Verified accessible: workspace "Fabric Insights" (bae3b540-...), lakehouse "SQL_Lakehouse".
    "sqlserver": ("bae3b540-d044-45e0-8c52-3cf4ee3dcb31", "87ddccfe-cfa3-47d6-92ab-b638ce379319"),
    "sql server": ("bae3b540-d044-45e0-8c52-3cf4ee3dcb31", "87ddccfe-cfa3-47d6-92ab-b638ce379319"),
    "dynamics365": ("9cae3cbc-5ca6-49ce-9587-302752b104eb", "be349165-d57c-4756-96b9-738d1c69ed65"),
    "dynamics 365": ("9cae3cbc-5ca6-49ce-9587-302752b104eb", "be349165-d57c-4756-96b9-738d1c69ed65"),
    "d365": ("9cae3cbc-5ca6-49ce-9587-302752b104eb", "be349165-d57c-4756-96b9-738d1c69ed65"),
}

# Only used when a Medallion layer should live in its own, separately
# managed Lakehouse (overrides SOURCE_LAKEHOUSE_MAP/dynamic resolution for
# just that layer). {"Bronze": ("<workspace_id>", "<lakehouse_id>"), ...}
LAYER_LAKEHOUSE_MAP = {}


def get_or_create_lakehouse(workspace_id, display_name, token):
    """Returns the id of the Lakehouse named `display_name` in
    `workspace_id`, creating it first if it doesn't exist yet."""
    return fabric_api.get_or_create_item(workspace_id, "lakehouses", display_name, token)


def build_artifact_lakehouse_name(source_system, database_name):
    """
    e.g. source_system='databricks', database_name='sales_prod'
      -> 'databricks_sales_prod'
    Falls back to generic placeholders if either piece is missing so the
    pipeline never breaks on a name, it just produces something less
    descriptive (e.g. 'source_db').
    """
    src = clean_identifier((source_system or "source").strip().lower())
    db = clean_identifier((database_name or "db").strip().lower())
    return f"{src}_{db}"


def resolve_artifact_lakehouse(source_system, database_name, default_workspace_id, fabric_token, dry_run):
    """
    Returns (workspace_id, lakehouse_id, lakehouse_display_name).

    Priority:
      1. SOURCE_LAKEHOUSE_MAP - a pre-provisioned Lakehouse dedicated to
         this source system. Used as-is, no Fabric API call needed since
         the id is already known.
      2. Dynamic get-or-create of a "<source>_<database>" Lakehouse in
         default_workspace_id, for any source system not in the map.
    """
    src_key = (source_system or "").strip().lower()

    if src_key in SOURCE_LAKEHOUSE_MAP:
        ws, lh = SOURCE_LAKEHOUSE_MAP[src_key]
        print(f"[INFO] '{source_system}' has a pre-provisioned Lakehouse - using it directly (id={lh}).")
        return ws, lh, f"{src_key} (pre-provisioned)"

    display_name = build_artifact_lakehouse_name(source_system, database_name)
    if dry_run:
        print(f"  [DRY-RUN] would get-or-create Lakehouse '{display_name}' in workspace {default_workspace_id}")
        return default_workspace_id, "<lakehouse-id-resolved-at-runtime>", display_name

    lakehouse_id = get_or_create_lakehouse(default_workspace_id, display_name, fabric_token)
    return default_workspace_id, lakehouse_id, display_name


def map_arrow_type(dt_raw):
    """
    Maps a source-system data type string to a pyarrow type. Deliberately
    dialect-agnostic: the same function handles SQL Server (varchar,
    datetime, bigint), Databricks/Unity Catalog (STRING, TIMESTAMP,
    DECIMAL(p,s)), and Dynamics 365 / Dataverse (String, DateTime,
    Uniqueidentifier, Picklist, Money) type vocabulary, since all three
    extractors (sqlserver.py, databricks_client.py, dynamics365.py) already
    normalize into the same {name, datatype, max_length, precision, scale}
    column shape upstream - only the *words* used for a type differ.
    """
    dt = (dt_raw or "").strip().lower()

    if dt in ("bigint",):
        return pa.int64()
    if dt in ("tinyint", "byte"):
        return pa.int8()
    if dt in ("smallint", "short"):
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
    if "float" in dt or "real" in dt or "double" in dt:
        return pa.float64()
    if "bit" in dt or "bool" in dt:
        return pa.bool_()
    if dt == "date":
        return pa.date32()
    if "datetime" in dt or "smalldatetime" in dt or "timestamp" in dt:
        return pa.timestamp("us")
    if "uniqueidentifier" in dt or dt == "guid" or "lookup" in dt:
        return pa.string()  # GUID/lookup references - stored as string, no native Arrow UUID type
    if "picklist" in dt or "optionset" in dt or "state" in dt or "status" in dt:
        return pa.string()  # Dataverse choice/enum fields - stored as their label/string form
    if dt.startswith("array") or dt.startswith("map") or dt.startswith("struct") or dt == "variant":
        print(f"  [WARN] complex/nested type '{dt_raw}' has no flat Arrow mapping - "
              f"storing as string; consider flattening upstream if you need real structure.")
        return pa.string()
    # varchar/nvarchar/char/nchar/text/ntext/varchar(MAX)/string/memo/etc.
    return pa.string()


def clean_identifier(name):
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in (name or "").strip())


def load_plan(json_path):
    if not json_path.exists():
        print(f"ERROR: JSON plan not found at {json_path}", file=sys.stderr)
        print("Run plan_to_json.py first to generate it from the docx reports.", file=sys.stderr)
        return {"meta": {}, "tables": []}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                print(f"ERROR: JSON plan at {json_path} is empty.", file=sys.stderr)
                return {"meta": {}, "tables": []}
            return json.loads(content)
    except Exception as e:
        print(f"ERROR: Failed to parse JSON plan at {json_path}: {e}", file=sys.stderr)
        return {"meta": {}, "tables": []}


def resolve_lakehouse_target(layer, default_workspace_id, default_lakehouse_id):
    """
    Returns (workspace_id, lakehouse_id). LAYER_LAKEHOUSE_MAP can route a
    given Medallion layer to its own dedicated Lakehouse; every other layer
    uses the default (source-system) Lakehouse.
    """
    if layer and layer in LAYER_LAKEHOUSE_MAP:
        return LAYER_LAKEHOUSE_MAP[layer]
    return default_workspace_id, default_lakehouse_id


def build_table_uri(entry, default_workspace_id, default_lakehouse_id):
    """
    Builds the OneLake path as Tables/<schema>/<table> - the exact depth a
    schema-enabled Fabric Lakehouse requires to recognize a Delta table and
    parse its columns/types into the Tables UI and SQL analytics endpoint.
    A third path segment (e.g. a medallion-layer folder) breaks that
    recognition: Fabric just shows the raw _delta_log with no schema. So
    medallion_layer is intentionally NOT part of the physical path here -
    it's still tracked, printed, and returned in the result for visibility,
    and can still route to a dedicated Lakehouse via LAYER_LAKEHOUSE_MAP.
    """
    schema_name = clean_identifier(entry.get("schema") or "dbo")
    table_name = clean_identifier(entry["table_name"])
    layer = entry.get("medallion_layer")

    ws, lh = resolve_lakehouse_target(layer, default_workspace_id, default_lakehouse_id)
    table_uri = f"abfss://{ws}@onelake.dfs.fabric.microsoft.com/{lh}/Tables/{schema_name}/{table_name}"
    return table_uri, schema_name, table_name


def sync_table(table_uri, schema_name, table_name, target_pa_schema, storage_options, log_fn=print):
    """
    Ensure the Delta table at `table_uri` exists and conforms to
    `target_pa_schema`. Missing columns are added via alter; columns
    whose type changed, are only reported — never auto-altered, since that
    could destroy existing data.
    """
    from deltalake import write_deltalake, DeltaTable
    from deltalake.schema import Schema as DeltaSchema

    target_fields = {f.name: f for f in DeltaSchema.from_arrow(target_pa_schema).fields}

    try:
        dt = DeltaTable(table_uri, storage_options=storage_options)
    except Exception:
        empty_table = pa.Table.from_pylist([], schema=target_pa_schema)
        write_deltalake(table_uri, empty_table, storage_options=storage_options, mode="error")
        log_fn(f"  [CREATE] Created {schema_name}.{table_name}")
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
        log_fn(f"  [UPDATE] {schema_name}.{table_name}: added columns {new_names}")

    if removed_names:
        log_fn(f"  [WARN] {schema_name}.{table_name}: columns no longer in report, NOT dropped: {removed_names}")

    if changed_names:
        log_fn(f"  [WARN] {schema_name}.{table_name}: type changed in report, NOT altered: {changed_names}")

    if not new_names and not removed_names and not changed_names:
        log_fn(f"  [OK] {schema_name}.{table_name} already in sync")


def Generator(json_path=None, dry_run=False, source_system=None, database_name=None, workspace_id=None):
    """
    Generate/synchronize Fabric Delta tables from migration_plan.json.

    Resolves (or creates) a Lakehouse named "<source_system>_<database_name>"
    in the target workspace to hold the artifacts - e.g. source_system=
    "databricks", database_name="sales_prod" -> lakehouse "databricks_sales_prod".
    If a Lakehouse with that name already exists in the workspace, it is
    reused rather than duplicated.

    This function can be called directly from Django.

    Returns:
        dict containing execution information.
    """
    logs_list = []
    def log(msg=""):
        print(msg)
        logs_list.append(str(msg))

    if json_path is None:
        json_path = JSON_PATH

    json_path = Path(json_path).resolve()

    output_dir = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output"
    assessment_candidates = [
        output_dir / "databricks_Assessment_Report.docx",
        output_dir / "Assesment Report.docx",
        output_dir / "Metadata_Report.docx"
    ]
    assessment_doc = next((p for p in assessment_candidates if p.exists()), None)

    # Rebuild migration_plan.json from the scan assessment report whenever:
    # 1. json_path is the default output path and a report is present (ensures 1:1 match with scan)
    # 2. json_path is missing or empty
    # 3. assessment_doc has been modified since migration_plan.json was created
    # 4. the existing JSON belongs to a different source system
    should_rebuild = not json_path.exists() or (json_path.exists() and json_path.stat().st_size == 0)
    if json_path == JSON_PATH.resolve() and assessment_doc:
        should_rebuild = True
    elif json_path.exists() and assessment_doc and (assessment_doc.stat().st_mtime >= json_path.stat().st_mtime):
        should_rebuild = True
    elif json_path.exists() and not should_rebuild:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    should_rebuild = True
                else:
                    existing_meta = json.loads(content).get("meta", {})
                    if source_system and existing_meta.get("source_system", "").lower() != source_system.lower():
                        should_rebuild = True
        except Exception:
            should_rebuild = True

    if should_rebuild and assessment_doc:
        if assessment_doc:
            plan_candidates = [
                output_dir / "databricks_Migration_Plan.docx",
                output_dir / "AI_Migration_Plan.docx",
                output_dir / "Migration_Assessment.docx"
            ]
            plan_doc = next((p for p in plan_candidates if p.exists()), None)
            try:
                try:
                    from Artifacts_Generator.plan_to_json import build_plan
                except ImportError:
                    from plan_to_json import build_plan
                plan_data = build_plan(
                    assessment_doc,
                    migration_plan_path=plan_doc if plan_doc and plan_doc.exists() else None,
                    source_system=source_system or "databricks",
                    database_name=database_name
                )
                json_path.parent.mkdir(parents=True, exist_ok=True)
                plan_json_str = json.dumps(plan_data, indent=2, ensure_ascii=False)
                with open(json_path, "w", encoding="utf-8") as f:
                    f.write(plan_json_str)
                log(f"[INFO] Auto-generated {json_path.name} from {assessment_doc.name}")
            except Exception as e:
                log(f"[WARN] Could not auto-build {json_path.name}: {e}")

    if not json_path.exists():
        msg = f"Migration JSON not found at {json_path}. Please run scan first."
        return {
            "status": "error",
            "message": msg,
            "logs": [f"[ERROR] {msg}"],
            "errors": [msg],
            "tables": []
        }

    plan = load_plan(json_path)
    tables = plan.get("tables", [])

    # source_system/database_name aren't always passed explicitly - fall
    # back to whatever plan_to_json.py recorded in meta, if anything.
    meta = plan.get("meta", {}) or {}
    source_system = source_system or meta.get("source_system")
    database_name = database_name or meta.get("database_name")
    target_workspace_id = workspace_id or WORKSPACE_ID
    target_workspace_id = workspace_id or WORKSPACE_ID

    log("==================================================")
    log("JSON -> FABRIC")
    log("==================================================")
    log(f"[INFO] JSON: {json_path}")
    log(f"[INFO] Tables found: {len(tables)}")
    log(f"[INFO] Dry run: {dry_run}")
    log(f"[INFO] Source system: {source_system}")

    storage_options = None
    fabric_token = None

    caller_id = "unknown"
    caller_name = "Azure Identity"
    if not dry_run:
        token = fabric_api.get_onelake_token()
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            caller_id = claims.get("upn") or claims.get("appid") or claims.get("azp") or claims.get("oid") or "Service Principal"
            caller_name = claims.get("name") or claims.get("app_displayname") or "Service Principal"
            tid = claims.get("tid", "")
            log(f"[INFO] Authenticated Identity: {caller_name} (ID: {caller_id}, Tenant: {tid})")
        except Exception:
            pass

        storage_options = {
            "bearer_token": token,
            "use_fabric_endpoint": "true",
            "allow_unsafe_rename": "true",
        }
        fabric_token = fabric_api.get_fabric_api_token()

    target_workspace_id, default_lakehouse_id, artifact_lakehouse_name = resolve_artifact_lakehouse(
        source_system, database_name, target_workspace_id, fabric_token, dry_run
    )
    log(f"[INFO] Target workspace: {target_workspace_id}")
    log(f"[INFO] Artifact lakehouse: {artifact_lakehouse_name}")

    created_or_updated = []
    skipped = []
    errors = []

    for table in tables:

        table_name = table.get("table_name")

        if not table_name:
            errors.append({
                "table": None,
                "error": "Table does not contain table_name"
            })
            continue

        cols = table.get("columns") or []

        if not cols:
            skipped.append(table_name)

            print(
                f"[WARN] Skipping {table_name}: "
                f"no column information found."
            )

            continue

        try:

            table_uri, schema_name, clean_table_name = build_table_uri(
                table, target_workspace_id, default_lakehouse_id
            )

            layer = table.get("medallion_layer") or "(no layer assigned)"

            load_strategy = (
                table.get("load_strategy")
                or "(not specified)"
            )

            log(
                f"=== {schema_name}.{clean_table_name} "
                f"({len(cols)} columns) | "
                f"Layer: {layer} | "
                f"Load: {load_strategy} ==="
            )

            fields = []

            for col in cols:

                column_name = clean_identifier(
                    col.get("name")
                )

                data_type = col.get("data_type")

                if not column_name:
                    log(
                        "[WARN] Ignoring column with empty name."
                    )
                    continue

                arrow_type = map_arrow_type(data_type)

                fields.append(
                    pa.field(
                        column_name,
                        arrow_type,
                        nullable=True
                    )
                )

                log(
                    f"  {column_name:30s} "
                    f"{str(data_type):20s} -> "
                    f"{arrow_type}"
                )

            if not fields:
                skipped.append(table_name)
                continue

            schema = pa.schema(fields)

            if dry_run:

                log(
                    f"[DRY-RUN] Would synchronize: "
                    f"{table_uri}"
                )

            else:

                sync_table(
                    table_uri=table_uri,
                    schema_name=schema_name,
                    table_name=clean_table_name,
                    target_pa_schema=schema,
                    storage_options=storage_options,
                    log_fn=log
                )

            created_or_updated.append({
                "schema": schema_name,
                "table": clean_table_name,
                "layer": layer,
                "load_strategy": load_strategy,
                "columns": len(fields),
                "uri": table_uri
            })

        except Exception as exc:

            log(
                f"[ERROR] Failed processing "
                f"{table_name}: {exc}"
            )

            errors.append({
                "table": table_name,
                "error": str(exc)
            })

    log("==================================================")
    log("JSON -> FABRIC COMPLETED")
    log("==================================================")

    log(
        f"[INFO] Processed: {len(created_or_updated)}"
    )

    log(
        f"[INFO] Skipped: {len(skipped)}"
    )

    log(
        f"[INFO] Errors: {len(errors)}"
    )

    synced_list = [f"{t['schema']}.{t['table']} ({t['columns']} columns) [{t.get('layer', 'Table')}]" for t in created_or_updated]
    tables_meta = [{"schema": t["schema"], "table": t["table"], "columns_count": t["columns"]} for t in created_or_updated]
    err_list = [f"{e.get('table', 'Error')}: {str(e.get('error', '')).replace('\u21b3', '->')}" for e in errors]

    return {
        "status": "success" if not errors else ("partial" if created_or_updated else "error"),
        "message": f"Synced {len(created_or_updated)} table(s) to Fabric Lakehouse '{artifact_lakehouse_name}' ({len(errors)} error(s)).",
        "tables": synced_list,
        "tables_info": tables_meta,
        "errors": err_list,
        "logs": logs_list,
        "generator_script": "DB2_2_Fabric.py",
        "source_system": "Databricks",
        "target": {
            "workspace_id": target_workspace_id,
            "lakehouse_id": default_lakehouse_id,
            "report_name": artifact_lakehouse_name
        },
        "lakehouse_name": artifact_lakehouse_name,
        "processed": created_or_updated,
        "skipped": skipped
    }

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--json",
        default=None,
        help=f"Path to migration_plan.json"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Azure auth / OneLake writes"
    )

    parser.add_argument(
        "--source-system",
        default=None,
        help="Source system name used to build the artifact lakehouse name, e.g. 'databricks', 'sqlserver'"
    )

    parser.add_argument(
        "--database-name",
        default=None,
        help="Source database name used to build the artifact lakehouse name, e.g. 'sales_prod'"
    )

    args = parser.parse_args()

    json_path = (
        Path(args.json).resolve()
        if args.json
        else None
    )

    Generator(
        json_path=json_path,
        dry_run=args.dry_run,
        source_system=args.source_system,
        database_name=args.database_name,
    )