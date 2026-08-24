import json
from pathlib import Path

import pymssql

from Metadata_Scanner.extractors.base_extractor import BaseExtractor


class SynapseExtractor(BaseExtractor):
    """
    Azure Synapse SQL pools (dedicated or serverless) speak T-SQL and
    expose INFORMATION_SCHEMA just like SQL Server, so this mirrors
    SQLServerExtractor almost exactly - same driver (pymssql), same
    queries. The only practical difference is the server address you
    connect to:
      - Dedicated SQL pool  : <workspace>.sql.azuresynapse.net
      - Serverless SQL pool : <workspace>-ondemand.sql.azuresynapse.net
    Both are entered in the normal "Server" field - no code change needed
    to switch between them.

    Install: pip install pymssql   (already installed - used by SQLServerExtractor)
    """

    DEFAULT_PORT = 1433

    def __init__(self, Creds):
        self.server = Creds.get_servername()
        self.database = Creds.get_database_name()
        self.username = Creds.get_username()
        self.password = Creds.get_password()
        self.port = Creds.get_port() or self.DEFAULT_PORT
        self.connection = None

    def connect(self):

        print("Server   :", repr(self.server))
        print("Database :", repr(self.database))
        print("User     :", repr(self.username))
        print("Port     :", repr(self.port))

        if not self.server:
            raise ValueError("Server name is empty.")
        if not self.database:
            raise ValueError("Database name is empty.")
        if not self.username:
            raise ValueError("Username is empty.")
        if self.password is None:
            raise ValueError("Password is None.")

        self.connection = pymssql.connect(
            server=self.server,
            port=str(self.port),
            database=self.database,
            user=self.username,
            password=self.password,
            timeout=15,
            login_timeout=15,
        )

    def close(self):
        if self.connection:
            self.connection.close()

    def extract(self, output_file="data/metadata.json"):

        self.connect()

        metadata = {
            "database": self.database,
            "schemas": []
        }

        cursor = self.connection.cursor(as_dict=True)

        cursor.execute("""
            SELECT
                TABLE_SCHEMA,
                TABLE_NAME,
                TABLE_TYPE
            FROM INFORMATION_SCHEMA.TABLES
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """)

        tables = cursor.fetchall()

        schema_map = {}

        for table in tables:

            schema_name = table["TABLE_SCHEMA"]
            table_name = table["TABLE_NAME"]

            if schema_name not in schema_map:
                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": [],
                    "procedures": []
                }

            table_object = {
                "name": table_name,
                "type": table["TABLE_TYPE"],
                "columns": []
            }

            column_cursor = self.connection.cursor(as_dict=True)
            column_cursor.execute("""
                SELECT
                    COLUMN_NAME,
                    DATA_TYPE,
                    CHARACTER_MAXIMUM_LENGTH,
                    NUMERIC_PRECISION,
                    NUMERIC_SCALE,
                    IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
            """, (schema_name, table_name))

            for column in column_cursor.fetchall():
                table_object["columns"].append({
                    "name": column["COLUMN_NAME"],
                    "datatype": column["DATA_TYPE"],
                    "max_length": column["CHARACTER_MAXIMUM_LENGTH"],
                    "precision": column["NUMERIC_PRECISION"],
                    "scale": column["NUMERIC_SCALE"],
                    "nullable": column["IS_NULLABLE"],
                })

            # Row count. NOTE: dedicated SQL pools distribute rows across
            # nodes - COUNT(*) is still correct, just potentially slower
            # on very large tables than on a single-node SQL Server.
            count_cursor = self.connection.cursor()
            try:
                count_cursor.execute(
                    f"SELECT COUNT(*) FROM [{schema_name}].[{table_name}]"
                )
                row_count_result = count_cursor.fetchone()
                table_object["row_count"] = row_count_result[0] if row_count_result else 0
            except Exception as e:
                print(f"[WARNING] Could not get row count for {schema_name}.{table_name}: {e}")
                table_object["row_count"] = None

            schema_map[schema_name]["tables"].append(table_object)

        # Stored procedures, kept per-schema alongside "tables" so Harness
        # Layer 1's PROCEDURES_DETECTED check (HarnessLayers/layer1/Layer.py)
        # has something to see on a real scan. Serverless SQL pools don't
        # support stored procedures, so this degrades to "none found" there
        # instead of failing the whole scan.
        try:
            proc_cursor = self.connection.cursor(as_dict=True)
            proc_cursor.execute("""
                SELECT
                    ROUTINE_SCHEMA,
                    ROUTINE_NAME
                FROM INFORMATION_SCHEMA.ROUTINES
                WHERE ROUTINE_TYPE = 'PROCEDURE'
                ORDER BY ROUTINE_SCHEMA, ROUTINE_NAME
            """)
            for proc in proc_cursor.fetchall():
                schema_name = proc["ROUTINE_SCHEMA"]
                if schema_name not in schema_map:
                    schema_map[schema_name] = {"name": schema_name, "tables": [], "procedures": []}
                schema_map[schema_name]["procedures"].append({"name": proc["ROUTINE_NAME"]})
        except Exception as e:
            print(f"[WARNING] Could not list stored procedures: {e}")

        metadata["schemas"] = list(schema_map.values())

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as fp:
            json.dump(metadata, fp, indent=4)

        self.close()
        return metadata
