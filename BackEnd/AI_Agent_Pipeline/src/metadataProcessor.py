import os
import re
import sys
import json
import pandas as pd
 
# Reconfigure stdout to use UTF-8 just in case
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass
 
 
def read_csv_robust(file_path):
    """
    Attempts to read a CSV file using multiple fallback strategies
    to handle malformed lines, unescaped quotes, or complex JSON strings.
    """
    try:
        return pd.read_csv(file_path, nrows=100, low_memory=False)
    except Exception:
        try:
            return pd.read_csv(file_path, nrows=100, on_bad_lines='skip', low_memory=False)
        except Exception:
            try:
                return pd.read_csv(file_path, nrows=100, engine='python', on_bad_lines='skip')
            except Exception:
                return pd.read_csv(file_path, nrows=100, quoting=3, on_bad_lines='skip')
 
 
def read_json_robust(file_path):
    """
    Attempts to read a JSON file using multiple fallback strategies
    (standard JSON array, JSON lines, etc.) and returns a Pandas DataFrame.
    """
    try:
        # Strategy 1: Read standard JSON array using pandas
        return pd.read_json(file_path, nrows=100)
    except Exception:
        try:
            # Strategy 2: Read JSON lines using pandas
            return pd.read_json(file_path, lines=True, nrows=100)
        except Exception:
            # Strategy 3: Native json parser fallback (handles full loads for small custom schemas)
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return pd.DataFrame(data[:100])
                elif isinstance(data, dict):
                    if "columns" in data and "data" in data:
                        return pd.DataFrame(data["data"][:100], columns=data["columns"])
                    return pd.DataFrame([data])
            except Exception:
                # Strategy 4: Line-by-line manual parse fallback for large malformed files
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        records = []
                        for _ in range(100):
                            line = f.readline()
                            if not line:
                                break
                            try:
                                records.append(json.loads(line))
                            except Exception:
                                continue
                        if records:
                            return pd.DataFrame(records)
                except Exception:
                    pass
            # Return empty DataFrame if all fail
            return pd.DataFrame()
 
 
def count_json_rows(file_path):
    """
    Attempts to count rows in a JSON file without loading the entire content into memory,
    supporting both standard JSON arrays and JSON lines.
    """
    try:
        # Check if JSON lines format by reading the first line
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline().strip()
            if first_line.startswith("{") and not first_line.endswith(","):
                f.seek(0)
                count = sum(1 for _ in f)
                return count
    except Exception:
        pass
    try:
        # Fallback to loading standard JSON array
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
            if isinstance(data, list):
                return len(data)
            elif isinstance(data, dict):
                if "data" in data and isinstance(data["data"], list):
                    return len(data["data"])
                return 1
    except Exception:
        pass
 
    try:
        # Fallback: simple line count
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            count = sum(1 for _ in f)
            return max(0, count - 1)
    except Exception:
        return 0
 
 
def infer_sql_type(col_name, series):
    """
    Infers the target SQL data type based on column naming conventions
    and Pandas Series data types.
    """
    col_lower = col_name.lower()
    if "date" in col_lower or "dt" in col_lower or "time" in col_lower or "ts" in col_lower:
        if "date" in col_lower and "time" not in col_lower:
            return "date"
        return "datetime"
    if pd.api.types.is_integer_dtype(series):
        if series.max() > 2147483647:
            return "bigint"
        elif series.max() < 32767:
            return "smallint"
        return "int"
    elif pd.api.types.is_float_dtype(series):
        return "decimal(10,5)"
    elif pd.api.types.is_bool_dtype(series):
        return "bit"
    else:
        try:
            max_len = series.dropna().astype(str).str.len().max()
        except Exception:
            max_len = 0
        if pd.isna(max_len) or max_len == 0:
            max_len = 255
        if max_len > 1000:
            return "nvarchar(MAX)"
        elif "name" in col_lower or "nm" in col_lower or "email" in col_lower:
            return "nvarchar(150)"
        else:
            return f"nvarchar({int(max_len * 1.5 // 50 * 50 + 50)})"  # rounded up
 
 
def parse_schema_dict(schema_data, label="metadata"):
    """
    Parses one {"database": ..., "schemas": [...]} metadata export - the
    shape every extractor in Metadata_Scanner/extractors returns - into
    flat table/column/stat/view/procedure/function/volume records. Shared
    by collect_metadata() (reading such a file off disk) and
    metadata_to_dataframes() (consuming a live scan's in-memory dict
    directly).
    """
    tables_list = []
    columns_list = []
    stats_list = []
    views_list = []
    procedures_list = []
    functions_list = []
    volumes_list = []
    for schema in schema_data.get("schemas", []):
        schema_name = schema.get("name", "dbo")
        for procedure in schema.get("procedures", []):
            procedures_list.append({
                "schema_name": schema_name,
                "procedure_name": procedure.get("name"),
            })
        for function in schema.get("functions", []):
            functions_list.append({
                "schema_name": schema_name,
                "function_name": function.get("name"),
                "return_type": function.get("return_type"),
            })
        for volume in schema.get("volumes", []):
            volumes_list.append({
                "schema_name": schema_name,
                "volume_name": volume.get("name"),
                "volume_type": volume.get("volume_type"),
                "storage_location": volume.get("storage_location"),
            })
        for table in schema.get("tables", []):
            table_name = table.get("name")
            table_type = table.get("type", "BASE TABLE")
            if table_type.upper() == "VIEW":
                views_list.append({
                    "schema_name": schema_name,
                    "view_name": table_name
                })
            else:
                tables_list.append({
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "file_name": label,
                    "full_table_name": f"{schema_name}.{table_name}"
                })
                row_cnt = table.get("row_count") or 0
                sz_mb = table.get("size_mb") or 0.0
                try:
                    sz_mb = float(sz_mb)
                except Exception:
                    sz_mb = 0.0

                if sz_mb <= 0.0:
                    cols = table.get("columns", [])
                    num_cols = len(cols)
                    base_bytes = 64 * 1024  # 64 KB base page allocation
                    row_bytes = int(row_cnt) * max(num_cols * 32, 64)
                    sz_mb = round((base_bytes + row_bytes) / (1024.0 * 1024.0), 2)
                    sz_mb = max(0.06, sz_mb)

                stats_list.append({
                    "row_count": row_cnt,
                    "schema_name": schema_name,
                    "size_mb": sz_mb,
                    "table_name": table_name,
                    "file_name": label,
                    "table_type": table_type
                })
            # Process columns
            for idx, col in enumerate(table.get("columns", [])):
                col_name = col.get("name")
                datatype = col.get("datatype", "nvarchar")
                max_len = col.get("max_length")
                if max_len is not None and max_len != -1 and ("varchar" in datatype.lower() or "char" in datatype.lower()):
                    datatype_str = f"{datatype}({max_len})"
                elif max_len == -1 and ("varchar" in datatype.lower() or "char" in datatype.lower()):
                    datatype_str = f"{datatype}(MAX)"
                else:
                    datatype_str = datatype
                columns_list.append({
                    "SchemaName": schema_name,
                    "TableName": table_name,
                    "FileName": label,
                    "ColumnName": col_name,
                    "SourceDataType": datatype_str,
                    "TargetDataType": datatype_str,
                    "OrdinalPosition": idx + 1,
                    "IsNullable": col.get("nullable", "YES").upper() == "YES",
                    "IsActive": 1
                })
    return tables_list, columns_list, stats_list, views_list, procedures_list, functions_list, volumes_list


def build_dataframes(tables_list, columns_list, stats_list, views_list, procedures_list=None, functions_list=None, volumes_list=None):
    """
    Turns the flat record lists gathered by collect_metadata()/
    metadata_to_dataframes() into the DataFrames the rest of the AI
    pipeline consumes, including foreign-key dependency inference from
    naming conventions (*_id / *_key columns).
    """
    tables_df = pd.DataFrame(tables_list)
    columns_df = pd.DataFrame(columns_list)
    stats_df = pd.DataFrame(stats_list)
    views_df = pd.DataFrame(views_list) if views_list else pd.DataFrame(columns=["schema_name", "view_name"])
    procedures_df = pd.DataFrame(procedures_list) if procedures_list else pd.DataFrame(columns=["schema_name", "procedure_name"])
    functions_df = pd.DataFrame(functions_list) if functions_list else pd.DataFrame(columns=["schema_name", "function_name", "return_type"])
    volumes_df = pd.DataFrame(volumes_list) if volumes_list else pd.DataFrame(columns=["schema_name", "volume_name", "volume_type", "storage_location"])
    dep_list = []
    for t_idx, t_row in tables_df.iterrows():
        t_name = t_row["table_name"]
        t_schema = t_row["schema_name"]
        t_cols = columns_df[
            (columns_df["TableName"] == t_name) & (columns_df["SchemaName"] == t_schema)
        ]["ColumnName"].tolist()
        for col in t_cols:
            col_lower = col.lower()
            if col_lower.endswith("_id") or col_lower.endswith("_key"):
                base_ref = col_lower[:-3] if col_lower.endswith("_id") else col_lower[:-4]
                for ref_row in tables_list:
                    ref_name = ref_row["table_name"]
                    ref_schema = ref_row["schema_name"]
                    ref_name_lower = ref_name.lower()
                    if ref_name_lower == base_ref or ref_name_lower.endswith("_" + base_ref) or ref_name_lower.endswith(base_ref):
                        if (ref_name != t_name or ref_schema != t_schema):
                            dep_list.append({
                                "fk_name": f"FK_{t_name}_{col}_{ref_name}",
                                "parent_schema": t_schema,
                                "parent_table": t_name,
                                "referenced_schema": ref_schema,
                                "referenced_table": ref_name
                            })
    dep_df = pd.DataFrame(dep_list) if dep_list else pd.DataFrame(columns=["fk_name", "parent_schema", "parent_table", "referenced_schema", "referenced_table"])
    return tables_df, columns_df, stats_df, views_df, procedures_df, functions_df, volumes_df, dep_df


def metadata_to_dataframes(metadata: dict, label: str = "live_scan"):
    """
    Converts a live extractor's in-memory metadata dict directly into the
    dataframes the AI pipeline needs, bypassing collect_metadata()'s
    data/ directory scan entirely. This is what makes the Assessment
    Report reflect exactly the source that was just scanned, instead of
    whatever file happens to be sitting on disk in data/.
    """
    tables_list, columns_list, stats_list, views_list, procedures_list, functions_list, volumes_list = parse_schema_dict(metadata, label=label)
    return build_dataframes(tables_list, columns_list, stats_list, views_list, procedures_list, functions_list, volumes_list)


def collect_metadata(data_dir):
    """
    Scans the given data directory and parses metadata for all CSV and JSON files,
    extracting tables, columns, stats, views, procedures, and foreign key dependencies.
    """
    print(f"[INFO] Scanning data folder: {data_dir}")
    file_entries = []
    for root, _, files in os.walk(data_dir):
        for f in files:
            file_path = os.path.join(root, f)
            rel_dir = os.path.relpath(root, data_dir)
            file_entries.append((f, file_path, rel_dir if rel_dir != "." else ""))
    tables_list = []
    columns_list = []
    stats_list = []
    views_list = []
    procedures_list = []
    functions_list = []
    volumes_list = []
    def get_default_schema(table_name):
        name_upper = table_name.upper()
        if any(x in name_upper for x in ["DQ_", "OBJ_", "JOB_", "MDR_", "APPL_", "BSN_", "CONN_", "DATA_"]):
            return "MDR_T"
        return "dbo"
    def clean_table_name(filename):
        base = os.path.splitext(filename)[0]
        return base if base else filename
    for raw_filename, file_path, sub_folder in file_entries:
        c_table_name = clean_table_name(raw_filename)
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        # Check if the file is a database schema metadata JSON export
        is_schema_json = False
        schema_data = None
        if raw_filename.lower().endswith(".json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    schema_data = json.load(f)
                if isinstance(schema_data, dict) and "schemas" in schema_data:
                    is_schema_json = True
            except Exception:
                pass
        if is_schema_json:
            try:
                t_list, c_list, s_list, v_list, p_list, f_list, vol_list = parse_schema_dict(schema_data, label=raw_filename)
                tables_list.extend(t_list)
                columns_list.extend(c_list)
                stats_list.extend(s_list)
                views_list.extend(v_list)
                procedures_list.extend(p_list)
                functions_list.extend(f_list)
                volumes_list.extend(vol_list)
                print(f"[INFO] Successfully parsed database schema metadata JSON: {raw_filename}")
                continue
            except Exception as e:
                print(f"[ERROR] Error parsing schema JSON {raw_filename}: {e}")
        # Fallback to standard CSV or flat record JSON parsing
        try:
            if raw_filename.lower().endswith(".json"):
                df = read_json_robust(file_path)
            else:
                df = read_csv_robust(file_path)
            # Detect schema name
            detected_schema = None
            for col in df.columns:
                if col.upper() in ["SCHEMA_NM", "SCHEMANAME", "SCHEMA", "SCHEMA_NAME"]:
                    non_nulls = df[col].dropna().astype(str).str.strip().tolist()
                    if non_nulls:
                        detected_schema = non_nulls[0]
                        break
            if not detected_schema:
                if sub_folder:
                    detected_schema = sub_folder.replace("\\", ".").replace("/", ".")
                else:
                    detected_schema = get_default_schema(c_table_name)
            # Ensure uniqueness of (schema_name, table_name) across uploaded files
            existing_matches = [t for t in tables_list if t["schema_name"] == detected_schema and t["table_name"] == c_table_name]
            if existing_matches:
                version_idx = len(existing_matches) + 1
                assigned_schema = f"{detected_schema}_v{version_idx}"
            else:
                assigned_schema = detected_schema
            # Count rows
            if raw_filename.lower().endswith(".json"):
                row_count = count_json_rows(file_path)
            else:
                row_count = 0
                with open(file_path, "r", encoding="utf-8", errors="ignore") as file_handle:
                    for line in file_handle:
                        row_count += 1
                row_count = max(0, row_count - 1)
            tables_list.append({
                "schema_name": assigned_schema,
                "table_name": c_table_name,
                "file_name": raw_filename,
                "full_table_name": f"{assigned_schema}.{c_table_name}"
            })
            stats_list.append({
                "row_count": row_count,
                "schema_name": assigned_schema,
                "size_mb": round(size_mb, 4),
                "table_name": c_table_name,
                "file_name": raw_filename,
                "table_type": "Base Table"
            })
            for idx, col in enumerate(df.columns):
                sql_type = infer_sql_type(col, df[col])
                columns_list.append({
                    "SchemaName": assigned_schema,
                    "TableName": c_table_name,
                    "FileName": raw_filename,
                    "ColumnName": col,
                    "SourceDataType": sql_type,
                    "TargetDataType": sql_type,
                    "OrdinalPosition": idx + 1,
                    "IsNullable": True,
                    "IsActive": 1
                })
        except Exception as e:
            print(f"[ERROR] Error processing file {raw_filename}: {e}")
    return build_dataframes(tables_list, columns_list, stats_list, views_list, procedures_list, functions_list, volumes_list)