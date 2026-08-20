import threading
import uuid

from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.Credentials import PrivateVariables

from Migrator.connection_store import (
    save_connection,
    get_saved_connection,
    get_saved_connections,
)

from Metadata_Scanner.extractors.sqlserver import SQLServerExtractor
# from Metadata_Scanner.extractors.oracle import OracleExtractor
# from Metadata_Scanner.extractors.mysql import MySQLExtractor
# from Metadata_Scanner.extractors.postgres import PostgreSQLExtractor
# from Metadata_Scanner.extractors.sqlite import SQLiteExtractor

from Metadata_Scanner.extractors.synapse import SynapseExtractor
from Metadata_Scanner.extractors.snowflake_extractor import SnowflakeExtractor

from Metadata_Scanner.extractors.databricks_client import (
    DatabricksExtractor,
    load_databricks_env_credentials,
)

from Metadata_Scanner.extractors.dynamics365 import Dynamics365Extractor
# from Metadata_Scanner.extractors.sap import SAPExtractor

from AI_Agent_Pipeline.Agents_PipeLine import Agents_PipeLine

from HarnessLayers.layer1.Layer import layer1_Harness
from HarnessLayers.Harness_Json_Log_Formatter import format_harness_report

from Logs import Logs, reset_Logs


# ============================================================
# GLOBAL CREDENTIALS / SOURCE
# ============================================================

Creds = PrivateVariables()
source = None
scan_jobs = {}
scan_jobs_lock = threading.Lock()


# ============================================================
# CONNECT DATABASE
# ============================================================

@api_view(["POST"])
def connect_database(request):

    global source

    reset_Logs()

    print("[INFO]: Connect request received")
    Logs["Scan Info"].append("Connect request received")

    source = request.data.get("source")

    Creds.set_servername(
        request.data.get("server")
    )

    Creds.set_database_name(
        request.data.get("database")
    )

    Creds.set_username(
        request.data.get("username")
    )

    Creds.set_password(
        request.data.get("password")
    )

    Creds.set_port(
        request.data.get("port") or None
    )

    Creds.set_extra_dict(
        request.data.get("extra") or {}
    )

    db_type = (source or "").lower()


    # ========================================================
    # DATABRICKS REMEMBER ME
    # ========================================================

    if (
        db_type == "databricks"
        and request.data.get("remember_me") is True
    ):

        env_credentials = load_databricks_env_credentials()

        Creds.set_servername(
            env_credentials["server"]
        )

        Creds.set_database_name(
            env_credentials["database"]
        )

        Creds.set_password(
            env_credentials["password"]
        )

        Creds.set_extra(
            "http_path",
            env_credentials["http_path"]
        )


    print("[INFO]: Connection Details recieved")

    Logs["Scan Info"].append(
        "[INFO]: Connection Details recieved"
    )


    # ========================================================
    # CONNECTIVITY TEST
    # ========================================================

    try:

        server = Creds.get_servername()
        database = Creds.get_database_name()


        # ----------------------------------------------------
        # FIELD VALIDATION
        # ----------------------------------------------------

        if source == "sqlite":

            if not database:

                Logs["Scan Info"].append(
                    "[Err]: Database file path is required."
                )

                return Response(
                    {
                        "status": "error",
                        "message": "Database file path is required.",
                    },
                    status=400,
                )


        elif source in (
            "dynamics365",
            "dynamics 365",
            "d365",
        ):

            if not server:

                Logs["Scan Info"].append(
                    "[Err]: Org URL is required."
                )

                return Response(
                    {
                        "status": "error",
                        "message": "Org URL (Server field) is required.",
                    },
                    status=400,
                )


        elif not server or not database:

            Logs["Scan Info"].append(
                "[Err]: Server or database name are required."
            )

            return Response(
                {
                    "status": "error",
                    "message": "Server and database name are required.",
                },
                status=400,
            )


        # ----------------------------------------------------
        # SELECT EXTRACTOR
        # ----------------------------------------------------

        if db_type == "sqlserver":

            test_extractor = SQLServerExtractor(
                Creds
            )

        elif db_type == "synapse":

            test_extractor = SynapseExtractor(
                Creds
            )

        elif db_type == "snowflake":

            test_extractor = SnowflakeExtractor(
                Creds
            )

        elif db_type == "databricks":

            test_extractor = DatabricksExtractor(
                Creds
            )

        elif db_type in (
            "dynamics365",
            "dynamics 365",
            "d365",
        ):

            test_extractor = Dynamics365Extractor(
                Creds
            )

        else:

            Logs["Scan Info"].append(
                f"[Err]: Unsupported database type: '{source}'"
            )

            return Response(
                {
                    "status": "error",
                    "message": (
                        f"Unsupported database type: '{source}'. "
                        "Supported: sqlserver, oracle, mysql, "
                        "postgres, sqlite, synapse, snowflake, "
                        "databricks, dynamics365, sap"
                    ),
                },
                status=400,
            )


        # ----------------------------------------------------
        # TEST CONNECTION
        # ----------------------------------------------------

        test_extractor.connect()

        print(
            "[INFO]: Successfully Connected To the Database"
        )

        Logs["Scan Info"].append(
            "[INFO]: Successfully Connected To the Database"
        )

        test_extractor.close()


        # ----------------------------------------------------
        # SAVE CONNECTION
        # ----------------------------------------------------

        if db_type != "databricks":

            save_connection(
                source,
                {
                    "server": server,
                    "database": database,
                    "username": Creds.get_username(),
                    "password": Creds.get_password(),
                    "extra": Creds.get_extra_dict(),
                },
            )


    except Exception as e:

        print(e)

        Logs["Scan Info"].append(
            str(e)
        )

        return Response(
            {
                "status": "error",
                "message": str(e),
            },
            status=400,
        )


    # ========================================================
    # CONNECTION SUCCESS
    # ========================================================

    Logs["Scan Info"].append(
        f"[INFO]: Connected to {database} on {server} successfully."
    )

    return Response(
        {
            "status": "success",
            "message": (
                f"Connected to {database} on {server} successfully."
            ),
            "source": source,
        }
    )


# ============================================================
# SAVED CONNECTION
# ============================================================

@api_view(["GET"])
def saved_connection(request):

    src = request.query_params.get(
        "source"
    )

    if not src:

        return Response(
            {
                "status": "error",
                "message": "Query param 'source' is required.",
            },
            status=400,
        )


    saved = get_saved_connection(
        src
    )

    if not saved:

        return Response(
            {
                "status": "success",
                "found": False,
            }
        )


    return Response(
        {
            "status": "success",
            "found": True,
            "connection": saved,
        }
    )


# ============================================================
# SAVED CONNECTIONS
# ============================================================

@api_view(["GET"])
def saved_connections(request):

    """
    Returns every saved profile for a source.
    """

    src = request.query_params.get(
        "source"
    )

    if not src:

        return Response(
            {
                "status": "error",
                "message": "Query param 'source' is required.",
            },
            status=400,
        )


    return Response(
        {
            "status": "success",
            "connections": get_saved_connections(src),
        }
    )


# ============================================================
# DATABASE SCANNER
# ============================================================

@api_view(["POST"])
def Db_Scanner(request):

    scan_id = str(uuid.uuid4())
    with scan_jobs_lock:
        scan_jobs[scan_id] = {
            "status": "running",
            "result": None,
            "error": None,
        }

    def run_scan_job():
        try:
            result = _run_scan_pipeline(request)
            with scan_jobs_lock:
                scan_jobs[scan_id] = {
                    "status": "completed" if result.status_code < 400 else "failed",
                    "result": result.data,
                    "error": None if result.status_code < 400 else result.data.get("message"),
                }
        except Exception as exc:
            with scan_jobs_lock:
                scan_jobs[scan_id] = {
                    "status": "failed",
                    "result": None,
                    "error": str(exc),
                }

    threading.Thread(target=run_scan_job, daemon=True).start()
    return Response({"status": "started", "scan_id": scan_id}, status=202)


@api_view(["GET"])
def scan_status(request, scan_id):
    with scan_jobs_lock:
        job = scan_jobs.get(scan_id)

    if not job:
        return Response({"status": "not_found"}, status=404)

    return Response({
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    })


def _run_scan_pipeline(request):

    destination = request.data.get(
        "destination"
    )

    connection = request.data.get(
        "connection",
        {}
    )


    # ========================================================
    # CHECK CONNECTION
    # ========================================================

    if (
        not Creds.get_servername()
        and not Creds.get_database_name()
    ):

        return Response(
            {
                "status": "error",
                "message": "Connect to the database first.",
            },
            status=400,
        )


    # ========================================================
    # START SCAN
    # ========================================================

    Logs["Scan Info"].append(
        f"{Creds.get_database_name()} DataBase Scan Started"
    )

    print(
        f"[INFO]: {Creds.get_database_name()} DataBase Scan Started"
    )


    db_type = (
        source or ""
    ).lower()


    # ========================================================
    # SELECT EXTRACTOR
    # ========================================================

    if db_type == "sqlserver":

        obj = SQLServerExtractor(
            Creds
        )

    elif db_type == "synapse":

        obj = SynapseExtractor(
            Creds
        )

    elif db_type == "snowflake":

        obj = SnowflakeExtractor(
            Creds
        )

    elif db_type == "databricks":

        obj = DatabricksExtractor(
            Creds
        )

    elif db_type in (
        "dynamics365",
        "dynamics 365",
        "d365",
    ):

        obj = Dynamics365Extractor(
            Creds
        )

    else:

        Logs["Scan Info"].append(
            f"[Err]: Unsupported database type: '{source}'"
        )

        return Response(
            {
                "status": "error",
                "message": (
                    f"Unsupported database type: '{source}'. "
                    "Supported: sqlserver, oracle, mysql, "
                    "postgres, sqlite, synapse, snowflake, "
                    "databricks, dynamics365, sap"
                ),
            },
            status=400,
        )


    # ========================================================
    # RUN DATABASE SCAN
    # ========================================================

    try:

        # ----------------------------------------------------
        # METADATA EXTRACTION
        # ----------------------------------------------------

        Logs["Scan Info"].append(
            "Extracting Metadata from DataBase"
        )

        print(
            "Extracting Metadata from DataBase"
        )


        metadata = obj.extract()


        # ----------------------------------------------------
        # HARNESS LAYER 1
        # ----------------------------------------------------

        Logs["Scan Info"].append(
            "\n"
            + "=" * 30
            + "\n"
            + "=" * 30
            + "\nMetaData Extracted\nRunning Harnnes Layer-1"
        )

        print(
            "MetaData Extracted\nRunning Harnness Layer-1"
        )


        layer_result = layer1_Harness(
            metadata
        )


        temp = format_harness_report(
            layer_result
        )


        Logs["Scan Info"].append(
            temp
        )

        Logs["Harness Layer1"].append(
            temp
        )


        print(
            temp
        )


        # ----------------------------------------------------
        # AI ASSESSMENT PIPELINE
        # ----------------------------------------------------

        Logs["Scan Info"].append(
            "Using extracted Metadata and the Harness Feedback "
            "Generating an Assessment Report and migration Plan"
        )

        print(
            "Using extracted Metadata and the Harness Feedback "
            "Generating an Assessment Report and migration Plan"
        )


        Agents_PipeLine(
            metadata
        )


        # ----------------------------------------------------
        # OUTPUT MESSAGE
        # ----------------------------------------------------

        Logs["Scan Info"].append(
            "Output is available at Show Logs embedded in the UI Screen"
        )

        print(
            "Output is available at Show Logs embedded in the UI Screen"
        )


        # ----------------------------------------------------
        # SCAN COMPLETED
        # ----------------------------------------------------

        Logs["Scan Info"].append(
            "Database scan completed successfully."
        )

        print(
            "Database scan completed successfully."
        )


        # Copy the log lists so the background job returns a stable snapshot
        # instead of exposing the mutable global log object to the frontend.
        log_snapshot = {
            "Token Info": list(Logs.get("Token Info", [])),
            "Scan Info": list(Logs.get("Scan Info", [])),
            "Progress Percentage": Logs.get("Progress Percentage", 0),
            "Harness Layer1": list(Logs.get("Harness Layer1", [])),
            "Harness Layer2": list(Logs.get("Harness Layer2", [])),
        }

        return Response(
            {
                "status": "success",

                "message": (
                    "Database scan completed successfully."
                ),

                "source": source,

                "destination": destination,

                "Logs": log_snapshot,
            }
        )


    # ========================================================
    # SCAN ERROR
    # ========================================================

    except Exception as e:

        print(
            f"[ERROR]: Database scan failed: {e}"
        )

        Logs["Scan Info"].append(
            str(e)
        )

        return Response(
            {
                "status": "error",
                "message": str(e),
            },
            status=400,
        )


# ============================================================
# SERVE OUTPUT FILE
# ============================================================

def serve_output_file(
    request,
    filename
):

    import os

    from django.http import (
        FileResponse,
        Http404,
    )

    from django.conf import settings

    if request.method == "HEAD":
        return HttpResponse(status=200)


    # ========================================================
    # OUTPUT FILE MAPPING
    # ========================================================

    mapping = {

        "Metadata_Report.docx":
            "Assesment Report.docx",

        "Migration_Assessment.docx":
            "AI_Migration_Plan.docx",
    }


    actual_filename = mapping.get(
        filename,
        filename
    )


    # ========================================================
    # FILE PATH
    # ========================================================

    file_path = os.path.join(

        settings.BASE_DIR,

        "AI_Agent_Pipeline",

        "output",

        actual_filename,
    )


    # ========================================================
    # CHECK FILE
    # ========================================================

    if os.path.exists(
        file_path
    ):

        return FileResponse(

            open(
                file_path,
                "rb"
            ),

            as_attachment=True,

            filename=filename,
        )


    raise Http404(
        "File not found"
    )