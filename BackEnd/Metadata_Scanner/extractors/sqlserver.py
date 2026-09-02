import pymssql
import json
from pathlib import Path

from Metadata_Scanner.extractors.base_extractor import BaseExtractor
from config.Credentials import PrivateVariables

class SQLServerExtractor(BaseExtractor):

    def __init__(self,Creds):
        self.server = Creds.get_servername()
        self.database = Creds.get_database_name()
        self.username = Creds.get_username()
        self.password = Creds.get_password()
        self.connection = None


    def connect(self):

        print("Server   :", repr(self.server))
        print("Database :", repr(self.database))
        print("User     :", repr(self.username))

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
            database=self.database,
            user=self.username,
            password=self.password,
            timeout=600,
            as_dict=True
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

        cursor = self.connection.cursor()

        cursor.execute("""
            SELECT
                TABLE_SCHEMA,
                TABLE_NAME,
                TABLE_TYPE
            FROM INFORMATION_SCHEMA.TABLES
            ORDER BY TABLE_SCHEMA,
                     TABLE_NAME
        """)

        tables = cursor.fetchall()

        schema_map = {}

        for table in tables:

            schema_name = table["TABLE_SCHEMA"]

            if schema_name not in schema_map:

                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": [],
                    "procedures": []
                }

            table_object = {
                "name": table["TABLE_NAME"],
                "type": table["TABLE_TYPE"],
                "columns": []
            }

            column_cursor = self.connection.cursor()

            column_cursor.execute("""
                SELECT

                    COLUMN_NAME,
                    DATA_TYPE,
                    CHARACTER_MAXIMUM_LENGTH,
                    NUMERIC_PRECISION,
                    NUMERIC_SCALE,
                    IS_NULLABLE

                FROM INFORMATION_SCHEMA.COLUMNS

                WHERE TABLE_SCHEMA=%s
                AND TABLE_NAME=%s

                ORDER BY ORDINAL_POSITION
            """, (schema_name, table["TABLE_NAME"]))

            columns = column_cursor.fetchall()

            for column in columns:

                table_object["columns"].append({

                    "name": column["COLUMN_NAME"],
                    "datatype": column["DATA_TYPE"],
                    "max_length": column["CHARACTER_MAXIMUM_LENGTH"],
                    "precision": column["NUMERIC_PRECISION"],
                    "scale": column["NUMERIC_SCALE"],
                    "nullable": column["IS_NULLABLE"]

                })
            # Row count for this table
            count_cursor = self.connection.cursor()
            try:
                count_cursor.execute(
                    f"SELECT COUNT(*) AS row_count FROM [{schema_name}].[{table['TABLE_NAME']}]"
                )
                row_count_result = count_cursor.fetchone()
                table_object["row_count"] = row_count_result["row_count"] if row_count_result else 0
            except Exception as e:
                print(f"[WARNING] Could not get row count for {schema_name}.{table['TABLE_NAME']}: {e}")
                table_object["row_count"] = 0

            # Calculate or query table size in MB
            try:
                size_cursor = self.connection.cursor()
                size_cursor.execute(f"""
                    SELECT ROUND(((SUM(a.total_pages) * 8) / 1024.0), 2) AS size_mb
                    FROM sys.tables t
                    JOIN sys.schemas s ON t.schema_id = s.schema_id
                    JOIN sys.indexes i ON t.OBJECT_ID = i.object_id
                    JOIN sys.partitions p ON i.object_id = p.OBJECT_ID AND i.index_id = p.index_id
                    JOIN sys.allocation_units a ON p.partition_id = a.container_id
                    WHERE s.name = '{schema_name}' AND t.name = '{table['TABLE_NAME']}'
                    GROUP BY t.Name, s.Name
                """)
                size_row = size_cursor.fetchone()
                if size_row and size_row.get("size_mb") is not None and float(size_row["size_mb"]) > 0:
                    table_object["size_mb"] = float(size_row["size_mb"])
                else:
                    r_cnt = table_object.get("row_count") or 0
                    c_cnt = len(table_object.get("columns", []))
                    table_object["size_mb"] = round((64 + (r_cnt * max(c_cnt * 32, 64)) / 1024.0) / 1024.0, 2)
            except Exception:
                r_cnt = table_object.get("row_count") or 0
                c_cnt = len(table_object.get("columns", []))
                table_object["size_mb"] = round((64 + (r_cnt * max(c_cnt * 32, 64)) / 1024.0) / 1024.0, 2)

            schema_map[schema_name]["tables"].append(table_object)

        # Stored procedures, kept per-schema alongside "tables" so Harness
        # Layer 1's PROCEDURES_DETECTED check (HarnessLayers/layer1/Layer.py)
        # has something to see on a real scan.
        try:
            proc_cursor = self.connection.cursor()
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

        Path(output_file).parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with open(output_file, "w", encoding="utf-8") as fp:

            json.dump(
                metadata,
                fp,
                indent=4
            )

        self.close()

        return metadata