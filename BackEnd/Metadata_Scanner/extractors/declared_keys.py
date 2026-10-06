"""
Attaches declared primary and foreign keys to an extractor's schema_map.

Each extractor reads keys from its own catalog (sys.* on SQL Server/Synapse,
SHOW ... KEYS on Snowflake, information_schema on Databricks) and hands the
rows to attach_declared_keys(), so every source ends up with the same
per-table shape:

    "primary_key":  ["ORDER_ID"]                      # [] when none declared
    "foreign_keys": [{"name": "FK_ORDERS_CUSTOMERS",
                      "columns": ["CUSTOMER_ID"],
                      "ref_schema": "SALES", "ref_table": "CUSTOMERS",
                      "ref_columns": ["CUSTOMER_ID"]}]

Both keys are only set when the catalog could be read - a table without
them means "unknown", not "no keys" - which is how Migrator/er_diagram.py
decides between declared and inferred relationships.
"""

# Shared by SQL Server and Synapse, which have the same sys.* catalog.
# Synapse dedicated pools only allow NOT ENFORCED primary keys and no
# foreign keys, so there the FK query simply returns nothing.
SQLSERVER_PRIMARY_KEYS_SQL = """
    SELECT s.name, t.name, c.name, ic.key_ordinal
    FROM sys.key_constraints kc
    JOIN sys.tables t ON t.object_id = kc.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
    JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
    WHERE kc.type = 'PK'
"""

SQLSERVER_FOREIGN_KEYS_SQL = """
    SELECT fk.name, ps.name, pt.name, pc.name, rs.name, rt.name, rc.name, fkc.constraint_column_id
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    JOIN sys.tables pt ON pt.object_id = fk.parent_object_id
    JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
    JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
    JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id
    JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
    JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
"""


def read_sqlserver_keys(connection, schema_map):
    """Runs the sys.* key queries on a pymssql connection and attaches the results."""
    cursor = connection.cursor(as_dict=False)
    cursor.execute(SQLSERVER_PRIMARY_KEYS_SQL)
    pk_rows = [tuple(r) for r in cursor.fetchall()]
    cursor.execute(SQLSERVER_FOREIGN_KEYS_SQL)
    fk_rows = [tuple(r) for r in cursor.fetchall()]
    return attach_declared_keys(schema_map, pk_rows, fk_rows)


def attach_declared_keys(schema_map, pk_rows, fk_rows):
    """
    pk_rows: (schema, table, column, position) per primary-key column.
    fk_rows: (fk_name, schema, table, column, ref_schema, ref_table,
              ref_column, position) per foreign-key column pair.
    """
    primary_keys = {}
    for schema, table, column, position in sorted(pk_rows, key=lambda r: (r[0], r[1], r[3] or 0)):
        primary_keys.setdefault((schema, table), []).append(column)

    foreign_keys = {}
    for fk_name, schema, table, column, ref_schema, ref_table, ref_column, position in sorted(
        fk_rows, key=lambda r: (r[1], r[2], r[0] or "", r[7] or 0)
    ):
        fk = foreign_keys.setdefault((schema, table), {}).setdefault(fk_name, {
            "name": fk_name,
            "columns": [],
            "ref_schema": ref_schema,
            "ref_table": ref_table,
            "ref_columns": [],
        })
        fk["columns"].append(column)
        fk["ref_columns"].append(ref_column)

    for schema in schema_map.values():
        for table in schema.get("tables", []):
            if (table.get("type") or "").upper() in ("VIEW", "MATERIALIZED VIEW"):
                continue
            key = (schema["name"], table["name"])
            table["primary_key"] = primary_keys.get(key, [])
            table["foreign_keys"] = list(foreign_keys.get(key, {}).values())

    # (tables with a primary key, foreign keys) - for the extractor's log line.
    return len(primary_keys), sum(len(v) for v in foreign_keys.values())
