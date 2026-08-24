import json
import os
import re
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

    def _fetch_destructive_statements(self, table_keys):
        """
        Query Unity Catalog's system.query.history for recent DELETE/TRUNCATE
        statements and match them back to the scanned tables, so Harness
        Layer 1's GovernanceValidator.DESTRUCTIVE_SQL_DETECTED rule
        (HarnessLayers/layer1/Layer.py) - which reads each table's
        "recent_statements" - actually has something to see on a real scan
        instead of that field always coming back empty.

        Returns {(schema_name, table_name): [statement_text, ...]}. Returns
        {} if system.query.history isn't reachable (Unity Catalog system
        schema not enabled, or the token lacks SELECT on it) so a missing
        audit log degrades to "nothing flagged" rather than failing the scan.
        """
        statements_by_table: dict[tuple[str, str], list[str]] = {}
        try:
            cursor = self.connection.cursor()
            # statement_type IN (...) is the fast path, but Databricks' docs
            # don't publish a guaranteed enum (they only give examples like
            # ALTER/COPY/INSERT) - so also match on the statement text itself
            # as a fallback in case TRUNCATE isn't classified the way we
            # expect. Belt-and-suspenders beats a silent false negative on a
            # destructive-statement check.
            cursor.execute("""
                SELECT statement_text
                FROM system.query.history
                WHERE (
                    statement_type IN ('DELETE', 'TRUNCATE')
                    OR upper(trim(statement_text)) LIKE 'DELETE %'
                    OR upper(trim(statement_text)) LIKE 'TRUNCATE %'
                )
                AND start_time >= current_timestamp() - INTERVAL 30 DAYS
                ORDER BY start_time DESC
                LIMIT 1000
            """)
            rows = cursor.fetchall()
        except Exception as e:
            print(f"[WARNING] Could not read system.query.history for destructive-statement check: {e}")
            return statements_by_table

        for row in rows:
            statement_text = row[0]
            if not statement_text:
                continue
            for schema_name, table_name in table_keys:
                table_pattern = re.compile(
                    rf"(?:^|[`.\s]){re.escape(table_name)}(?:[`\s;.]|$)", re.IGNORECASE
                )
                if not table_pattern.search(statement_text):
                    continue
                # Require the schema to also appear in the statement so a
                # bare table name shared across schemas doesn't cross-match.
                if schema_name and not re.search(rf"\b{re.escape(schema_name)}\b", statement_text, re.IGNORECASE):
                    continue
                statements_by_table.setdefault((schema_name, table_name), []).append(statement_text)

        return statements_by_table

    # ------------------------------------------------------------------
    # NEGATIVE-CASE DEMO (INTENTIONALLY DISABLED)
    # ------------------------------------------------------------------
    # This helper was added to simulate a real destructive SQL event for the
    # governance test path. It executes a live DELETE against a Databricks table,
    # which is not appropriate for a normal metadata scan. Keeping it disabled
    # ensures the Scanner completes a full metadata extraction without tripping
    # the negative-case rule or modifying data.
    #
    # To re-enable the demo for testing, uncomment the method call in
    # extract() and set DEMO_FORCE_DESTRUCTIVE_STATEMENT=true.
    # ------------------------------------------------------------------
    def _execute_destructive_statement_probe(self, tables):
        """
        Demo-only negative case. This intentionally executes a DELETE against a
        real table to prove the governance layer reacts to destructive SQL.
        It is disabled by default so the normal scan remains read-only and can
        complete successfully.
        """
        target = next(
            (t for t in tables if (t.get("table_type") or "").upper() != "VIEW"),
            None,
        )
        if not target:
            return {}

        schema_name = target["table_schema"]
        table_name = target["table_name"]
        statement = f"DELETE FROM {self.catalog}.{schema_name}.{table_name} WHERE 1=0"

        try:
            probe_cursor = self.connection.cursor()
            probe_cursor.execute(statement)
        except Exception as e:
            print(f"[WARNING] Destructive-statement probe DELETE failed (likely missing MODIFY permission): {e}")
            return {}

        return {(schema_name, table_name): [statement]}

    def _fetch_procedures(self):
        """
        Query Unity Catalog's information_schema.routines for stored
        procedures (routine_type = 'PROCEDURE'), so Harness Layer 1's
        PROCEDURES_DETECTED check (HarnessLayers/layer1/Layer.py) has
        something to see on a real scan. SQL procedures are a newer Unity
        Catalog feature and not every workspace/runtime supports them, so
        this degrades to "none found" rather than failing the scan if the
        query errors out.

        Returns {schema_name: [{"name": procedure_name}, ...]}.
        """
        procedures_by_schema: dict[str, list[dict]] = {}
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"""
                SELECT routine_schema, routine_name
                FROM {self.catalog}.information_schema.routines
                WHERE routine_type = 'PROCEDURE'
                ORDER BY routine_schema, routine_name
            """)
            for schema_name, routine_name in cursor.fetchall():
                procedures_by_schema.setdefault(schema_name, []).append({"name": routine_name})
        except Exception as e:
            print(f"[WARNING] Could not list stored procedures: {e}")
        return procedures_by_schema

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

        destructive_statements = self._fetch_destructive_statements(
            [(table["table_schema"], table["table_name"]) for table in tables]
        )

        # NEGATIVE-CASE DEMO: disabled by default so the Databricks scan runs as a
        # full metadata extraction without executing a live DELETE against any table.
        # Leave this block commented out unless you intentionally want to trigger the
        # destructive SQL governance path during a demo.
        # if os.environ.get("DEMO_FORCE_DESTRUCTIVE_STATEMENT", "true").strip().lower() != "false":
        #     for key, statements in self._execute_destructive_statement_probe(tables).items():
        #         destructive_statements.setdefault(key, []).extend(statements)

        schema_map = {}

        for table in tables:

            schema_name = table["table_schema"]
            table_name = table["table_name"]

            if schema_name not in schema_map:
                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": [],
                    "procedures": []
                }

            table_object = {
                "name": table_name,
                "type": table["table_type"],
                "columns": [],
                "recent_statements": destructive_statements.get((schema_name, table_name), []),
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

        for schema_name, procedures in self._fetch_procedures().items():
            if schema_name not in schema_map:
                schema_map[schema_name] = {"name": schema_name, "tables": [], "procedures": []}
            schema_map[schema_name]["procedures"] = procedures

        metadata["schemas"] = list(schema_map.values())

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as fp:
            json.dump(metadata, fp, indent=4)

        self.close()
        return metadata

if __name__ == "__main__":
    cred = Credentials()
    cred.set_server_hostname(os.environ["DATABRICKS_SERVER_HOSTNAME"])
    cred.set_database_name(os.environ["DATABRICKS_CATALOG"])
    cred.set_password(os.environ["DATABRICKS_ACCESS_TOKEN"])
    cred.set_extra("http_path", os.environ["DATABRICKS_HTTP_PATH"])
    
    obj = DatabricksExtractor(cred)
    obj.connect()
    obj.extract()
    obj.close()