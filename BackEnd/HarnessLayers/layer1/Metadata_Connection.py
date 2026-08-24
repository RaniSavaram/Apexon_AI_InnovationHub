# metadata_database_config.py
import time
from typing import Any, Optional

import pymssql
import pyodbc



def build_db_config() -> dict[str, Any]:
    """
    Builds the connection config from the credentials submitted at runtime
    via the /connect_database Django endpoint (Migrator.views.Creds), rather
    than a hardcoded dict.
    """
    from Migrator.views import Creds
    return {
        "server": Creds.get_servername(),
        "database": Creds.get_database_name(),
        "username": Creds.get_username(),
        "password": Creds.get_password(),
        "timeout": 10,                 # query timeout in seconds
        "login_timeout": 10,           # connection timeout in seconds
    }


def check_database_connection(config: Optional[dict] = None) -> dict[str, Any]:
    """
    Attempts a real DB connection via pymssql and returns a dict matching
    what ConnectionValidator.validate() expects.
    """
    config = config or build_db_config()
    result = {
        "connected": False,
        "authenticated": False,
        "credentials_verified": False,
        "timed_out": False,
        "permissions": [],
    }

    start = time.time()
    conn = None
    try:
        conn = pymssql.connect(
            server=config["server"],
            user=config["username"],
            password=config["password"],
            database=config["database"],
            timeout=config["timeout"],
            login_timeout=config["login_timeout"],
        )

        # Sanity check the connection actually works end-to-end
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()

        result["connected"] = True
        result["authenticated"] = True
        result["credentials_verified"] = True

        # Fetch actual granted permissions
        result["permissions"] = fetch_permissions(conn)

    except pymssql.OperationalError as e:
        # Covers login failures, network issues, etc.
        if time.time() - start >= config["timeout"]:
            result["timed_out"] = True
        print(f"Operational error connecting to DB: {e}")

    except pymssql.InterfaceError as e:
        # Covers connection-level failures (e.g. server unreachable)
        print(f"Interface error connecting to DB: {e}")

    except Exception as e:
        # Catch-all — log the real exception, don't swallow silently
        print(f"Unexpected DB error: {e}")

    finally:
        if conn is not None:
            conn.close()

    return result


def fetch_permissions(conn) -> list[str]:
    """
    Returns the current connection's EFFECTIVE database permissions via
    fn_my_permissions, which includes permissions inherited through role
    membership (e.g. db_datareader). Querying sys.database_permissions
    directly only shows grants made to the principal itself and misses
    role-inherited ones, which is how most read access is actually granted.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT permission_name FROM fn_my_permissions(NULL, 'DATABASE')")
    return [row[0] for row in cursor.fetchall()]
import pyodbc
import logging

logger = logging.getLogger(__name__)


def scan_info(conn, expected_tables: list[str]) -> dict:
    """
    Scans the given list of expected tables against the live database,
    reporting which were actually found/accessible.

    Args:
        conn: an open pyodbc connection
        expected_tables: list of "schema.table" strings, e.g. ["dbo.Customers", "dbo.Orders"]

    Returns:
        dict matching the ScanValidator contract:
        {status, expected_scope, actual_scope, interrupted, partial_extraction}
    """
    actual_scope = []
    interrupted = False
    errors = []  # per-table failures, useful for debugging even if not in the final schema

    cursor = conn.cursor()

    for full_name in expected_tables:
        try:
            schema, name = full_name.split(".", 1)
        except ValueError:
            logger.warning(f"Skipping malformed table reference: {full_name}")
            errors.append({"table": full_name, "reason": "malformed_name"})
            continue

        try:
            # Check the table exists
            cursor.execute("""
                SELECT 1
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            """, schema, name)

            if not cursor.fetchone():
                logger.warning(f"Table not found: {full_name}")
                errors.append({"table": full_name, "reason": "not_found"})
                continue

            # Confirm we actually have read access (SELECT permission),
            # not just visibility in INFORMATION_SCHEMA
            cursor.execute(f"""
                SELECT TOP 1 1 FROM [{schema}].[{name}]
            """)
            cursor.fetchall()

            actual_scope.append(full_name)

        except pyodbc.Error as e:
            # Permission denied, table locked, etc. — don't abort the whole scan
            logger.error(f"Failed to access {full_name}: {e}")
            errors.append({"table": full_name, "reason": str(e)})
            continue

    # A hard failure (connection dropped mid-scan, etc.) is different from
    # individual tables being skipped — check the connection is still alive
    try:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    except pyodbc.Error as e:
        logger.error(f"Connection lost during scan: {e}")
        interrupted = True

    partial_extraction = set(actual_scope) != set(expected_tables)

    if interrupted:
        status = "FAILED"
    elif partial_extraction:
        status = "PARTIAL"
    else:
        status = "COMPLETED"

    return {
        "status": status,
        "expected_scope": expected_tables,
        "actual_scope": actual_scope,
        "interrupted": interrupted,
        "partial_extraction": partial_extraction,
        "errors": errors,  # extra field — drop this if ScanValidator does strict schema validation
    }


_PREFERRED_DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",  # legacy driver bundled with Windows — always present as a last resort
)


def _pick_installed_driver(preferred: Optional[str] = None) -> str:
    installed = set(pyodbc.drivers())
    if preferred:
        if preferred not in installed:
            raise RuntimeError(
                f"ODBC driver '{preferred}' is not installed. Installed drivers: "
                f"{sorted(installed) or '(none)'}"
            )
        return preferred

    for candidate in _PREFERRED_DRIVERS:
        if candidate in installed:
            return candidate

    raise RuntimeError(
        "No usable SQL Server ODBC driver found. Install the 'ODBC Driver 18 for "
        "SQL Server' from Microsoft, or any driver from this machine's installed "
        f"list: {sorted(installed) or '(none)'}"
    )


def open_pyodbc_connection(config: Optional[dict] = None):
    """
    Opens a live pyodbc connection for use with scan_info()/metadata(),
    which query INFORMATION_SCHEMA using pyodbc's '?' parameter style.
    check_database_connection() uses pymssql and closes its own connection,
    so it can't supply the connection object these two need.

    Driver is auto-detected from what's actually installed (config["driver"]
    can force a specific one) since "ODBC Driver 17/18 for SQL Server" isn't
    installed by default on Windows — only the legacy "SQL Server" driver is.
    """
    config = config or build_db_config()
    driver = _pick_installed_driver(config.get("driver"))
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={config['server']};"
        f"DATABASE={config['database']};"
        f"UID={config['username']};"
        f"PWD={config['password']};"
        f"Timeout={config['login_timeout']};"
    )
    return pyodbc.connect(conn_str)


def metadata(conn, tables: list[str]) -> dict[str, Any]:
    """
    Extracts columns, primary keys, and foreign keys for the given
    "schema.table" names via INFORMATION_SCHEMA, returning the dict shape
    expected by MetadataValidator/FabricCompatibilityValidator/GovernanceValidator:
    {tables, views, procedures, relationships}.

    Table/column names are stored unqualified (schema kept separately) since
    GovernanceValidator's naming-convention check rejects '.' in identifiers.
    """
    cursor = conn.cursor()
    result_tables = []

    for full_name in tables:
        schema, name = full_name.split(".", 1)

        cursor.execute("""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """, schema, name)
        columns = [{"name": row[0], "data_type": row[1]} for row in cursor.fetchall()]

        cursor.execute("""
            SELECT kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                AND tc.TABLE_SCHEMA = ? AND tc.TABLE_NAME = ?
        """, schema, name)
        primary_key = [row[0] for row in cursor.fetchall()]

        cursor.execute("""
            SELECT
                fk_cols.COLUMN_NAME,
                pk_tab.TABLE_NAME,
                pk_cols.COLUMN_NAME
            FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE fk_cols
                ON rc.CONSTRAINT_NAME = fk_cols.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE pk_cols
                ON rc.UNIQUE_CONSTRAINT_NAME = pk_cols.CONSTRAINT_NAME
                AND fk_cols.ORDINAL_POSITION = pk_cols.ORDINAL_POSITION
            JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS pk_tab
                ON pk_cols.CONSTRAINT_NAME = pk_tab.CONSTRAINT_NAME
            WHERE fk_cols.TABLE_SCHEMA = ? AND fk_cols.TABLE_NAME = ?
        """, schema, name)
        foreign_keys = [
            {
                "column": row[0],
                "references_table": row[1],
                "references_column": row[2],
            }
            for row in cursor.fetchall()
        ]

        result_tables.append({
            "name": name,
            "schema": schema,
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
            "constraints": [],
            "indexes": [],
        })

    return {
        "tables": result_tables,
        "views": [],
        "procedures": [],
        "relationships": [],
    }


if __name__ == "__main__":
       info = check_database_connection()
       print(info)