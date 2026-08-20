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

@api_view(["POST"])
def connect_database(request):

    global source
    reset_Logs()

    print("[INFO]: Connect request received")
    Logs["Scan Info"].append("Connect request received")

    source = request.data.get("source")
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

        # Remember these details so the frontend can pre-fill the connect
        # form next time this source is selected, instead of the user
        # having to retype everything.
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
    destination = request.data.get("destination")
    connection = request.data.get("connection", {})

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

        Logs["Scan Info"].append(f"\n{'='*30}\n{'='*30}\nMetaData Extracted\nRunning Harnnes Layer-1")
        print("MetaData Extracted\nRunning Harnness Layer-1")
        layer_result= layer1_Harness(metadata)
        temp = format_harness_report(layer_result)
        Logs["Scan Info"].append(temp)
        Logs["Harness Layer1"].append(temp)
        print(temp)



        Logs["Scan Info"].append(f"Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        print("Using extracted Metadata and the Harness Feedback Generating an Assessment Report and migration Plan")
        Agents_PipeLine(metadata)

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
                "Logs":Logs
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


def serve_output_file(request, filename):
    import os
    from django.http import FileResponse, Http404
    from django.conf import settings

    mapping = {
        "Metadata_Report.docx": "Assesment Report.docx",
        "Migration_Assessment.docx": "AI_Migration_Plan.docx",
    }
    actual_filename = mapping.get(filename, filename)
    
    file_path = os.path.join(settings.BASE_DIR, "AI_Agent_Pipeline", "output", actual_filename)
    
    if os.path.exists(file_path):
        return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)
    else:
        raise Http404("File not found")