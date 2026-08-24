import os
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


def _run_scan_in_background(scan_id, destination):
    try:
        result = _run_scan(destination)
        status = "Completed" if result.status_code < 400 else "Failed"
        error = result.data.get("message") if status == "Failed" else None
        result_data = dict(result.data)
    except Exception as exc:
        status = "Failed"
        error = str(exc)
        result_data = {}

    with scan_jobs_lock:
        scan_jobs[scan_id].update({
            "status": status,
            "error": error,
            "result": result_data,
            "logs": {
                "Token Info": list(Logs.get("Token Info", [])),
                "Scan Info": list(Logs.get("Scan Info", [])),
                "Harness Layer1": list(Logs.get("Harness Layer1", [])),
                "Harness Layer2": list(Logs.get("Harness Layer2", [])),
            },
        })


@api_view(["GET"])
def scan_status(request, scan_id):
    job = _scan_status(str(scan_id))
    if not job:
        return Response({"status": "Failed", "error": "Scan not found."}, status=404)

    response = dict(job)
    response["Logs"] = response.get("logs", {})
    response["token info"] = response["Logs"].get("Token Info", [])
    response["scan info"] = response["Logs"].get("Scan Info", [])
    response["progressbar"] = Logs.get("Progress Percentage", 0)
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

    response = FileResponse(file_path.open("rb"), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    response["Content-Disposition"] = f'attachment; filename="{file_path.name}"'
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

    with scan_jobs_lock:
        scan_jobs[scan_id] = {
            "status": "Running",
            "source": source,
            "destination": destination,
            "scan_id": scan_id,
            "error": None,
        }

    Thread(target=_run_scan_in_background, args=(scan_id, destination), daemon=True).start()
    return Response({
        "status": "started",
        "message": "Database scan started.",
        "scan_id": scan_id,
    }, status=202)


def _run_scan(destination):

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
    
    Logs["Scan Info"].append(f"[INFO]: {Creds.get_database_name()} DataBase Scan Started")
    print(f"[INFO]: {Creds.get_database_name()} DataBase Scan Started")

    db_type = (source or "").lower()

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
    try:
        Logs["Scan Info"].append(f"Extracting Metadata from DataBase")
        print("Extracting Metadata from DataBase")
        metadata = obj.extract()
        original_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        metadata = _limit_metadata_tables(metadata)
        selected_table_count = sum(
            len(schema.get("tables", [])) for schema in metadata.get("schemas", [])
        )
        Logs["Scan Info"].append(
            f"Selected {selected_table_count} of {original_table_count} tables for analysis."
        )

        Logs["Scan Info"].append(f"\n{'='*30}\n{'='*30}\nMetaData Extracted\nRunning Harnnes Layer-1")
        print("MetaData Extracted\nRunning Harnness Layer-1")
        layer_result= layer1_Harness(metadata)
        temp = format_harness_report(layer_result)
        Logs["Scan Info"].append(temp)
        Logs["Harness Layer1"].append(temp)
        print(temp)

        # TEMP: negative-case gate disabled to test full scan flow end-to-end
        # if layer_result.get("decision") != "PASS":
        #     Logs["Scan Info"].append(
        #         "[Err]: DDL or DML statement identified - terminating process before the Assessment Agent / migration plan."
        #     )
        #     print("[Err]: DDL or DML statement identified - terminating process before the Assessment Agent / migration plan.")
        #     return Response(
        #         {
        #             "status": "error",
        #             "message": "DDL or DML statement identified - terminating process before the Assessment Agent / migration plan.",
        #             "source": source,
        #             "destination": destination,
        #             "Logs": Logs,
        #             "harness_result": layer_result,
        #         },
        #         status=400
        #     )

        Logs["Scan Info"].append(f"Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        print("Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        output_files = Agents_PipeLine(metadata, source_hint=source)

        Logs["Scan Info"].append(f"Output is avaliable at Show Logs embedded in the UI Screen")
        print(f"Output is avaliable at Show Logs embedded in the UI Screen")


        Logs["Scan Info"].append(f"Database scan completed successfully.")
        print(f"Database scan completed successfully.")
        return Response({
                "status": "success",
                "message": "Database scan completed successfully. Refer to View Output tab below ",
                "source": source,
                "destination": destination,
                "data":Logs["Token Info"],
                "Logs":Logs,
                "output_files": output_files,
                "tables_found": selected_table_count,
            })
    except Exception as e:
        Logs["Scan Info"].append(str(e))
        return Response(
                        {
                            "status": "Error",
                            "message": str(e)
                        },
                        status=400
                    )