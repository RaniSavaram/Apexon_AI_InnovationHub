import json
from pathlib import Path

import databricks.sql as databricks_sql
from config import Credentials
from Metadata_Scanner.extractors.base_extractor import BaseExtractor


class DatabricksExtractor(BaseExtractor):
    """
    Databricks SQL warehouses authenticate with a personal access token
    (not username/password) and need an HTTP path identifying the specific
    warehouse/cluster to run queries against.

    This targets Unity Catalog's ANSI-standard information_schema, which is
    the modern, reliable way to enumerate tables/columns. If the workspace
    doesn't have Unity Catalog enabled, this will only see the legacy
    'hive_metastore' catalog's system schema, not user tables - Unity
    Catalog should be enabled for this to be useful.

    Field mapping:
      Creds.get_servername()  -> server_hostname, e.g. "dbc-xxxx.cloud.databricks.com"
      Creds.get_password()    -> the personal access token (used as the auth secret)
      Creds.get_database_name() -> the Unity Catalog catalog name, e.g. "main"
      Creds.get_extra("http_path") -> required, e.g. "/sql/1.0/warehouses/abc123def456"

    Install: pip install databricks-sql-connector
    """

    def __init__(self, Creds):
        self.server_hostname = Creds.get_servername()
        self.catalog = Creds.get_database_name()
        self.access_token = Creds.get_password()
        self.http_path = Creds.get_extra("http_path")
        self.connection = None

    def connect(self):

        print("Server Hostname :", repr(self.server_hostname))
        print("Catalog         :", repr(self.catalog))
        print("HTTP Path       :", repr(self.http_path))

        if not self.server_hostname:
            raise ValueError("Server hostname is empty.")
        if not self.catalog:
            raise ValueError("Catalog (database) name is empty.")
        if not self.access_token:
            raise ValueError("Access token (password field) is empty.")
        if not self.http_path:
            raise ValueError("HTTP path is empty (extra['http_path']).")

        self.connection = databricks_sql.connect(
            server_hostname=self.server_hostname,
            http_path=self.http_path,
            access_token=self.access_token,
        )

    def close(self):
        if self.connection:
            self.connection.close()

    def extract(self, output_file="data/metadata.json"):

        self.connect()

        metadata = {
            "database": self.catalog,
            "schemas": []
        }

        cursor = self.connection.cursor()

        cursor.execute(f"""
            SELECT
                table_schema,
                table_name,
                table_type
            FROM {self.catalog}.information_schema.tables
            WHERE table_schema != 'information_schema'
            ORDER BY table_schema, table_name
        """)

        columns_desc = [d[0] for d in cursor.description]
        tables = [dict(zip(columns_desc, row)) for row in cursor.fetchall()]

        # Same one-table-per-schema sampling used by the other extractors.
        seen_schemas = set()
        sampled_tables = []
        for table in tables:
            schema_name = table["table_schema"]
            if schema_name in seen_schemas:
                continue
            seen_schemas.add(schema_name)
            sampled_tables.append(table)
        tables = sampled_tables

        schema_map = {}

        for table in tables:

            schema_name = table["table_schema"]
            table_name = table["table_name"]

            if schema_name not in schema_map:
                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": []
                }

            table_object = {
                "name": table_name,
                "type": table["table_type"],
                "columns": []
            }

            column_cursor = self.connection.cursor()
            column_cursor.execute(f"""
                SELECT
                    column_name,
                    data_type,
                    character_maximum_length,
                    numeric_precision,
                    numeric_scale,
                    is_nullable
                FROM {self.catalog}.information_schema.columns
                WHERE table_schema = %(schema_name)s AND table_name = %(table_name)s
                ORDER BY ordinal_position
            """, {"schema_name": schema_name, "table_name": table_name})

            col_desc = [d[0] for d in column_cursor.description]
            for row in column_cursor.fetchall():
                column = dict(zip(col_desc, row))
                table_object["columns"].append({
                    "name": column["column_name"],
                    "datatype": column["data_type"],
                    "max_length": column["character_maximum_length"],
                    "precision": column["numeric_precision"],
                    "scale": column["numeric_scale"],
                    "nullable": column["is_nullable"],
                })

            # Row count for this table.
            count_cursor = self.connection.cursor()
            try:
                count_cursor.execute(
                    f"SELECT COUNT(*) FROM `{self.catalog}`.`{schema_name}`.`{table_name}`"
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

if __name__ == "__main__":
    cred = Credentials()
    cred.set_server_hostname("dbc-a58bcebf-ff15.cloud.databricks.com")
    cred.set_database_name("e17b0dca-b70a-4c2d-b0b7-83859e1bfcb0")
    cred.set_password("DATABRICKS_TOKEN_REMOVED")
    cred.set_extra("/sql/1.0/warehouses/570872a58c83ef23")
    
    obj = DatabricksExtractor(cred)
    obj.connect()
    obj.extract()
    obj.close()