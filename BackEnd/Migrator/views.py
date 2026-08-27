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
        update_scan_job_state(scan_id, progress=25, current_message="Extracting schema and table metadata...", log_entry="Extracting Metadata from DataBase")
        print("Extracting Metadata from DataBase")
        metadata = obj.extract()
        original_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        metadata = _limit_metadata_tables(metadata)
        selected_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        update_scan_job_state(scan_id, progress=45, current_message=f"Metadata extracted ({selected_table_count} tables)", log_entry=f"Selected {selected_table_count} of {original_table_count} tables for analysis.")
        time.sleep(1.0)

        update_scan_job_state(scan_id, progress=55, current_message="Running Harness Layer 1 validation...", log_entry=f"\n{'='*30}\n{'='*30}\nMetaData Extracted\nRunning Harnnes Layer-1")
        print("MetaData Extracted\nRunning Harnness Layer-1")
        time.sleep(1.5)
        layer_result = layer1_Harness(metadata)
        temp = format_harness_report(layer_result)
        
        update_scan_job_state(scan_id, progress=65, current_message="Harness Layer 1 validation completed.", log_entry=temp, log_type="Scan Info")
        update_scan_job_state(scan_id, log_entry=temp, log_type="Harness Layer1")
        print(temp)
        time.sleep(1.2)

        update_scan_job_state(scan_id, progress=70, current_message="Generating Assessment Report and Migration Plan...", log_entry="Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        print("Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        
        # Forward scan_id to Agents_PipeLine
        output_files = Agents_PipeLine(metadata, source_hint=(scan_source or source), scan_id=scan_id)

        update_scan_job_state(scan_id, progress=95, current_message="Finalizing reports and logs...", log_entry="Output is avaliable at Show Logs embedded in the UI Screen")
        print(f"Output is avaliable at Show Logs embedded in the UI Screen")

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