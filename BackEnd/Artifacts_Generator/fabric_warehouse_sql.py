"""
Scaffold for creating placeholder Views and Stored Procedures in a Fabric
Warehouse's SQL analytics endpoint - the Views/Stored-Procedures sibling of
DB2_2_Fabric.py's Delta-table sync and fabric_pipeline_builder.py's
pipeline scaffold.

Why this is a scaffold, not a finished feature: a Fabric Lakehouse can't
hold SQL views or stored procedures at all - only a Warehouse can. And
unlike Lakehouses/Pipelines/Notebooks, a Warehouse's views/procedures
aren't created through the Fabric REST item-definition API; they only
exist as real T-SQL DDL executed against the Warehouse's SQL analytics
endpoint. So this module opens an actual database connection and runs
CREATE OR ALTER statements directly, authenticated with an Azure AD access
token instead of a password.

Two connection backends, tried in this order:
  1. pyodbc + "ODBC Driver 18/17 for SQL Server" - Microsoft's documented,
     battle-tested path, used automatically IF that driver happens to be
     installed on the machine running this.
  2. python-tds (pytds) - a pure-Python TDS client, no system driver
     required at all (just `pip install python-tds`). This is the
     fallback used when the ODBC driver isn't present, and is what has
     actually been exercised against a live Fabric Warehouse.

Getting pytds to work against a live Fabric Warehouse from outside Azure's
network needed three fixes beyond a plain connect() call:
  - TLS needs an explicit `cafile` (a trusted CA bundle, e.g. certifi's) -
    without one, pytds silently tells the server "no encryption support"
    during PRELOGIN, which Fabric's TLS-mandatory endpoint rejects.
  - `validate_host=False` works around a pytds 1.17.1 bug: it calls
    `X509.get_extension()`, a pyOpenSSL API removed in newer pyOpenSSL
    releases (installed here: 26.x). The actual certificate-chain/CA
    trust check (via `cafile`) is unaffected by this - it only skips
    pytds's *additional* SAN-hostname cross-check, which is a low-risk
    thing to skip here since the hostname we connect to comes straight
    from Fabric's own authenticated REST API response, not user input.
  - Fabric's login sequence redirects to an internal node using SQL
    Server's legacy "host\\instance" named-instance address format (e.g.
    "...pbidedicated.windows.net\\XXXXX-dw"). pytds's redirect handler
    passes that whole string into getaddrinfo() unmodified, which fails
    immediately since a backslash isn't valid in a DNS name. And unlike a
    real named SQL Server instance, that node doesn't run the SQL Browser
    UDP service the "\\instance" syntax normally resolves through - this
    is Fabric-internal routing reusing that address format, not an actual
    named instance. Fix: for the *socket* connection only, strip the
    "\\instance" suffix and connect directly to host:1433; the TLS/login
    layer still needs the untouched full string as the server name, since
    that's what Fabric's routing keys off. See _patched_socket_for_redirect().

The bigger caveat, unrelated to any of the above: wherever a view's real
SQL definition was captured, it is in Databricks SQL (Spark SQL) - a
different dialect from T-SQL, and Unity Catalog's information_schema.routines
doesn't expose a procedure body at all. This module does NOT attempt to
translate anything. Every object it creates is a structurally valid but
functionally empty placeholder, with the original (untranslated) source SQL
embedded as a comment for a person to port by hand in Fabric Studio.
"""
import contextlib
import socket as _socket_module
import struct

# The ODBC connection attribute Microsoft's SQL Server ODBC drivers use for
# "authenticate with this already-issued AAD access token"
# (SQL_COPT_SS_ACCESS_TOKEN) - see Microsoft's pyodbc/Azure AD access token
# documentation for connecting to Azure SQL/Synapse/Fabric Warehouse
# without a username or password.
_SQL_COPT_SS_ACCESS_TOKEN = 1256

PREFERRED_ODBC_DRIVERS = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]


def _pick_odbc_driver():
    """Returns an installed preferred ODBC driver name, or None if pyodbc
    isn't importable or neither preferred driver is installed."""
    try:
        import pyodbc
    except ImportError:
        return None
    available = set(pyodbc.drivers())
    for name in PREFERRED_ODBC_DRIVERS:
        if name in available:
            return name
    return None


def _encode_token(token):
    """
    pyodbc's access-token connection attribute wants the token UTF-16-LE
    encoded and length-prefixed as a 4-byte little-endian int - the exact
    byte layout SQL_COPT_SS_ACCESS_TOKEN expects.
    """
    token_bytes = token.encode("utf-16-le")
    return struct.pack("=i", len(token_bytes)) + token_bytes


def _connect_pyodbc(connection_string, sql_token, driver):
    import pyodbc

    odbc_conn_str = f"Driver={{{driver}}};Server={connection_string};Encrypt=yes;TrustServerCertificate=no;"
    return pyodbc.connect(odbc_conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _encode_token(sql_token)})


@contextlib.contextmanager
def _patched_socket_for_redirect():
    """
    See the module docstring's third bullet on Fabric's "host\\instance"
    login redirect. Scoped to just the pytds connect() call below (rather
    than patched at import time) so it can't affect any other
    socket.create_connection() caller elsewhere in the process - e.g. the
    requests/azure-identity HTTP calls this same pipeline makes. It only
    ever changes behavior for a host string containing a backslash, which
    a real hostname never does, but scoping it keeps the blast radius to
    exactly this connection attempt regardless.
    """
    original = _socket_module.create_connection

    def patched(address, *args, **kwargs):
        host, port = address
        if "\\" in host:
            real_host, _instance = host.split("\\", 1)
            return original((real_host, 1433), *args, **kwargs)
        return original(address, *args, **kwargs)

    _socket_module.create_connection = patched
    try:
        yield
    finally:
        _socket_module.create_connection = original


def _connect_pytds(connection_string, sql_token, database=None):
    import certifi
    import pytds

    with _patched_socket_for_redirect():
        return pytds.connect(
            server=connection_string,
            port=1433,
            database=database,
            access_token_callable=lambda: sql_token,
            login_timeout=30,
            cafile=certifi.where(),
            validate_host=False,
        )


def connect(connection_string, sql_token, database=None, driver=None):
    """
    Opens a connection to a Fabric Warehouse's SQL analytics endpoint
    using an already-issued Azure AD access token (from
    fabric_api.get_fabric_sql_token()) instead of a username/password.
    Prefers pyodbc if a modern ODBC driver is installed; otherwise falls
    back to pytds (pure Python, no system driver needed) - see the module
    docstring for what that fallback needed to actually work against
    Fabric.

    connection_string: the hostname Fabric reports as the Warehouse item's
    properties.connectionString (e.g. "xxxx.datawarehouse.fabric.microsoft.com").
    database: the Warehouse's displayName, used as the initial database
    context on the pytds path (pyodbc reaches the right context via the
    driver's own default-database resolution, so it's unused there).
    """
    odbc_driver = driver or _pick_odbc_driver()
    if odbc_driver:
        return _connect_pyodbc(connection_string, sql_token, odbc_driver)
    return _connect_pytds(connection_string, sql_token, database=database)


def clean_sql_identifier(name):
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in (name or "").strip()) or "_"


def ensure_schema(cursor, schema_name):
    """
    Creates the target SQL schema if missing - CREATE VIEW/PROCEDURE both
    require it to exist first. Interpolated directly (not parameterized)
    since schema_name is already restricted to [A-Za-z0-9_] by
    clean_sql_identifier() below, and pyodbc/pytds don't agree on a
    parameter placeholder style (qmark vs pyformat) - avoiding placeholders
    here keeps this one function working unchanged under either backend.
    """
    schema_name = clean_sql_identifier(schema_name)
    cursor.execute(
        f"IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = '{schema_name}') "
        f"EXEC('CREATE SCHEMA [{schema_name}]')"
    )
    return schema_name


def create_placeholder_view(cursor, schema_name, view_name, source_system=None, original_definition=None):
    """
    Creates (or replaces) a structurally valid but functionally empty view
    - `WHERE 1 = 0` so it can never return real rows - with the real,
    untranslated source SQL definition (if known) embedded as a comment
    for a person to translate into T-SQL by hand.
    """
    schema_name = ensure_schema(cursor, schema_name)
    view_name = clean_sql_identifier(view_name)

    comment_lines = [
        f"-- Placeholder view generated from a {source_system or 'source'} scan.",
        "-- The original view definition (untranslated - not valid T-SQL as-is) was:",
    ]
    for line in (original_definition or "Not available").splitlines() or ["Not available"]:
        comment_lines.append(f"-- {line}")

    sql = (
        "\n".join(comment_lines)
        + f"\nCREATE OR ALTER VIEW [{schema_name}].[{view_name}] AS "
        + "SELECT CAST(NULL AS INT) AS placeholder_column WHERE 1 = 0;"
    )
    cursor.execute(sql)


def create_placeholder_procedure(cursor, schema_name, procedure_name, source_system=None):
    """
    Creates (or replaces) an empty, no-op stored procedure as a structural
    placeholder. Unity Catalog's information_schema.routines doesn't
    expose a procedure body at all, so there is no source logic to embed
    here even as a comment - only the name/schema were ever known.
    """
    schema_name = ensure_schema(cursor, schema_name)
    procedure_name = clean_sql_identifier(procedure_name)

    sql = (
        f"-- Placeholder procedure generated from a {source_system or 'source'} scan; no logic ported.\n"
        f"CREATE OR ALTER PROCEDURE [{schema_name}].[{procedure_name}] AS\nBEGIN\n    RETURN 0;\nEND;"
    )
    cursor.execute(sql)
