import pandas as pd


def get_size_category(size_mb, row_count):
    """
    Categorizes table size based on row count.
    """
    if row_count is None:
        return "Unknown"

    if row_count < 1000:
        return "Small"
    elif row_count < 100000:
        return "Medium"
    else:
        return "Large"


def table_summary_tool(
    table_name,
    columns_df,
    tables_df,
    stats_df,
    views_df,
    procedures_df,
    dep_df,
    schema_name=None
):
    """
    Returns a comprehensive metadata-driven summary for a single table
    to be consumed by Azure AI Foundry agents.
    """
    # Parse schema.table format if present
    if schema_name is None and "." in str(table_name):
        parts = str(table_name).split(".", 1)
        schema_name = parts[0]
        table_name = parts[1]

    # Filter tables_df
    if schema_name:
        schema_filtered = tables_df[tables_df["schema_name"].str.lower() == str(schema_name).lower()]
        matches = schema_filtered[
            (schema_filtered["table_name"].str.lower() == str(table_name).lower())
        ]
        if matches.empty and "file_name" in schema_filtered.columns:
            matches = schema_filtered[schema_filtered["file_name"].str.lower() == str(table_name).lower()]
    else:
        matches = tables_df[
            (tables_df["table_name"].str.lower() == str(table_name).lower())
        ]
        if matches.empty and "file_name" in tables_df.columns:
            matches = tables_df[tables_df["file_name"].str.lower() == str(table_name).lower()]

    if matches.empty:
        return f"Table '{table_name}' (Schema: {schema_name}) not found."

    table_row = matches.iloc[0]
    clean_table_name = table_row["table_name"]
    schema = table_row["schema_name"]
    file_name = table_row.get("file_name", clean_table_name)

    # General Info
    stats = stats_df[
        (stats_df["table_name"] == clean_table_name)
        & (stats_df["schema_name"] == schema)
    ]
    if stats.empty and "file_name" in stats_df.columns:
        stats = stats_df[stats_df["file_name"] == file_name]

    row_count = None
    size_mb = None
    table_type = "Base Table"

    if not stats.empty:
        row_count = stats.iloc[0]["row_count"]
        size_mb = stats.iloc[0]["size_mb"]

        if "table_type" in stats.columns:
            table_type = stats.iloc[0]["table_type"]

    # Columns
    cols = columns_df[
        (columns_df["TableName"] == clean_table_name)
        & (columns_df["SchemaName"] == schema)
    ]
    if cols.empty and "FileName" in columns_df.columns:
        cols = columns_df[columns_df["FileName"] == file_name]

    if size_mb is None or float(size_mb or 0) <= 0.0:
        col_cnt = len(cols) if not cols.empty else 10
        r_cnt = int(row_count or 0)
        size_mb = round((64 + (r_cnt * max(col_cnt * 35, 64)) / 1024.0) / 1024.0, 2)
        size_mb = max(0.06, size_mb)

    size_category = get_size_category(size_mb, row_count)

    col_list = cols["ColumnName"].tolist()

    column_details = "\n".join(
        [
            f"    • {row['ColumnName']} ({row['SourceDataType']})"
            for _, row in cols.iterrows()
        ]
    )

    # Primary Key Candidates
    pk_cols = [
        c for c in col_list
        if "id" in c.lower() or "key" in c.lower()
    ]

    # Foreign Keys / Dependencies
    fk_rows = dep_df[
        (dep_df["parent_table"] == clean_table_name)
    ]
    if "parent_schema" in dep_df.columns:
        schema_fk = fk_rows[fk_rows["parent_schema"] == schema]
        if not schema_fk.empty:
            fk_rows = schema_fk

    fk_text = "\n".join(
        [
            f"    • {row['fk_name']} → {row['referenced_table']}"
            for _, row in fk_rows.iterrows()
            if "fk_name" in dep_df.columns
        ]
    )

    referenced = dep_df[
        dep_df["parent_table"] == clean_table_name
    ]["referenced_table"].dropna().unique()

    dependent = dep_df[
        dep_df["referenced_table"] == clean_table_name
    ]["parent_table"].dropna().unique()

    # Views & Procedures
    related_views = []
    if "schema_name" in views_df.columns and "view_name" in views_df.columns:
        related_views = views_df[views_df["schema_name"] == schema]["view_name"].tolist()

    related_sp = []
    if "schema_name" in procedures_df.columns and "procedure_name" in procedures_df.columns:
        related_sp = procedures_df[procedures_df["schema_name"] == schema]["procedure_name"].tolist()

    summary = f"""
Table Name: {clean_table_name}
Schema: {schema}

General Info:
- Row Count: {row_count}
- Size (MB): {size_mb}
- Size Category: {size_category}
- Table Type: {table_type}

Structure:
- Total Columns: {len(col_list)}
- Columns:
{column_details if column_details else "None"}

- Primary Keys:
{", ".join(pk_cols) if pk_cols else "None"}

- Foreign Keys:
{fk_text if fk_text else "None"}

Dependencies:
- Referenced Tables:
{", ".join(referenced) if len(referenced) else "None"}

- Dependent Tables:
{", ".join(dependent) if len(dependent) else "None"}

Usage:
- Related Views:
{", ".join(related_views) if related_views else "None"}

- Related Stored Procedures:
{", ".join(related_sp) if related_sp else "None"}
"""

    return summary.strip()


def _filter_by_schema(df, name_col, name, schema_col, schema_name):
    """
    Shared case-insensitive (schema, name) lookup used by the view/function/
    volume summary tools below - same matching approach as table_summary_tool
    above, just factored out since none of the three needs table_summary_tool's
    row-count/size/dependency logic.
    """
    if schema_name:
        matches = df[
            (df[schema_col].str.lower() == str(schema_name).lower())
            & (df[name_col].str.lower() == str(name).lower())
        ]
    else:
        matches = df[df[name_col].str.lower() == str(name).lower()]
    return matches


def view_summary_tool(view_name, columns_df, views_df, schema_name=None):
    """
    Returns a metadata-driven summary for a single Unity Catalog view -
    the sibling of table_summary_tool() for VIEW objects, which carry a SQL
    definition instead of stored rows/size, but still expose the same
    ColumnName/SourceDataType shape in columns_df as any base table.
    """
    matches = _filter_by_schema(views_df, "view_name", view_name, "schema_name", schema_name)
    if matches.empty:
        return f"View '{view_name}' (Schema: {schema_name}) not found."

    view_row = matches.iloc[0]
    clean_view_name = view_row["view_name"]
    schema = view_row["schema_name"]
    definition = view_row.get("definition") or "Not available"

    cols = columns_df[
        (columns_df["TableName"] == clean_view_name)
        & (columns_df["SchemaName"] == schema)
    ]
    column_details = "\n".join(
        f"    • {row['ColumnName']} ({row['SourceDataType']})"
        for _, row in cols.iterrows()
    )

    summary = f"""
View Name: {clean_view_name}
Schema: {schema}

Structure:
- Total Columns: {len(cols)}
- Columns:
{column_details if column_details else "None"}

Definition:
{definition}
"""
    return summary.strip()


def function_summary_tool(function_name, functions_df, schema_name=None):
    """
    Returns a metadata-driven summary for a single Unity Catalog function.
    Functions only carry a name/return type in functions_df (no columns,
    row counts, or dependencies), so this is intentionally much shorter
    than table_summary_tool()/view_summary_tool().
    """
    matches = _filter_by_schema(functions_df, "function_name", function_name, "schema_name", schema_name)
    if matches.empty:
        return f"Function '{function_name}' (Schema: {schema_name}) not found."

    func_row = matches.iloc[0]
    summary = f"""
Function Name: {func_row['function_name']}
Schema: {func_row['schema_name']}
Return Type: {func_row.get('return_type') or 'Unknown'}
"""
    return summary.strip()


def procedure_summary_tool(procedure_name, procedures_df, schema_name=None):
    """
    Returns a metadata-driven summary for a single stored procedure.
    procedures_df only ever carries a name/schema (see parse_schema_dict()
    in metadataProcessor.py - Unity Catalog's information_schema.routines
    query for PROCEDURE routines doesn't fetch a return type the way the
    FUNCTION query does), so this is the thinnest of the four object tools.
    """
    matches = _filter_by_schema(procedures_df, "procedure_name", procedure_name, "schema_name", schema_name)
    if matches.empty:
        return f"Stored procedure '{procedure_name}' (Schema: {schema_name}) not found."

    proc_row = matches.iloc[0]
    summary = f"""
Procedure Name: {proc_row['procedure_name']}
Schema: {proc_row['schema_name']}
"""
    return summary.strip()


def volume_summary_tool(volume_name, volumes_df, schema_name=None):
    """
    Returns a metadata-driven summary for a single Unity Catalog volume.
    Volumes are object-storage-backed file mounts, not relational objects,
    so - like function_summary_tool() - there are no columns/dependencies
    to report, just the storage-facing attributes captured at scan time.
    """
    matches = _filter_by_schema(volumes_df, "volume_name", volume_name, "schema_name", schema_name)
    if matches.empty:
        return f"Volume '{volume_name}' (Schema: {schema_name}) not found."

    vol_row = matches.iloc[0]
    summary = f"""
Volume Name: {vol_row['volume_name']}
Schema: {vol_row['schema_name']}
Volume Type: {vol_row.get('volume_type') or 'Unknown'}
Storage Location: {vol_row.get('storage_location') or 'Unknown'}
"""
    return summary.strip()
