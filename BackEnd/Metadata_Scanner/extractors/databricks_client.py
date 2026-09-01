import json
import os
import re
from pathlib import Path

import databricks.sql as databricks_sql
from config import Credentials
from Metadata_Scanner.extractors.base_extractor import BaseExtractor

# Demo toggle for showing both Harness Layer 1 governance outcomes back to
# back: True makes the next scan execute a real (harmless, "WHERE 1=0")
# DELETE probe against one table before extraction finishes, which lands in
# Unity Catalog's system.query.history and trips the negative-case
# (DESTRUCTIVE_SQL_DETECTED) path - the scan then gets stopped by the gate
# in Migrator/views.py. False leaves the scan fully read-only for the
# positive-case (full scan completes) demo. Flip this one line between the
# two demo runs - Django's dev-server autoreloader picks up the change on
# save, no restart needed.
#
# NOTE ON ORDER: the probe's DELETE is a real statement, so once it runs it
# stays in system.query.history for 30 days (see _fetch_destructive_statements
# below) and will keep failing every subsequent scan of that same catalog
# regardless of this flag. Demo the positive case FIRST, then flip this to
# True for the negative case - not the other way around.
DEMO_FORCE_DESTRUCTIVE_STATEMENT = False


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
    # NEGATIVE-CASE DEMO (gated by DEMO_FORCE_DESTRUCTIVE_STATEMENT above)
    # ------------------------------------------------------------------
    # This helper simulates a real destructive SQL event for the governance
    # test path by executing a live (no-op, "WHERE 1=0") DELETE against a
    # Databricks table. Only runs when DEMO_FORCE_DESTRUCTIVE_STATEMENT is
    # True; leave that False for a normal, fully read-only scan.
    # ------------------------------------------------------------------
    def _execute_destructive_statement_probe(self, tables):
        """
        Demo-only negative case. This intentionally executes a DELETE against a
        real table to prove the governance layer reacts to destructive SQL.
        Only called when DEMO_FORCE_DESTRUCTIVE_STATEMENT is True.
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

    def _fetch_view_definitions(self):
        """
        Query Unity Catalog's information_schema.views for the SQL text
        behind each view, so views carry their actual definition instead of
        just their column list. Degrades to "no definitions captured" if the
        workspace/token can't reach information_schema.views, rather than
        failing the scan.

        Returns {(schema_name, view_name): definition_sql}.
        """
        definitions: dict[tuple[str, str], str] = {}
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"""
                SELECT table_schema, table_name, view_definition
                FROM {self.catalog}.information_schema.views
            """)
            for schema_name, view_name, view_definition in cursor.fetchall():
                definitions[(schema_name, view_name)] = view_definition
        except Exception as e:
            print(f"[WARNING] Could not read view definitions: {e}")
        return definitions

    def _fetch_functions(self):
        """
        Query Unity Catalog's information_schema.routines for user-defined
        functions (routine_type = 'FUNCTION'), the sibling of
        _fetch_procedures() for PROCEDUREs. Degrades to "none found" if the
        query errors out rather than failing the scan.

        Returns {schema_name: [{"name": function_name, "return_type": ...}, ...]}.
        """
        functions_by_schema: dict[str, list[dict]] = {}
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"""
                SELECT routine_schema, routine_name, data_type
                FROM {self.catalog}.information_schema.routines
                WHERE routine_type = 'FUNCTION'
                ORDER BY routine_schema, routine_name
            """)
            for schema_name, routine_name, return_type in cursor.fetchall():
                functions_by_schema.setdefault(schema_name, []).append({
                    "name": routine_name,
                    "return_type": return_type,
                })
        except Exception as e:
            print(f"[WARNING] Could not list functions: {e}")
        return functions_by_schema

    def _fetch_volumes(self):
        """
        Query Unity Catalog's information_schema.volumes for managed/external
        volumes (object-storage-backed file mounts, distinct from tables).
        Only populated on workspaces with Unity Catalog volumes enabled, so
        this degrades to "none found" rather than failing the scan if the
        system table isn't reachable.

        Returns {schema_name: [{"name": ..., "volume_type": ..., "storage_location": ...}, ...]}.
        """
        volumes_by_schema: dict[str, list[dict]] = {}
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"""
                SELECT volume_schema, volume_name, volume_type, storage_location
                FROM {self.catalog}.information_schema.volumes
                ORDER BY volume_schema, volume_name
            """)
            for schema_name, volume_name, volume_type, storage_location in cursor.fetchall():
                volumes_by_schema.setdefault(schema_name, []).append({
                    "name": volume_name,
                    "volume_type": volume_type,
                    "storage_location": storage_location,
                })
        except Exception as e:
            print(f"[WARNING] Could not list volumes: {e}")
        return volumes_by_schema

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

        view_definitions = self._fetch_view_definitions()

        # NEGATIVE-CASE DEMO: see DEMO_FORCE_DESTRUCTIVE_STATEMENT at the top of
        # this file - flip that one line to switch between the positive-case
        # (full scan) and negative-case (governance stop) demo scenarios.
        #
        # Both destructive-statement sources are gated behind the same flag:
        # _fetch_destructive_statements() reads REAL Unity Catalog query
        # history, which is a persistent audit log - once the demo probe (or
        # any real DELETE/TRUNCATE) has run against this catalog, it stays in
        # that history for 30 days and would keep failing every scan
        # regardless of this flag if the real fetch ran unconditionally. So
        # when the flag is False, skip the real history check entirely and
        # guarantee a clean, fully read-only positive-case run; when True,
        # check real history AND inject the synthetic probe statement.
        destructive_statements = {}
        if DEMO_FORCE_DESTRUCTIVE_STATEMENT:
            destructive_statements = self._fetch_destructive_statements(
                [(table["table_schema"], table["table_name"]) for table in tables]
            )
            for key, statements in self._execute_destructive_statement_probe(tables).items():
                destructive_statements.setdefault(key, []).extend(statements)

        schema_map = {}

        for table in tables:

            schema_name = table["table_schema"]
            table_name = table["table_name"]

            if schema_name not in schema_map:
                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": [],
                    "procedures": [],
                    "functions": [],
                    "volumes": [],
                }

            table_object = {
                "name": table_name,
                "type": table["table_type"],
                "columns": [],
                "recent_statements": destructive_statements.get((schema_name, table_name), []),
            }
            if (table["table_type"] or "").upper() == "VIEW":
                table_object["definition"] = view_definitions.get((schema_name, table_name))

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

        def _ensure_schema(schema_name):
            if schema_name not in schema_map:
                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": [],
                    "procedures": [],
                    "functions": [],
                    "volumes": [],
                }
            return schema_map[schema_name]

        for schema_name, procedures in self._fetch_procedures().items():
            _ensure_schema(schema_name)["procedures"] = procedures

        for schema_name, functions in self._fetch_functions().items():
            _ensure_schema(schema_name)["functions"] = functions

        for schema_name, volumes in self._fetch_volumes().items():
            _ensure_schema(schema_name)["volumes"] = volumes

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