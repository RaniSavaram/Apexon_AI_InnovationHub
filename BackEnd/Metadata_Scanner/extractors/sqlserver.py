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

        # ------------------------------------------------------------
        # Pick only ONE table per schema instead of scanning every table.
        # `tables` is already ORDER BY TABLE_SCHEMA, TABLE_NAME, so the
        # first row we see for a given schema is that schema's
        # alphabetically-first table.
        # ------------------------------------------------------------
        seen_schemas = set()
        sampled_tables = []

        for table in tables:
            schema_name = table["TABLE_SCHEMA"]
            if schema_name in seen_schemas:
                continue
            seen_schemas.add(schema_name)
            sampled_tables.append(table)

        tables = sampled_tables

        schema_map = {}

        for table in tables:

            schema_name = table["TABLE_SCHEMA"]

            if schema_name not in schema_map:

                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": []
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
                table_object["row_count"] = None
            schema_map[schema_name]["tables"].append(table_object)

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