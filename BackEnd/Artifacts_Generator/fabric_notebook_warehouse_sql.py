"""
Views/Stored-Procedures sync for a Fabric Warehouse, executed via a Fabric
Notebook instead of a direct external TDS connection (see
fabric_warehouse_sql.py for that original, direct-connection approach).

Why this exists: fabric_warehouse_sql.py connects straight from this
backend process to the Warehouse's SQL analytics endpoint, presenting an
externally-acquired (Azure CLI, via fabric_api.get_fabric_sql_token())
Azure AD access token over TDS. Against this tenant/workspace, that login
is rejected by the server with SQL error 18456 ("Login failed"), wrapped in
the oddly-worded message "Couldn't complete the operation due to a system
update. Close out this connection, sign in again, and retry the
operation." - even though the same identity authenticates fine through the
Fabric portal's own SQL query editor. That pattern (portal/embedded auth
succeeds, a raw externally-issued bearer token over TDS fails) matches a
Conditional Access / "allowed client apps" policy restricting which app
registrations may authenticate directly to Azure SQL/Warehouse endpoints -
Azure CLI's well-known public client ID is a common target for exactly
this kind of tenant-level restriction, and this backend has no way to
route around a policy like that from outside Fabric.

Verified workaround (against the real target workspace, 2026-09-03): a
notebook running *inside* Fabric, connecting via pyodbc using a token
minted by notebookutils.credentials.getToken() - Fabric's own internal,
first-party token issuance, a different trusted auth path from an
externally-acquired CLI token - IS accepted by the same Warehouse. So
instead of connecting directly from this process, this module:

  1. Builds one Notebook item's Python source (the DDL logic mirrors
     fabric_warehouse_sql.py's create_placeholder_view()/
     create_placeholder_procedure(), reimplemented inline here since the
     notebook runs on Fabric's remote compute and can't import this
     backend's local modules) with the target views/procedures embedded as
     a base64-encoded JSON literal - not interpolated as raw Python
     source - so arbitrary schema/view/procedure names or view definition
     text can never break out of the generated code.
  2. Creates/updates one fixed Notebook item with that source via the
     Fabric item-definition API (fabric_api.create_or_update_notebook()).
  3. Triggers a RunNotebook job and polls it to completion
     (fabric_api.run_notebook_job()).
  4. Reads back a JSON result the notebook wrote to a OneLake Files/ path
     (created/errors lists, the same shape sync_views_and_procedures() in
     DB2_2_Fabric.py already expects) - a job's REST status doesn't expose
     print()/cell output directly, so the notebook writes its result to a
     known location instead of us trying to scrape run output.

Best-effort like fabric_warehouse_sql.py's connection path was: any
failure here (notebook creation, the run itself, or reading back the
result) is caught by the caller in DB2_2_Fabric.py and reported as a
warning/error list rather than raised, so it can never take down a scan
that otherwise succeeded.
"""
import base64
import json
import uuid

import requests

try:
    from Artifacts_Generator import fabric_api
except ImportError:
    import fabric_api

NOTEBOOK_DISPLAY_NAME = "Fabric_Artifact_Warehouse_Sync"


def _build_notebook_source(warehouse_connection_string, warehouse_database_name, views, procedures, source_system, output_abfss_path):
    """
    Returns the full "Fabric notebook source" formatted Python string (see
    fabric_api.create_or_update_notebook()'s docstring for the required
    format) that will run inside Fabric and create each view/procedure as
    a structurally-valid but functionally empty placeholder, exactly like
    fabric_warehouse_sql.create_placeholder_view()/
    create_placeholder_procedure() do for the direct-connection path.

    warehouse_database_name is REQUIRED, not optional: this workspace hosts
    many Lakehouse/Warehouse items behind the same kind of hostname, and
    without an explicit initial `Database=` in the connection string the
    TDS login silently lands in some other item's database instead (a real
    incident: it landed in an unrelated "StockMarket_LH" item, and a probe
    view/procedure actually got created there before this was caught) -
    even though `warehouse_connection_string` is verified (via the Fabric
    REST API) to be this specific Warehouse's own connectionString
    property.

    views/procedures are embedded as a base64-encoded JSON blob (decoded
    at the top of the generated cell) rather than interpolated as Python
    literals directly, so arbitrary object names or view definition text
    can never accidentally break out of the generated source.
    """
    payload = base64.b64encode(
        json.dumps({"views": views, "procedures": procedures}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    # Indentation/quoting inside this cell body is plain Python - nothing
    # here is Fabric-templated, so normal Python escaping rules apply.
    cell_body = f'''import base64
import json
import struct

import pyodbc

_payload = json.loads(base64.b64decode("{payload}").decode("utf-8"))
views = _payload["views"]
procedures = _payload["procedures"]
source_system = {source_system!r}
warehouse_host = {warehouse_connection_string!r}
warehouse_database = {warehouse_database_name!r}
output_path = {output_abfss_path!r}

created = []
errors = []


def clean_sql_identifier(name):
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in (name or "").strip()) or "_"


def write_result():
    notebookutils.fs.put(output_path, json.dumps({{"created": created, "errors": errors}}), overwrite=True)
    print(f"RESULT_WRITTEN: {{output_path}}")


try:
    token = notebookutils.credentials.getToken("https://database.windows.net/")
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{{len(token_bytes)}}s", len(token_bytes), token_bytes)
    conn_str = (
        f"Driver={{{{ODBC Driver 18 for SQL Server}}}};Server={{warehouse_host}};"
        f"Database={{warehouse_database}};Encrypt=yes;TrustServerCertificate=no;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={{SQL_COPT_SS_ACCESS_TOKEN: token_struct}})
    cursor = conn.cursor()
    # Belt-and-suspenders: this workspace hosts many Lakehouse/Warehouse
    # items behind similar-looking hostnames, and a login has silently
    # landed in the wrong one before despite an explicit Database= clause
    # matching what the Fabric REST API reports for this item. Refuse to
    # touch anything rather than risk creating placeholders in someone
    # else's database again.
    cursor.execute("SELECT DB_NAME()")
    actual_db = cursor.fetchone()[0]
    if actual_db != warehouse_database:
        raise RuntimeError(
            f"Connected, but landed in database '{{actual_db}}' instead of the expected "
            f"'{{warehouse_database}}' - refusing to create objects in the wrong database."
        )
except Exception as exc:
    errors.append({{"object": None, "error": f"Warehouse connection unavailable: {{exc}}"}})
    write_result()
    raise


def ensure_schema(schema_name):
    schema_name = clean_sql_identifier(schema_name)
    cursor.execute(
        f"IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = '{{schema_name}}') "
        f"EXEC('CREATE SCHEMA [{{schema_name}}]')"
    )
    return schema_name


for v in views:
    schema_name, view_name = v.get("schema"), v.get("view_name")
    try:
        clean_schema = ensure_schema(schema_name)
        clean_view = clean_sql_identifier(view_name)
        comment_lines = [
            f"-- Placeholder view generated from a {{source_system or 'source'}} scan.",
            "-- The original view definition (untranslated - not valid T-SQL as-is) was:",
        ]
        for line in (v.get("definition") or "Not available").splitlines() or ["Not available"]:
            comment_lines.append(f"-- {{line}}")
        sql = (
            "\\n".join(comment_lines)
            + f"\\nCREATE OR ALTER VIEW [{{clean_schema}}].[{{clean_view}}] AS "
            + "SELECT CAST(NULL AS INT) AS placeholder_column WHERE 1 = 0;"
        )
        cursor.execute(sql)
        conn.commit()
        created.append(f"View {{clean_schema}}.{{clean_view}}")
    except Exception as exc:
        conn.rollback()
        errors.append({{"object": f"View {{schema_name}}.{{view_name}}", "error": str(exc)}})

for p in procedures:
    schema_name, procedure_name = p.get("schema"), p.get("procedure_name")
    try:
        clean_schema = ensure_schema(schema_name)
        clean_proc = clean_sql_identifier(procedure_name)
        sql = (
            f"-- Placeholder procedure generated from a {{source_system or 'source'}} scan; no logic ported.\\n"
            f"CREATE OR ALTER PROCEDURE [{{clean_schema}}].[{{clean_proc}}] AS\\nBEGIN\\n    RETURN 0;\\nEND;"
        )
        cursor.execute(sql)
        conn.commit()
        created.append(f"Procedure {{clean_schema}}.{{clean_proc}}")
    except Exception as exc:
        conn.rollback()
        errors.append({{"object": f"Procedure {{schema_name}}.{{procedure_name}}", "error": str(exc)}})

conn.close()
write_result()
'''

    return f'''# Fabric notebook source

# METADATA ********************

# META {{
# META   "kernel_info": {{
# META     "name": "jupyter"
# META   }}
# META }}

# CELL ********************

{cell_body}
'''


def sync_views_and_procedures(
    views, procedures, source_system, dry_run,
    workspace_id, lakehouse_id, warehouse_connection_string, warehouse_database_name, fabric_token,
):
    """
    Notebook-based replacement for fabric_warehouse_sql-based direct
    connection sync - see this module's docstring for why. `workspace_id`/
    `lakehouse_id` are the same target Lakehouse DB2_2_Fabric.py's
    Generator() already resolved for this scan's tables (reused purely as
    a OneLake location to stash the run's result JSON at, not because the
    Warehouse belongs to that Lakehouse - it doesn't; they're separate
    Fabric items).

    Returns (created, errors) - same shape as
    fabric_warehouse_sql-based sync_views_and_procedures() in
    DB2_2_Fabric.py returned, so callers there don't need to change.
    """
    created = []
    errors = []

    if not views and not procedures:
        return created, errors

    if dry_run:
        for v in views:
            print(f"[DRY-RUN] Would create placeholder view {v.get('schema')}.{v.get('view_name')} in Warehouse '{warehouse_connection_string}' (via Fabric Notebook)")
            created.append(f"View {v.get('schema')}.{v.get('view_name')}")
        for p in procedures:
            print(f"[DRY-RUN] Would create placeholder procedure {p.get('schema')}.{p.get('procedure_name')} in Warehouse '{warehouse_connection_string}' (via Fabric Notebook)")
            created.append(f"Procedure {p.get('schema')}.{p.get('procedure_name')}")
        return created, errors

    run_id = uuid.uuid4().hex
    output_abfss_path = (
        f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/"
        f"{lakehouse_id}/Files/_artifact_sync/{run_id}/result.json"
    )
    output_https_path = (
        f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}"
        f"/Files/_artifact_sync/{run_id}/result.json"
    )

    try:
        source = _build_notebook_source(warehouse_connection_string, warehouse_database_name, views, procedures, source_system, output_abfss_path)
        notebook_id = fabric_api.create_or_update_notebook(workspace_id, NOTEBOOK_DISPLAY_NAME, source, fabric_token)
        print(f"[INFO] Running '{NOTEBOOK_DISPLAY_NAME}' Fabric Notebook (id={notebook_id}) to sync {len(views)} view(s)/{len(procedures)} procedure(s)...")
        fabric_api.run_notebook_job(workspace_id, notebook_id, fabric_token)

        onelake_token = fabric_api.get_onelake_token()
        resp = requests.get(output_https_path, headers={"Authorization": f"Bearer {onelake_token}"})
        if not resp.ok:
            raise RuntimeError(f"Notebook run completed but its result file wasn't found at {output_https_path}: {resp.status_code} {resp.text}")

        result = resp.json()
        created = result.get("created", [])
        errors = result.get("errors", [])
        for c in created:
            print(f"  [CREATE] Placeholder {c} in Warehouse '{warehouse_connection_string}' (via Fabric Notebook)")
        for e in errors:
            print(f"[ERROR] {e.get('object')}: {e.get('error')}")

        return created, errors

    except Exception as exc:
        print(f"[WARN] Views/Stored Procedures scaffold unavailable: {exc}")
        errors.append({"object": None, "error": f"Warehouse connection unavailable: {exc}"})
        return created, errors
