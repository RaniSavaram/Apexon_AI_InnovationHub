import json
import os
import time
import traceback
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from django.http import FileResponse, Http404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.Credentials import PrivateVariables
from Migrator.connection_store import save_connection, get_saved_connection, get_saved_connections
from Metadata_Scanner.extractors.sqlserver import SQLServerExtractor
#from Metadata_Scanner.extractors.oracle import OracleExtractor
#from Metadata_Scanner.extractors.mysql import MySQLExtractor
#from Metadata_Scanner.extractors.postgres import PostgreSQLExtractor
#from Metadata_Scanner.extractors.sqlite import SQLiteExtractor
from Metadata_Scanner.extractors.synapse import SynapseExtractor
from Metadata_Scanner.extractors.snowflake_extractor import SnowflakeExtractor
from Metadata_Scanner.extractors.databricks_client import DatabricksExtractor
from Metadata_Scanner.extractors.dynamics365 import Dynamics365Extractor
#from Metadata_Scanner.extractors.sap import SAPExtractor
from AI_Agent_Pipeline.Agents_PipeLine import Agents_PipeLine
from HarnessLayers.layer1.Layer import layer1_Harness
from HarnessLayers.Harness_Json_Log_Formatter import format_harness_report
from Logs import Logs,reset_Logs

DEFAULT_HARNESS_LAYER2_LOGS = [
    "HARNESS LAYER 2:",
    "Waiting for the AI assessment and migration report generation to finish.",
]

Creds = PrivateVariables()
source = None
scan_jobs = {}
scan_jobs_lock = Lock()
MAX_SCAN_TABLES = int(os.environ.get("MAX_SCAN_TABLES", "5"))


def _limit_metadata_tables(metadata):
    remaining = MAX_SCAN_TABLES
    limited_schemas = []
    for schema in metadata.get("schemas", []):
        tables = schema.get("tables", [])
        selected_tables = tables[:remaining]
        if selected_tables:
            limited_schema = dict(schema)
            limited_schema["tables"] = selected_tables
            limited_schemas.append(limited_schema)
            remaining -= len(selected_tables)
        if remaining == 0:
            break

    limited_metadata = dict(metadata)
    limited_metadata["schemas"] = limited_schemas
    return limited_metadata


def _push_databricks_to_fabric(output_files, database_name):
    """
    Converts the just-generated Assessment Report + Migration Plan docx
    into migration_plan.json and pushes it straight into the pre-provisioned
    "Databricks_Lakehouse" in Fabric via DB2_2_Fabric.py - no manual CLI
    step needed for databricks scans. Only called for db_type == "databricks";
    other sources aren't wired up to a real Fabric Lakehouse yet.

    Returns the dict DB2_2_Fabric.Generator() returns (status/processed/errors).
    Raises on failure - callers should catch and log rather than fail the scan,
    since the docx reports themselves already succeeded by the time this runs.
    """
    from Artifacts_Generator.plan_to_json import build_plan
    from Artifacts_Generator import DB2_2_Fabric

    output_dir = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output"
    assessment_path = output_dir / output_files["assessment_report"]
    migration_plan_path = output_dir / output_files["migration_plan"]

    plan = build_plan(
        assessment_path=assessment_path,
        migration_plan_path=migration_plan_path,
        source_system="databricks",
        database_name=database_name,
    )

    json_path = output_dir / "migration_plan.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)

    return DB2_2_Fabric.Generator(
        json_path=json_path,
        dry_run=False,
        source_system="databricks",
        database_name=database_name,
    )


def _scan_status(scan_id):
    with scan_jobs_lock:
        return scan_jobs.get(scan_id)


def update_scan_job_state(scan_id=None, progress=None, current_message=None, log_entry=None, log_type="Scan Info"):
    if log_entry:
        print(f"[LOG][{log_type}] {log_entry}")
        if log_type in Logs:
            Logs[log_type].append(log_entry)
        if log_type == "Scan Info":
            Logs["Scan Info"].append(log_entry)

    if progress is not None:
        Logs["Progress Percentage"] = progress

    if not scan_id:
        return

    with scan_jobs_lock:
        job = scan_jobs.get(scan_id)
        if not job:
            return
        if progress is not None:
            job["progress"] = progress
            
        if current_message is not None:
            job["current_message"] = current_message
        elif log_entry and isinstance(log_entry, str):
            clean_log = log_entry.strip().replace("[INFO] ", "").replace("[INFO]", "").replace("[Err] ", "").replace("[Err]", "").strip()
            if "\n" not in clean_log and len(clean_log) < 120:
                job["current_message"] = clean_log

        if log_entry:
            if log_type == "Scan Info":
                job["logs"].append(log_entry)
            elif log_type == "Harness Layer1":
                job["harness1_logs"].append(log_entry)
            elif log_type == "Harness Layer2":
                job["harness2_logs"].append(log_entry)
            elif log_type == "Token Info":
                job["token_info"].append(log_entry)


def _run_scan_in_background(scan_id, destination):
    try:
        job = None
        with scan_jobs_lock:
            job = scan_jobs.get(scan_id)
        job_source = job.get("source") if job else None
        
        # Pass scan_id down to _run_scan
        result = _run_scan(destination, job_source, scan_id)
        status = "Completed" if result.status_code < 400 else "Failed"
        error = result.data.get("message") if status == "Failed" else None
        result_data = dict(result.data)
    except Exception as exc:
        status = "Failed"
        error = str(exc)
        result_data = {}
        update_scan_job_state(scan_id, log_entry=f"[ERROR] Scan failed: {exc}", log_type="Scan Info")
        update_scan_job_state(scan_id, log_entry=traceback.format_exc(), log_type="Scan Info")
        update_scan_job_state(scan_id, log_entry=f"[FAILED] Scan stopped during Layer 2 or report generation: {exc}", log_type="Harness Layer2")

    with scan_jobs_lock:
        job = scan_jobs.get(scan_id)
        if job:
            job.update({
                "status": status,
                "error": error,
                "result": result_data,
                "phase": "completed" if status == "Completed" else "failed",
                "progress": 100 if status == "Completed" else job.get("progress", 0),
                "current_message": "Scan completed successfully." if status == "Completed" else "Scan failed."
            })


@api_view(["GET"])
def scan_status(request, scan_id):
    scan_id_str = str(scan_id)
    job = _scan_status(scan_id_str)
    if not job:
        return Response({"status": "Failed", "error": "Scan not found."}, status=404)

    response = {
        "status": job["status"],
        "progressbar": job.get("progress", 0),
        "scan_status_message": job.get("current_message", ""),
        "Logs": {
            "Token Info": job.get("token_info", []),
            "Scan Info": job.get("logs", []),
            "Harness Layer1": job.get("harness1_logs", []),
            "Harness Layer2": job.get("harness2_logs", []) or DEFAULT_HARNESS_LAYER2_LOGS,
        },
        "result": job.get("result"),
        "error": job.get("error")
    }
    return Response(response)


def serve_generated_document(request, filename):
    """Serve generated DOCX reports from the AI_Agent_Pipeline output folder."""
    output_dir = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output"
    file_path = (output_dir / filename).resolve()
    try:
        file_path.relative_to(output_dir.resolve())
    except ValueError as exc:
        raise Http404("Invalid file path.") from exc

    if file_path.suffix.lower() != ".docx" or not file_path.is_file():
        raise Http404("Document not found.")

    if request.GET.get("view") == "1":
        host = request.get_host()
        if "localhost" not in host and "127.0.0.1" not in host:
            from django.http import HttpResponseRedirect
            import urllib.parse
            public_url = request.build_absolute_uri(request.path)
            if public_url.startswith("http://"):
                public_url = "https://" + public_url[7:]
            viewer_url = f"https://view.officeapps.live.com/op/view.aspx?src={urllib.parse.quote(public_url)}"
            return HttpResponseRedirect(viewer_url)

    response = FileResponse(file_path.open("rb"), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    disposition = "inline" if request.GET.get("view") == "1" else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{file_path.name}"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@api_view(["POST"])
def connect_database(request):

    global source
    reset_Logs()

    print("[INFO]: Connect request received")
    Logs["Scan Info"].append("Connect request received")

    source = request.data.get("source")
    remember_me = str(request.data.get("remember_me", "false")).strip().lower() == "true"
    Creds.set_servername(request.data.get("server"))
    Creds.set_database_name(request.data.get("database"))
    Creds.set_username (request.data.get("username"))
    Creds.set_password (request.data.get("password"))
    Creds.set_port(request.data.get("port") or None)
    Creds.set_extra_dict(request.data.get("extra") or {})
    print("[INFO]: Connection Details recieved")
    Logs["Scan Info"].append("[INFO]: Connection Details recieved")

    try:
        server = Creds.get_servername()
        database = Creds.get_database_name()

        # Field requirements differ by DB type family:
        #   - SQLite: only needs a file path (in `database`)
        #   - Dynamics 365: needs the org URL (in `server`) + tenant/client
        #     id/secret in `extra` - no `database`/username/password
        #   - Everything else: needs both server and database
        if source == "sqlite":
            if not database:
                Logs["Scan Info"].append("[Err]: Database file path is required.")
                return Response(
                    {"status": "error", "message": "Database file path is required."},
                    status=400
                )
        elif source in ("dynamics365", "dynamics 365", "d365"):
            if not server:
                Logs["Scan Info"].append("[Err]: Org URL is required.")
                return Response(
                    {"status": "error", "message": "Org URL (Server field) is required."},
                    status=400
                )
        elif not server or not database:
            Logs["Scan Info"].append("[Err]: Server or database name are required.")
            return Response(
                {"status": "error", "message": "Server and database name are required."},
                status=400
            )

        db_type = (source or "").lower()

        if db_type == "sqlserver":
            test_extractor = SQLServerExtractor(Creds)

        elif db_type == "synapse":
            test_extractor = SynapseExtractor(Creds)

        elif db_type == "snowflake":
            test_extractor = SnowflakeExtractor(Creds)

        elif db_type == "databricks":
            test_extractor = DatabricksExtractor(Creds)

        elif db_type in ("dynamics365", "dynamics 365", "d365"):
            test_extractor = Dynamics365Extractor(Creds)

        else:
            Logs["Scan Info"].append(f"[Err]: Unsupported database type: '{source}'")
            return Response(
                {
                    "status": "error",
                    "message": (
                        f"Unsupported database type: '{source}'. Supported: sqlserver, "
                        f"oracle, mysql, postgres, sqlite, synapse, snowflake, "
                        f"databricks, dynamics365, sap"
                    )
                },
                status=400
            )

        test_extractor.connect()
        print("[INFO]: Successfully Connected To the Database")
        Logs["Scan Info"].append("[INFO]: Successfully Connected To the Database")
        test_extractor.close()

        # Remember-me is now handled on the browser side with localStorage so
        # credentials are never stored in the repo or committed to GitHub.
        # The backend only writes to a temp file when explicitly requested.
        if remember_me:
            save_connection(source, {
                "server": server,
                "database": database,
                "username": Creds.get_username(),
                "password": Creds.get_password(),
                "extra": Creds.get_extra_dict(),
            })

    except Exception as e:
         print(e)
         Logs["Scan Info"].append(str(e))
         return Response({"status": "error", "message": str(e)}, status=400)

    
    Logs["Scan Info"].append(f"[INFO]: Connected to {database} on {server} successfully.")
    return Response({
        "status": "success",
        "message": f"Connected to {database} on {server} successfully.",
        "source": source,
        "Logs":Logs
    })

@api_view(["GET"])
def saved_connection(request):
    src = request.query_params.get("source")
    if not src:
        return Response(
            {"status": "error", "message": "Query param 'source' is required."},
            status=400
        )

    saved = get_saved_connection(src)
    if not saved:
        return Response({"status": "success", "found": False})

    return Response({"status": "success", "found": True, "connection": saved})


@api_view(["GET"])
def saved_connections(request):
    """Returns every saved profile for a source (e.g. all known Databricks
    servers), so the frontend can offer a picker instead of a single
    pre-filled form."""
    src = request.query_params.get("source")
    if not src:
        return Response(
            {"status": "error", "message": "Query param 'source' is required."},
            status=400
        )

    return Response({
        "status": "success",
        "connections": get_saved_connections(src),
    })


@api_view(["GET", "POST"])
def Db_Scanner(request):
    scan_id = str(uuid4())
    destination = request.data.get("destination")
    req_source = request.data.get("source") or source

    with scan_jobs_lock:
        scan_jobs[scan_id] = {
            "status": "Running",
            "source": req_source,
            "destination": destination,
            "scan_id": scan_id,
            "error": None,
            "phase": "starting",
            "progress": 5,
            "current_message": "Initializing scan...",
            "logs": ["Scan job initialized."],
            "harness1_logs": [],
            "harness2_logs": [],
            "token_info": []
        }

    Thread(target=_run_scan_in_background, args=(scan_id, destination), daemon=True).start()
    return Response({
        "status": "started",
        "message": "Database scan started.",
        "scan_id": scan_id,
    }, status=202)


def _run_scan(destination, scan_source=None, scan_id=None):

    # Some DB types don't populate both fields: SQLite has no server (just
    # a database file path), Dynamics 365 has no database (just an org
    # URL in server). Require at least one to confirm a connect happened.
    if not Creds.get_servername() and not Creds.get_database_name():
        return Response(
            {
                "status": "error",
                "message": "Connect to the database first."
            },
            status=400
        )
    
    update_scan_job_state(scan_id, progress=10, current_message="Starting database scan...", log_entry=f"[INFO]: {Creds.get_database_name()} DataBase Scan Started")
    print(f"[INFO]: {Creds.get_database_name()} DataBase Scan Started")
    time.sleep(1.0)

    db_type = (scan_source or source or "").lower()

    if db_type == "sqlserver":
        obj = SQLServerExtractor(Creds)

    elif db_type == "synapse":
        obj = SynapseExtractor(Creds)

    elif db_type == "snowflake":
        obj = SnowflakeExtractor(Creds)

    elif db_type == "databricks":
        obj = DatabricksExtractor(Creds)

    elif db_type in ("dynamics365", "dynamics 365", "d365"):
        obj = Dynamics365Extractor(Creds)

    else:
        update_scan_job_state(scan_id, log_entry=f"[Err]: Unsupported database type: '{source}'")
        return Response(
                {
                    "status": "error",
                    "message": (
                        f"Unsupported database type: '{source}'. Supported: sqlserver, "
                        f"oracle, mysql, postgres, sqlite, synapse, snowflake, "
                        f"databricks, dynamics365, sap"
                    )
                },
                status=400
            )
    try:
        update_scan_job_state(scan_id, progress=12, current_message="Connecting to database...", log_entry="Connecting to database for metadata extraction")
        update_scan_job_state(scan_id, progress=18, current_message="Extracting schema and table metadata...", log_entry="Extracting Metadata from DataBase")
        print("Extracting Metadata from DataBase")
        metadata = obj.extract()
        original_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        metadata = _limit_metadata_tables(metadata)
        selected_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        update_scan_job_state(scan_id, progress=26, current_message=f"Analyzing {selected_table_count} tables...", log_entry="Analyzing extracted schemas and tables")
        update_scan_job_state(scan_id, progress=32, current_message=f"Metadata extracted ({selected_table_count} tables)", log_entry=f"Selected {selected_table_count} of {original_table_count} tables for analysis.")
        time.sleep(0.5)

        update_scan_job_state(scan_id, progress=38, current_message="Running Harness Layer 1 validation...", log_entry=f"\n{'='*30}\n{'='*30}\nMetaData Extracted\nRunning Harnnes Layer-1")
        print("MetaData Extracted\nRunning Harnness Layer-1")
        time.sleep(1.0)
        layer_result = layer1_Harness(metadata)
        temp = format_harness_report(layer_result)
        
        update_scan_job_state(scan_id, progress=42, current_message="Harness Layer 1 validation completed.", log_entry=temp, log_type="Scan Info")
        update_scan_job_state(scan_id, log_entry=temp, log_type="Harness Layer1")
        print(temp)
        time.sleep(0.8)

        if layer_result.get("decision") != "PASS":
            update_scan_job_state(scan_id, log_entry="[Err]: DDL or DML statement identified - terminating process before the Assessment Agent / migration plan.")
            print("[Err]: DDL or DML statement identified - terminating process before the Assessment Agent / migration plan.")
            return Response(
                {
                    "status": "error",
                    "message": "DDL or DML statement identified - terminating process before the Assessment Agent / migration plan.",
                    "source": source,
                    "destination": destination,
                    "Logs": Logs,
                    "harness_result": layer_result,
                },
                status=400
            )

        update_scan_job_state(scan_id, progress=45, current_message="Generating Assessment Report and Migration Plan...", log_entry="Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        print("Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        
        # Forward scan_id to Agents_PipeLine (advances progress from 45% to 96%)
        output_files = Agents_PipeLine(metadata, source_hint=(scan_source or source), scan_id=scan_id)

        fabric_push = None
        if db_type == "databricks":
            update_scan_job_state(scan_id, progress=97, current_message="Syncing assessment with Microsoft Fabric OneLake...", log_entry="[INFO] Databricks source - auto-pushing assessment to Microsoft Fabric...")
            print("[INFO] Databricks source - auto-pushing assessment to Microsoft Fabric...")
            try:
                fabric_push = _push_databricks_to_fabric(output_files, Creds.get_database_name())
                update_scan_job_state(
                    scan_id,
                    progress=98,
                    current_message="Fabric OneLake artifacts synchronized.",
                    log_entry=(
                        f"[INFO] Fabric push completed: {fabric_push.get('processed_count')} table(s) "
                        f"created/updated in '{fabric_push.get('lakehouse_name')}', "
                        f"{fabric_push.get('error_count')} error(s)."
                    ),
                )
                print(f"[INFO] Fabric push completed: {fabric_push}")
            except Exception as fabric_exc:
                fabric_push = {"status": "error", "error": str(fabric_exc)}
                update_scan_job_state(
                    scan_id,
                    progress=98,
                    current_message="Fabric artifact sync note recorded.",
                    log_entry=f"[WARN] Fabric artifact push failed (reports above are still available): {fabric_exc}",
                )
                print(f"[WARN] Fabric artifact push failed: {fabric_exc}")

        update_scan_job_state(scan_id, progress=99, current_message="Finalizing reports and logs...", log_entry="Output is avaliable at Show Logs embedded in the UI Screen")
        print(f"Output is avaliable at Show Logs embedded in the UI Screen")
        time.sleep(0.4)

        update_scan_job_state(scan_id, progress=100, current_message="Database scan completed successfully.", log_entry="Database scan completed successfully.")
        print(f"Database scan completed successfully.")
        
        # Retrieve logs for direct response compat
        with scan_jobs_lock:
            job = scan_jobs.get(scan_id)
            job_logs = job.get("logs", []) if job else list(Logs.get("Scan Info", []))
            job_tokens = job.get("token_info", []) if job else list(Logs.get("Token Info", []))

        return Response({
                "status": "success",
                "message": "Database scan completed successfully. Refer to View Output tab below ",
                "source": source,
                "destination": destination,
                "data": job_tokens,
                "Logs": {
                    "Scan Info": job_logs,
                    "Harness Layer1": job.get("harness1_logs", []) if job else list(Logs.get("Harness Layer1", [])),
                    "Harness Layer2": job.get("harness2_logs", []) if job else list(Logs.get("Harness Layer2", [])),
                },
                "output_files": output_files,
                "tables_found": selected_table_count,
                "fabric_push": fabric_push,
            })
    except Exception as e:
        update_scan_job_state(scan_id, log_entry=str(e))
        return Response(
                        {
                            "status": "Error",
                            "message": str(e)
                        },
                        status=400
                    )
import os
import time
import traceback
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

from django.http import FileResponse, Http404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.Credentials import PrivateVariables
from Migrator.connection_store import save_connection, get_saved_connection, get_saved_connections
from Metadata_Scanner.extractors.sqlserver import SQLServerExtractor
#from Metadata_Scanner.extractors.oracle import OracleExtractor
#from Metadata_Scanner.extractors.mysql import MySQLExtractor
#from Metadata_Scanner.extractors.postgres import PostgreSQLExtractor
#from Metadata_Scanner.extractors.sqlite import SQLiteExtractor
from Metadata_Scanner.extractors.synapse import SynapseExtractor
from Metadata_Scanner.extractors.snowflake_extractor import SnowflakeExtractor
from Metadata_Scanner.extractors.databricks_client import DatabricksExtractor
from Metadata_Scanner.extractors.dynamics365 import Dynamics365Extractor
#from Metadata_Scanner.extractors.sap import SAPExtractor
from AI_Agent_Pipeline.Agents_PipeLine import Agents_PipeLine
from HarnessLayers.layer1.Layer import layer1_Harness
from HarnessLayers.Harness_Json_Log_Formatter import format_harness_report
from Logs import Logs,reset_Logs

DEFAULT_HARNESS_LAYER2_LOGS = [
    "HARNESS LAYER 2:",
    "Waiting for the AI assessment and migration report generation to finish.",
]

Creds = PrivateVariables()
source = None
scan_jobs = {}
scan_jobs_lock = Lock()
import threading
thread_local = threading.local()
MAX_SCAN_TABLES = int(os.environ.get("MAX_SCAN_TABLES", "5"))


def _limit_metadata_tables(metadata):
    remaining = MAX_SCAN_TABLES
    limited_schemas = []
    for schema in metadata.get("schemas", []):
        tables = schema.get("tables", [])
        selected_tables = tables[:remaining]
        if selected_tables:
            limited_schema = dict(schema)
            limited_schema["tables"] = selected_tables
            limited_schemas.append(limited_schema)
            remaining -= len(selected_tables)
        if remaining == 0:
            break

    limited_metadata = dict(metadata)
    limited_metadata["schemas"] = limited_schemas
    return limited_metadata


def _scan_status(scan_id):
    with scan_jobs_lock:
        return scan_jobs.get(scan_id)


def get_progress_from_log(log_entry):
    if not log_entry or not isinstance(log_entry, str):
        return None
    # Multi-line reports (e.g. Harness Layer 1 validation report) should not trigger single-line progress steps
    if "\n" in log_entry or len(log_entry) > 200:
        return None
    log_lower = log_entry.lower().strip()
    if "scan started" in log_lower:
        return 5
    if "connecting to" in log_lower or "connecting to database" in log_lower:
        return 12
    if "extracting metadata" in log_lower:
        return 18
    if "selected" in log_lower and "tables for analysis" in log_lower:
        return 26
    if "running harnnes layer-1" in log_lower or "running harness layer 1" in log_lower:
        return 34
    if "harness layer 1 validation completed" in log_lower:
        return 42
    if "starting evaluator-generator agents" in log_lower or "creating agents" in log_lower:
        return 45
    if "evaluator-generator agents created" in log_lower:
        return 48
    if "running table summarizer agent" in log_lower:
        return 55
    if "generating assessment report" in log_lower or "compiling table assessment report" in log_lower:
        return 68
    if "generating comprehensive migration roadmap" in log_lower or ("generating" in log_lower and "migration roadmap" in log_lower):
        return 72
    if "azure ai synthesizing workload patterns" in log_lower:
        return 75
    if "azure ai drafting medallion layer" in log_lower:
        return 78
    if "azure ai generating target fabric lakehouse" in log_lower:
        return 81
    if "azure ai formulating risk assessment" in log_lower:
        return 84
    if "successfully received migration roadmap" in log_lower or "azure ai migration roadmap completed" in log_lower:
        return 86
    if "compiling execution order and medallion plan" in log_lower:
        return 89
    if "generating fabric migration metadata" in log_lower:
        return 93
    if "fabric push completed" in log_lower or "auto-pushing assessment to microsoft fabric" in log_lower:
        return 97
    if "output is avaliable" in log_lower or "reports verified" in log_lower or "finalizing reports" in log_lower:
        return 99
    if log_lower.endswith("scan completed successfully.") or log_lower == "scan completed successfully":
        return 100
    return None


def update_scan_job_state(scan_id=None, progress=None, current_message=None, log_entry=None, log_type="Scan Info", skip_global=False):
    if progress is None and log_entry and isinstance(log_entry, str):
        detected_progress = get_progress_from_log(log_entry)
        if detected_progress is not None:
            progress = detected_progress

    if log_entry:
        if not skip_global:
            if log_type in Logs:
                Logs[log_type].append(log_entry)
            if log_type == "Scan Info" and log_type not in Logs:
                Logs["Scan Info"].append(log_entry)

    if progress is not None and not skip_global:
        current_global = Logs.get("Progress Percentage", 0)
        if not isinstance(current_global, (int, float)) or progress > current_global:
            Logs["Progress Percentage"] = progress

    if not scan_id:
        return

    with scan_jobs_lock:
        job = scan_jobs.get(scan_id)
        if not job:
            return
        if progress is not None:
            current_job_progress = job.get("progress", 0)
            if not isinstance(current_job_progress, (int, float)) or progress > current_job_progress:
                job["progress"] = progress
            
        if current_message is not None:
            clean_msg = current_message.strip()
            # Strip trailing dots so badge always shows a complete sentence
            while clean_msg.endswith("."):
                clean_msg = clean_msg[:-1].strip()
            job["current_message"] = clean_msg

        if log_entry and skip_global:
            if log_type == "Scan Info":
                job["logs"].append(log_entry)
            elif log_type == "Harness Layer1":
                job["harness1_logs"].append(log_entry)
            elif log_type == "Harness Layer2":
                job["harness2_logs"].append(log_entry)
            elif log_type == "Token Info":
                job["token_info"].append(log_entry)


def _run_scan_in_background(scan_id, destination):
    try:
        thread_local.active_scan_id = scan_id
        job = None
        with scan_jobs_lock:
            job = scan_jobs.get(scan_id)
        job_source = job.get("source") if job else None
        
        # Pass scan_id down to _run_scan
        result = _run_scan(destination, job_source, scan_id)
        status = "Completed" if result.status_code < 400 else "Failed"
        error = result.data.get("message") if status == "Failed" else None
        result_data = dict(result.data)
    except Exception as exc:
        status = "Failed"
        error = str(exc)
        result_data = {}
        update_scan_job_state(scan_id, log_entry=f"[ERROR] Scan failed: {exc}", log_type="Scan Info")
        update_scan_job_state(scan_id, log_entry=traceback.format_exc(), log_type="Scan Info")
        update_scan_job_state(scan_id, log_entry=f"[FAILED] Scan stopped during Layer 2 or report generation: {exc}", log_type="Harness Layer2")

    with scan_jobs_lock:
        job = scan_jobs.get(scan_id)
        if job:
            job.update({
                "status": status,
                "error": error,
                "result": result_data,
                "phase": "completed" if status == "Completed" else "failed",
                "progress": 100 if status == "Completed" else job.get("progress", 0),
                "current_message": "Scan completed successfully." if status == "Completed" else "Scan failed."
            })


@api_view(["GET"])
def scan_status(request, scan_id):
    scan_id_str = str(scan_id)
    job = _scan_status(scan_id_str)
    if not job:
        return Response({"status": "Failed", "error": "Scan not found."}, status=404)

    response = {
        "status": job["status"],
        "progressbar": job.get("progress", 0),
        "scan_status_message": job.get("current_message", ""),
        "Logs": {
            "Token Info": job.get("token_info", []),
            "Scan Info": job.get("logs", []),
            "Harness Layer1": job.get("harness1_logs", []),
            "Harness Layer2": job.get("harness2_logs", []) or DEFAULT_HARNESS_LAYER2_LOGS,
        },
        "result": job.get("result"),
        "error": job.get("error")
    }
    return Response(response)


def serve_generated_document(request, filename):
    """Serve generated DOCX reports from the AI_Agent_Pipeline output folder."""
    output_dir = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output"
    file_path = (output_dir / filename).resolve()
    try:
        file_path.relative_to(output_dir.resolve())
    except ValueError as exc:
        raise Http404("Invalid file path.") from exc

    if file_path.suffix.lower() != ".docx" or not file_path.is_file():
        raise Http404("Document not found.")

    if request.GET.get("view") == "1":
        host = request.get_host()
        if "localhost" not in host and "127.0.0.1" not in host:
            from django.http import HttpResponseRedirect
            import urllib.parse
            public_url = request.build_absolute_uri(request.path)
            if public_url.startswith("http://"):
                public_url = "https://" + public_url[7:]
            viewer_url = f"https://view.officeapps.live.com/op/view.aspx?src={urllib.parse.quote(public_url)}"
            return HttpResponseRedirect(viewer_url)

    response = FileResponse(file_path.open("rb"), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    disposition = "inline" if request.GET.get("view") == "1" else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{file_path.name}"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@api_view(["POST"])
def connect_database(request):

    global source
    reset_Logs()

    print("[INFO]: Connect request received")
    Logs["Scan Info"].append("Connect request received")

    source = request.data.get("source")
    remember_me = str(request.data.get("remember_me", "false")).strip().lower() == "true"
    Creds.set_servername(request.data.get("server"))
    Creds.set_database_name(request.data.get("database"))
    Creds.set_username (request.data.get("username"))
    Creds.set_password (request.data.get("password"))
    Creds.set_port(request.data.get("port") or None)
    Creds.set_extra_dict(request.data.get("extra") or {})
    print("[INFO]: Connection Details recieved")
    Logs["Scan Info"].append("[INFO]: Connection Details recieved")

    try:
        server = Creds.get_servername()
        database = Creds.get_database_name()

        # Field requirements differ by DB type family:
        #   - SQLite: only needs a file path (in `database`)
        #   - Dynamics 365: needs the org URL (in `server`) + tenant/client
        #     id/secret in `extra` - no `database`/username/password
        #   - Everything else: needs both server and database
        if source == "sqlite":
            if not database:
                Logs["Scan Info"].append("[Err]: Database file path is required.")
                return Response(
                    {"status": "error", "message": "Database file path is required."},
                    status=400
                )
        elif source in ("dynamics365", "dynamics 365", "d365"):
            if not server:
                Logs["Scan Info"].append("[Err]: Org URL is required.")
                return Response(
                    {"status": "error", "message": "Org URL (Server field) is required."},
                    status=400
                )
        elif not server or not database:
            Logs["Scan Info"].append("[Err]: Server or database name are required.")
            return Response(
                {"status": "error", "message": "Server and database name are required."},
                status=400
            )

        db_type = (source or "").lower()

        if db_type == "sqlserver":
            test_extractor = SQLServerExtractor(Creds)

        elif db_type == "synapse":
            test_extractor = SynapseExtractor(Creds)

        elif db_type == "snowflake":
            test_extractor = SnowflakeExtractor(Creds)

        elif db_type == "databricks":
            test_extractor = DatabricksExtractor(Creds)

        elif db_type in ("dynamics365", "dynamics 365", "d365"):
            test_extractor = Dynamics365Extractor(Creds)

        elif db_type == "sqlite":
            # SQLite uses local file, bypass connect check
            test_extractor = None

        else:
            Logs["Scan Info"].append(f"[Err]: Unsupported database type: '{source}'")
            return Response(
                {
                    "status": "error",
                    "message": (
                        f"Unsupported database type: '{source}'. Supported: sqlserver, "
                        f"oracle, mysql, postgres, sqlite, synapse, snowflake, "
                        f"databricks, dynamics365, sap"
                    )
                },
                status=400
            )

        if test_extractor:
            test_extractor.connect()
            print("[INFO]: Successfully Connected To the Database")
            Logs["Scan Info"].append("[INFO]: Successfully Connected To the Database")
            test_extractor.close()
        else:
            print("[INFO]: Successfully Connected To the Database")
            Logs["Scan Info"].append("[INFO]: Successfully Connected To the Database")

        # Remember-me is now handled on the browser side with localStorage so
        # credentials are never stored in the repo or committed to GitHub.
        # The backend only writes to a temp file when explicitly requested.
        if remember_me:
            save_connection(source, {
                "server": server,
                "database": database,
                "username": Creds.get_username(),
                "password": Creds.get_password(),
                "extra": Creds.get_extra_dict(),
            })

    except Exception as e:
         print(e)
         Logs["Scan Info"].append(str(e))
         return Response({"status": "error", "message": str(e)}, status=400)

    
    Logs["Scan Info"].append(f"[INFO]: Connected to {database} on {server} successfully.")
    return Response({
        "status": "success",
        "message": f"Connected to {database} on {server} successfully.",
        "source": source,
        "Logs":Logs
    })

@api_view(["GET"])
def saved_connection(request):
    src = request.query_params.get("source")
    if not src:
        return Response(
            {"status": "error", "message": "Query param 'source' is required."},
            status=400
        )

    saved = get_saved_connection(src)
    if not saved:
        return Response({"status": "success", "found": False})

    return Response({"status": "success", "found": True, "connection": saved})


@api_view(["GET"])
def saved_connections(request):
    """Returns every saved profile for a source (e.g. all known Databricks
    servers), so the frontend can offer a picker instead of a single
    pre-filled form."""
    src = request.query_params.get("source")
    if not src:
        return Response(
            {"status": "error", "message": "Query param 'source' is required."},
            status=400
        )

    return Response({
        "status": "success",
        "connections": get_saved_connections(src),
    })


@api_view(["GET", "POST"])
def Db_Scanner(request):
    scan_id = str(uuid4())
    destination = request.data.get("destination")
    req_source = request.data.get("source") or source

    with scan_jobs_lock:
        scan_jobs[scan_id] = {
            "status": "Running",
            "source": req_source,
            "destination": destination,
            "scan_id": scan_id,
            "error": None,
            "phase": "starting",
            "progress": 5,
            "current_message": "Initializing scan",
            "logs": ["Scan job initialized."],
            "harness1_logs": [],
            "harness2_logs": [],
            "token_info": []
        }

    Thread(target=_run_scan_in_background, args=(scan_id, destination), daemon=True).start()
    return Response({
        "status": "started",
        "message": "Database scan started.",
        "scan_id": scan_id,
    }, status=202)


def get_db_display_name(source_slug):
    mapping = {
        "sqlserver": "SQL Server",
        "oracle": "Oracle",
        "mysql": "MySQL",
        "postgres": "PostgreSQL",
        "sqlite": "SQLite",
        "synapse": "Azure Synapse",
        "snowflake": "Snowflake",
        "databricks": "Databricks",
        "dynamics365": "Dynamics 365",
        "sap": "SAP HANA"
    }
    return mapping.get(str(source_slug).lower(), "Database")


def _run_scan(destination, scan_source=None, scan_id=None):

    # Some DB types don't populate both fields: SQLite has no server (just
    # a database file path), Dynamics 365 has no database (just an org
    # URL in server). Require at least one to confirm a connect happened.
    if not Creds.get_servername() and not Creds.get_database_name():
        return Response(
            {
                "status": "error",
                "message": "Connect to the database first."
            },
            status=400
        )
    
    db_name = get_db_display_name(scan_source or source)
    update_scan_job_state(scan_id, progress=5, current_message=f"Starting {db_name} scan...", log_entry=f"[INFO]: {Creds.get_database_name()} {db_name} Scan Started")
    print(f"[INFO]: {Creds.get_database_name()} {db_name} Scan Started")
    time.sleep(0.5)

    db_type = (scan_source or source or "").lower()

    if db_type == "sqlserver":
        obj = SQLServerExtractor(Creds)

    elif db_type == "synapse":
        obj = SynapseExtractor(Creds)

    elif db_type == "snowflake":
        obj = SnowflakeExtractor(Creds)

    elif db_type == "databricks":
        obj = DatabricksExtractor(Creds)

    elif db_type in ("dynamics365", "dynamics 365", "d365"):
        obj = Dynamics365Extractor(Creds)

    else:
        update_scan_job_state(scan_id, log_entry=f"[Err]: Unsupported database type: '{source}'")
        return Response(
                {
                    "status": "error",
                    "message": (
                        f"Unsupported database type: '{source}'. Supported: sqlserver, "
                        f"oracle, mysql, postgres, sqlite, synapse, snowflake, "
                        f"databricks, dynamics365, sap"
                    )
                },
                status=400
            )
    try:
        update_scan_job_state(scan_id, progress=12, current_message=f"Connecting to {db_name}...", log_entry=f"Connecting to {db_name} for metadata extraction")
        update_scan_job_state(scan_id, progress=18, current_message=f"Extracting {db_name} schema and table metadata...", log_entry=f"Extracting Metadata from {db_name}")
        print(f"Extracting Metadata from {db_name}")
        metadata = obj.extract()
        original_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        metadata = _limit_metadata_tables(metadata)
        selected_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        update_scan_job_state(scan_id, progress=26, current_message=f"Analyzing {selected_table_count} tables from {db_name}...", log_entry="Analyzing extracted schemas and tables")
        update_scan_job_state(scan_id, progress=32, current_message=f"{db_name} metadata extracted ({selected_table_count} tables)", log_entry=f"Selected {selected_table_count} of {original_table_count} tables for analysis.")
        time.sleep(0.5)

        update_scan_job_state(scan_id, progress=38, current_message=f"Running {db_name} Harness Layer 1 validation", log_entry=f"\n{'='*30}\n{'='*30}\n{db_name} MetaData Extracted\nRunning Harnnes Layer-1")
        print(f"{db_name} MetaData Extracted\nRunning Harnness Layer-1")
        time.sleep(1.0)
        layer_result = layer1_Harness(metadata)
        temp = format_harness_report(layer_result)
        
        update_scan_job_state(scan_id, progress=42, current_message=f"{db_name} Harness Layer 1 validation completed", log_entry=temp, log_type="Scan Info")
        update_scan_job_state(scan_id, log_entry=temp, log_type="Harness Layer1")
        print(temp)
        time.sleep(0.8)

        update_scan_job_state(scan_id, progress=45, current_message=f"Generating {db_name} Assessment Report and Migration Plan", log_entry=f"Using extracted {db_name} Metadata and Harness feedback to generate Assessment Report & Migration Plan")
        print(f"Using extracted {db_name} Metadata and Harness feedback to generate Assessment Report & Migration Plan")
        
        # Forward scan_id to Agents_PipeLine (advances progress from 45% to 96%)
        output_files = Agents_PipeLine(metadata, source_hint=(scan_source or source), scan_id=scan_id)

        update_scan_job_state(scan_id, progress=98, current_message="Finalizing reports and logs", log_entry="Output is avaliable at Show Logs embedded in the UI Screen")
        print(f"Output is avaliable at Show Logs embedded in the UI Screen")
        time.sleep(0.4)

        update_scan_job_state(scan_id, progress=100, current_message=f"{db_name} scan completed successfully.", log_entry=f"{db_name} scan completed successfully.")
        print(f"{db_name} scan completed successfully.")
        
        # Retrieve logs for direct response compat
        with scan_jobs_lock:
            job = scan_jobs.get(scan_id)
            job_logs = job.get("logs", []) if job else list(Logs.get("Scan Info", []))
            job_tokens = job.get("token_info", []) if job else list(Logs.get("Token Info", []))

        return Response({
                "status": "success",
                "message": "Database scan completed successfully. Refer to View Output tab below ",
                "source": source,
                "destination": destination,
                "data": job_tokens,
                "Logs": {
                    "Scan Info": job_logs,
                    "Harness Layer1": job.get("harness1_logs", []) if job else list(Logs.get("Harness Layer1", [])),
                    "Harness Layer2": job.get("harness2_logs", []) if job else list(Logs.get("Harness Layer2", [])),
                },
                "output_files": output_files,
                "tables_found": selected_table_count,
            })
    except Exception as e:
        update_scan_job_state(scan_id, log_entry=str(e))
        return Response(
                        {
                            "status": "Error",
                            "message": str(e)
                        },
                        status=400
                    )


@api_view(["GET"])
def debug_view(request):
    with scan_jobs_lock:
        serialized_jobs = {}
        for k, v in scan_jobs.items():
            serialized_jobs[k] = {
                "status": v.get("status"),
                "progressbar": v.get("progress"),
                "scan_status_message": v.get("current_message"),
                "logs": v.get("logs"),
                "harness1_logs": v.get("harness1_logs"),
                "harness2_logs": v.get("harness2_logs"),
            }
        return Response({
            "scan_jobs": serialized_jobs,
            "Logs": {
                "Scan Info": list(Logs.get("Scan Info", [])),
                "Harness Layer1": list(Logs.get("Harness Layer1", [])),
                "Harness Layer2": list(Logs.get("Harness Layer2", [])),
            }
        })


@api_view(["POST", "GET"])
def generate_fabric_artifacts(request):
    """
    Executes BackEnd/Artifacts_Generator/SQL_2_Fabric.py (for SQL Server)
    or BackEnd/Artifacts_Generator/DB2_2_Fabric.py (for Databricks)
    to create empty Delta tables directly in a Microsoft Fabric OneLake Lakehouse
    based on the selected source.
    """
    try:
        global source
        source_param = (
            request.data.get("source") if request.method == "POST" else request.GET.get("source")
        ) or ""
        if not source_param and source:
            source_param = str(source)

        # Fallback to latest scan job source if available
        if not source_param:
            with scan_jobs_lock:
                if scan_jobs:
                    latest_job = list(scan_jobs.values())[-1]
                    source_param = latest_job.get("source") or ""

        # Fallback to Creds if databricks connection is configured
        if not source_param and Creds.get_databricks_http_path():
            source_param = "databricks"

        doc_filename = request.data.get("filename") if request.method == "POST" else request.GET.get("filename")
        workspace_id = request.data.get("workspace_id") if request.method == "POST" else request.GET.get("workspace_id")
        lakehouse_id = request.data.get("lakehouse_id") if request.method == "POST" else request.GET.get("lakehouse_id")

        source_clean = (source_param or "").strip().lower().replace(" ", "").replace("_", "")

        # Route based on source system:
        # If source is Databricks -> execute DB2_2_Fabric.py
        # If source is SQL Server -> execute SQL_2_Fabric.py
        if "databricks" in source_clean:
            script_name = "DB2_2_Fabric.py"
            source_display = "Databricks"
            print(f"[INFO] Routing Generate Artifacts to: {script_name} for source: {source_display}")
            from Artifacts_Generator.DB2_2_Fabric import Generator as DatabricksGenerator
            result = DatabricksGenerator(source_system="databricks", workspace_id=workspace_id)
        else:
            script_name = "SQL_2_Fabric.py"
            source_display = "SQL Server"
            print(f"[INFO] Routing Generate Artifacts to: {script_name} for source: {source_display}")
            from Artifacts_Generator.SQL_2_Fabric import Generator as SqlServerGenerator
            doc_path = None
            if doc_filename:
                output_dir = Path(__file__).resolve().parent.parent / "AI_Agent_Pipeline" / "output"
                doc_path = output_dir / doc_filename
            result = SqlServerGenerator(doc_path=doc_path, workspace_id=workspace_id, lakehouse_id=lakehouse_id)

        result["generator_script"] = script_name
        result["source_system"] = source_display
        if "logs" in result and isinstance(result.get("logs"), list):
            result["logs"].insert(0, f"Routing Generate Artifacts to: {script_name} for source: {source_display}")

        return Response(result, status=200)
    except Exception as exc:
        traceback.print_exc()
        fallback_script = "DB2_2_Fabric.py" if "databricks" in (source_param if "source_param" in locals() else "").lower() else "SQL_2_Fabric.py"
        return Response({
            "status": "error",
            "message": str(exc),
            "logs": [str(exc)],
            "generator_script": fallback_script
        }, status=200)
