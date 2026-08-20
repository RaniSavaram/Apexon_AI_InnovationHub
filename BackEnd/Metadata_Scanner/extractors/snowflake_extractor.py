import json
from pathlib import Path

import snowflake.connector

from Metadata_Scanner.extractors.base_extractor import BaseExtractor


class SnowflakeExtractor(BaseExtractor):
    """
    Snowflake needs a few fields beyond the usual server/database/user/pass:
      - account   (extra["account"])   e.g. "xy12345.us-east-1" or "myorg-myaccount"
      - warehouse (extra["warehouse"]) the compute warehouse to run queries on
      - role      (extra["role"])      optional - defaults to the user's default role

    Field mapping:
      Creds.get_servername()     -> unused (Snowflake connects via account, not host)
      Creds.get_database_name()  -> Snowflake database
      Creds.get_username()       -> Snowflake username
      Creds.get_password()       -> Snowflake password
      Creds.get_extra("account")   -> required
      Creds.get_extra("warehouse") -> required
      Creds.get_extra("role")      -> optional

    Install: pip install snowflake-connector-python
    """

    def __init__(self, Creds):
        self.database = Creds.get_database_name()
        self.username = Creds.get_username()
        self.password = Creds.get_password()
        self.account = Creds.get_extra("account")
        self.warehouse = Creds.get_extra("warehouse")
        self.role = Creds.get_extra("role")
        self.connection = None

    def connect(self):

        print("Account   :", repr(self.account))
        print("Database  :", repr(self.database))
        print("Warehouse :", repr(self.warehouse))
        print("User      :", repr(self.username))
        print("Role      :", repr(self.role))

        if not self.account:
            raise ValueError("Snowflake account identifier is empty (extra['account']).")
        if not self.database:
            raise ValueError("Database name is empty.")
        if not self.username:
            raise ValueError("Username is empty.")
        if self.password is None:
            raise ValueError("Password is None.")
        if not self.warehouse:
            raise ValueError("Snowflake warehouse is empty (extra['warehouse']).")

        connect_kwargs = dict(
            account=self.account,
            user=self.username,
            password=self.password,
            database=self.database,
            warehouse=self.warehouse,
            login_timeout=15,
        )
        if self.role:
            connect_kwargs["role"] = self.role

        self.connection = snowflake.connector.connect(**connect_kwargs)

    def close(self):
        if self.connection:
            self.connection.close()

    def extract(self, output_file="data/metadata.json"):

        self.connect()

        metadata = {
            "database": self.database,
            "schemas": []
        }

        cursor = self.connection.cursor(snowflake.connector.DictCursor)

        # Snowflake's own maintained schema - never what a migration
        # assessment cares about.
        cursor.execute("""
            SELECT
                TABLE_SCHEMA,
                TABLE_NAME,
                TABLE_TYPE
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA != 'INFORMATION_SCHEMA'
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """)

        tables = cursor.fetchall()

        # Same one-table-per-schema sampling used by the other extractors.
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
            table_name = table["TABLE_NAME"]

            if schema_name not in schema_map:
                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": []
                }

            table_object = {
                "name": table_name,
                "type": table["TABLE_TYPE"],
                "columns": []
            }

            column_cursor = self.connection.cursor(snowflake.connector.DictCursor)
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

            # Row count. Snowflake keeps this cached in table metadata, so
            # this is effectively free - no full scan needed.
            count_cursor = self.connection.cursor()
            try:
                count_cursor.execute(
                    f'SELECT COUNT(*) FROM "{schema_name}"."{table_name}"'
                )
                row_count_result = count_cursor.fetchone()
                table_object["row_count"] = row_count_result[0] if row_count_result else 0
            except Exception as e:
                print(f"[WARNING] Could not get row count for {schema_name}.{table_name}: {e}")
                table_object["row_count"] = None

            schema_map[schema_name]["tables"].append(table_object)

        metadata["schemas"] = list(schema_map.values())

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as fp:
            json.dump(metadata, fp, indent=4)

        self.close()
        return metadata
