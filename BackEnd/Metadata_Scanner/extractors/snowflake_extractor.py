import json
import re
from pathlib import Path

import snowflake.connector

from Metadata_Scanner.extractors.base_extractor import BaseExtractor
from Metadata_Scanner.extractors.declared_keys import attach_declared_keys


class SnowflakeExtractor(BaseExtractor):
    """
    Snowflake needs a few fields beyond the usual server/database/user/pass:
      - account   (extra["account"])   e.g. "xy12345.us-east-1" or "myorg-myaccount"
      - warehouse (extra["warehouse"]) the compute warehouse to run queries on
      - role      (extra["role"])      optional - defaults to the user's default role
      - token     (extra["token"])     optional - a Snowflake Programmatic Access
                                        Token (PAT); when set, connects via
                                        authenticator="PROGRAMMATIC_ACCESS_TOKEN"
                                        instead of a password. This is NOT the
                                        generic authenticator="oauth" flow - a PAT
                                        is validated server-side differently from
                                        a real OAuth access token, so it must be
                                        sent with the PAT-specific authenticator.
                                        Username is still required: Snowflake
                                        validates the PAT against the login name
                                        it was issued for, and rejects the token
                                        as invalid if no user is sent.

    Field mapping:
      Creds.get_servername()     -> unused (Snowflake connects via account, not host)
      Creds.get_database_name()  -> Snowflake database
      Creds.get_username()       -> Snowflake username (required in both modes)
      Creds.get_password()       -> Snowflake password (ignored when extra["token"] is set)
      Creds.get_extra("account")   -> required
      Creds.get_extra("warehouse") -> required
      Creds.get_extra("role")      -> optional
      Creds.get_extra("token")     -> optional - PAT; enables token auth instead of password

    Install: pip install snowflake-connector-python
    """

    def __init__(self, Creds):
        self.database = Creds.get_database_name()
        self.username = Creds.get_username()
        self.password = Creds.get_password()
        self.account = Creds.get_extra("account")
        self.warehouse = Creds.get_extra("warehouse")
        self.role = Creds.get_extra("role")
        self.token = Creds.get_extra("token")
        self.connection = None

    def connect(self):

        print("Account   :", repr(self.account))
        print("Database  :", repr(self.database))
        print("Warehouse :", repr(self.warehouse))
        print("User      :", repr(self.username))
        print("Role      :", repr(self.role))
        print("Auth mode :", "token" if self.token else "password")

        if not self.account:
            raise ValueError("Snowflake account identifier is empty (extra['account']).")
        if not self.database:
            raise ValueError("Database name is empty.")
        if not self.warehouse:
            raise ValueError("Snowflake warehouse is empty (extra['warehouse']).")
        if not self.username:
            raise ValueError("Username is empty.")

        if self.token:
            # Snowflake validates a PAT against the login name it was issued
            # for - omitting `user` here makes the server reject an
            # otherwise-valid token as invalid.
            connect_kwargs = dict(
                account=self.account,
                user=self.username,
                authenticator="PROGRAMMATIC_ACCESS_TOKEN",
                token=self.token,
                database=self.database,
                warehouse=self.warehouse,
                login_timeout=15,
            )
        else:
            if self.password is None:
                raise ValueError("Password is None.")
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

        # View SQL text, so views carry their definition the same way the
        # Databricks extractor's _fetch_view_definitions() does -
        # metadataProcessor.py picks up table["definition"] for type VIEW and
        # it ends up as the commented original body of the placeholder view
        # created in the Fabric Warehouse. VIEW_DEFINITION is NULL for views
        # the current role doesn't own.
        view_definitions = {}
        try:
            view_cursor = self.connection.cursor(snowflake.connector.DictCursor)
            view_cursor.execute("""
                SELECT
                    TABLE_SCHEMA,
                    TABLE_NAME,
                    VIEW_DEFINITION
                FROM INFORMATION_SCHEMA.VIEWS
                WHERE TABLE_SCHEMA != 'INFORMATION_SCHEMA'
            """)
            for view in view_cursor.fetchall():
                view_definitions[(view["TABLE_SCHEMA"], view["TABLE_NAME"])] = view["VIEW_DEFINITION"]
        except Exception as e:
            print(f"[WARNING] Could not read view definitions: {e}")

        # Materialized views come back from INFORMATION_SCHEMA.TABLES with
        # TABLE_TYPE 'MATERIALIZED VIEW' but are NOT listed in
        # INFORMATION_SCHEMA.VIEWS - their SQL is only exposed by SHOW
        # MATERIALIZED VIEWS' "text" column. Keeping TABLE_TYPE as-is lets
        # metadataProcessor.py route them to views (not Delta tables) while
        # still knowing they were materialized in Snowflake.
        try:
            mv_cursor = self.connection.cursor(snowflake.connector.DictCursor)
            mv_cursor.execute(f'SHOW MATERIALIZED VIEWS IN DATABASE "{self.database}"')
            for mv in mv_cursor.fetchall():
                view_definitions[(mv["schema_name"], mv["name"])] = mv.get("text")
        except Exception as e:
            print(f"[WARNING] Could not read materialized view definitions: {e}")

        schema_map = {}

        for table in tables:

            schema_name = table["TABLE_SCHEMA"]
            table_name = table["TABLE_NAME"]

            if schema_name not in schema_map:
                schema_map[schema_name] = {
                    "name": schema_name,
                    "tables": [],
                    "procedures": [],
                    "functions": []
                }

            table_object = {
                "name": table_name,
                "type": table["TABLE_TYPE"],
                "columns": []
            }
            if table["TABLE_TYPE"] in ("VIEW", "MATERIALIZED VIEW"):
                table_object["definition"] = view_definitions.get((schema_name, table_name))

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

        # Stored procedures, kept per-schema alongside "tables" so Harness
        # Layer 1's PROCEDURES_DETECTED check (HarnessLayers/layer1/Layer.py)
        # has something to see on a real scan.
        try:
            proc_cursor = self.connection.cursor(snowflake.connector.DictCursor)
            proc_cursor.execute("""
                SELECT
                    PROCEDURE_SCHEMA,
                    PROCEDURE_NAME,
                    ARGUMENT_SIGNATURE,
                    DATA_TYPE,
                    PROCEDURE_LANGUAGE,
                    PROCEDURE_DEFINITION
                FROM INFORMATION_SCHEMA.PROCEDURES
                WHERE PROCEDURE_SCHEMA != 'INFORMATION_SCHEMA'
                ORDER BY PROCEDURE_SCHEMA, PROCEDURE_NAME
            """)
            for proc in proc_cursor.fetchall():
                schema_name = proc["PROCEDURE_SCHEMA"]
                if schema_name not in schema_map:
                    schema_map[schema_name] = {"name": schema_name, "tables": [], "procedures": [], "functions": []}
                # PROCEDURE_DEFINITION is only the body between the $$
                # delimiters - rebuild the full CREATE statement so
                # "definition" reads the same way a view's VIEW_DEFINITION
                # does (a complete, standalone statement). NULL when the
                # current role doesn't own the procedure.
                body = proc["PROCEDURE_DEFINITION"]
                definition = None
                if body:
                    definition = (
                        f"CREATE OR REPLACE PROCEDURE {proc['PROCEDURE_NAME']}{proc['ARGUMENT_SIGNATURE'] or '()'}\n"
                        f"RETURNS {proc['DATA_TYPE']}\n"
                        f"LANGUAGE {proc['PROCEDURE_LANGUAGE'] or 'SQL'}\n"
                        f"AS\n$$\n{body.strip()}\n$$;"
                    )
                schema_map[schema_name]["procedures"].append({
                    "name": proc["PROCEDURE_NAME"],
                    "arguments": proc["ARGUMENT_SIGNATURE"],
                    "return_type": proc["DATA_TYPE"],
                    "language": proc["PROCEDURE_LANGUAGE"],
                    "definition": definition,
                })
        except Exception as e:
            print(f"[WARNING] Could not list stored procedures: {e}")

        # User-defined functions/UDTFs, kept per-schema alongside "procedures"
        # in the same "functions" shape the Databricks extractor already
        # produces ({"name": ..., "return_type": ...} - see
        # databricks_client.py's _fetch_functions()) so metadataProcessor.py,
        # the AI agent pipeline, and plan_to_json.py pick these up unchanged;
        # they already read schema["functions"] generically. DATA_TYPE comes
        # back as 'TABLE' for a UDTF instead of a scalar SQL type - callers
        # downstream can use that to tell the two apart. definition is
        # carried through to the Fabric Warehouse placeholder function as a
        # comment, the same way procedures' definitions are.
        try:
            func_cursor = self.connection.cursor(snowflake.connector.DictCursor)
            func_cursor.execute("""
                SELECT
                    FUNCTION_SCHEMA,
                    FUNCTION_NAME,
                    DATA_TYPE,
                    ARGUMENT_SIGNATURE,
                    FUNCTION_LANGUAGE,
                    IS_EXTERNAL,
                    FUNCTION_DEFINITION
                FROM INFORMATION_SCHEMA.FUNCTIONS
                WHERE FUNCTION_SCHEMA != 'INFORMATION_SCHEMA'
                ORDER BY FUNCTION_SCHEMA, FUNCTION_NAME
            """)
            for func in func_cursor.fetchall():
                schema_name = func["FUNCTION_SCHEMA"]
                if schema_name not in schema_map:
                    schema_map[schema_name] = {"name": schema_name, "tables": [], "procedures": [], "functions": []}
                # FUNCTION_DEFINITION is only the body - rebuild the full
                # CREATE statement the same way procedures do above. NULL
                # when the current role doesn't own the function.
                body = func["FUNCTION_DEFINITION"]
                definition = None
                if body:
                    definition = (
                        f"CREATE OR REPLACE FUNCTION {func['FUNCTION_NAME']}{func['ARGUMENT_SIGNATURE'] or '()'}\n"
                        f"RETURNS {func['DATA_TYPE']}\n"
                        f"LANGUAGE {func['FUNCTION_LANGUAGE'] or 'SQL'}\n"
                        f"AS\n$$\n{body.strip()}\n$$;"
                    )
                schema_map[schema_name]["functions"].append({
                    "name": func["FUNCTION_NAME"],
                    "return_type": func["DATA_TYPE"],
                    "arguments": func["ARGUMENT_SIGNATURE"],
                    "language": func["FUNCTION_LANGUAGE"],
                    "is_external": func["IS_EXTERNAL"],
                    "definition": definition,
                })
        except Exception as e:
            print(f"[WARNING] Could not list user-defined functions: {e}")

        # Declared primary/foreign keys (see declared_keys.py). Snowflake
        # doesn't enforce them, but they're commonly declared for modelling
        # and BI tools. An unquoted name resolves case-insensitively the way
        # the connection itself did; anything else has to be quoted.
        try:
            db = self.database if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", self.database) else f'"{self.database}"'
            key_cursor = self.connection.cursor(snowflake.connector.DictCursor)
            key_cursor.execute(f"SHOW PRIMARY KEYS IN DATABASE {db}")
            pk_rows = [
                (r["schema_name"], r["table_name"], r["column_name"], r["key_sequence"])
                for r in key_cursor.fetchall()
            ]
            key_cursor.execute(f"SHOW IMPORTED KEYS IN DATABASE {db}")
            fk_rows = [
                (r["fk_name"], r["fk_schema_name"], r["fk_table_name"], r["fk_column_name"],
                 r["pk_schema_name"], r["pk_table_name"], r["pk_column_name"], r["key_sequence"])
                for r in key_cursor.fetchall()
            ]
            pk_tables, fk_count = attach_declared_keys(schema_map, pk_rows, fk_rows)
            print(f"[INFO] Declared keys: {pk_tables} table(s) with a primary key, {fk_count} foreign key(s).")
        except Exception as e:
            print(f"[WARNING] Could not read declared keys: {e}")

        metadata["schemas"] = list(schema_map.values())

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as fp:
            json.dump(metadata, fp, indent=4)

        self.close()
        return metadata
