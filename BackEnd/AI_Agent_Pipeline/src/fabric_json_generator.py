import json
import os
import re
from datetime import datetime
import pandas as pd

def map_to_fabric_datatype(source_type: str) -> str:
    if not source_type:
        return "STRING"
    st = source_type.lower().strip()
    
    if "varchar" in st or "char" in st or "text" in st or "string" in st:
        return "STRING"
    if "int" in st:
        if "bigint" in st:
            return "BIGINT"
        if "smallint" in st or "tinyint" in st:
            return "INT"
        return "INT"
    if "float" in st or "real" in st or "double" in st:
        return "DOUBLE"
    if "decimal" in st or "numeric" in st or "money" in st:
        return "DECIMAL"
    if "datetime" in st or "timestamp" in st:
        return "TIMESTAMP"
    if "date" in st:
        return "DATE"
    if "bit" in st or "boolean" in st:
        return "BOOLEAN"
    if "binary" in st or "varbinary" in st or "image" in st:
        return "BINARY"
    
    return "STRING"

def generate_fabric_json_metadata(
    tables_df, columns_df, stats_df, dep_df, views_df, procedures_df,
    agent_writeups, output_path, source_hint="database", scan_id=None
):
    """
    Compiles database scanning dataframes, dependencies, execution sequence levels,
    and medallion layer allocations into a structured Fabric-compatible JSON configuration.
    """
    independent_tables = []
    dependent_tables = []
    root_tables = []
    
    total_tables = len(tables_df) if tables_df is not None else 0
    total_columns = len(columns_df) if columns_df is not None else 0
    total_views = len(views_df) if views_df is not None else 0
    total_procedures = len(procedures_df) if procedures_df is not None else 0
    total_size = round(stats_df["size_mb"].sum(), 4) if stats_df is not None else 0.0
    total_rows = int(stats_df["row_count"].sum()) if stats_df is not None else 0
    distinct_schemas = list(tables_df["schema_name"].unique()) if tables_df is not None else ["dbo"]
    
    db_name = "live_scan"
    if tables_df is not None and not tables_df.empty and "file_name" in tables_df.columns:
        db_name = str(tables_df.iloc[0]["file_name"])

    for _, row in tables_df.iterrows() if tables_df is not None else []:
        t_name = row["table_name"]
        has_fks = False
        is_referenced = False
        if dep_df is not None and not dep_df.empty:
            has_fks = t_name in dep_df["parent_table"].values
            is_referenced = t_name in dep_df["referenced_table"].values
        if not has_fks:
            independent_tables.append(t_name)
        else:
            dependent_tables.append(t_name)
        if not is_referenced:
            root_tables.append(t_name)

    from AI_Agent_Pipeline.src.docx_generator import parse_agent_sections, parse_table_layers_from_agent, parse_relationship, get_source_display_name
    sections = parse_agent_sections(agent_writeups)
    parsed_layers = parse_table_layers_from_agent(sections.get(5), tables_df)
    
    objects = []
    for _, row in tables_df.iterrows() if tables_df is not None else []:
        t_name = row["table_name"]
        s_name = row["schema_name"]
        
        if t_name in parsed_layers:
            layer, reason = parsed_layers[t_name]
        else:
            is_dependent = False
            if dep_df is not None and not dep_df.empty:
                is_dependent = t_name in dep_df["parent_table"].values or t_name in dep_df["referenced_table"].values
            
            t_name_lower = t_name.lower()
            if "gold" in t_name_lower or "fact" in t_name_lower or "dim" in t_name_lower or "agg" in t_name_lower or "summary" in t_name_lower or t_name_lower.startswith("demo") or "vehicle" in t_name_lower:
                layer = "Gold"
                reason = "Aggregated reporting or analytical table"
            elif "silver" in t_name_lower or is_dependent:
                layer = "Silver"
                reason = "Cleaned and relational structured table"
            else:
                layer = "Bronze"
                reason = "Raw staging or ingestion layer"
                
        t_size = 0.0
        t_rows = 0
        if stats_df is not None and not stats_df.empty:
            t_stats = stats_df[stats_df["table_name"] == t_name]
            if not t_stats.empty:
                t_size = round(float(t_stats.iloc[0]["size_mb"]), 4)
                t_rows = int(t_stats.iloc[0]["row_count"])
                
        pk_col = None
        t_cols_df = columns_df[(columns_df["TableName"] == t_name) & (columns_df["SchemaName"] == s_name)] if columns_df is not None else pd.DataFrame()
        for _, c_row in t_cols_df.iterrows():
            c_name = c_row["ColumnName"]
            if c_name.lower() == "id" or c_name.lower() == f"{t_name.lower()}id" or c_name.lower() == f"{t_name.lower()}_id":
                pk_col = c_name
                break
        if not pk_col and not t_cols_df.empty:
            for _, c_row in t_cols_df.iterrows():
                c_name = c_row["ColumnName"]
                if "id" in c_name.lower() or "key" in c_name.lower():
                    pk_col = c_name
                    break
                    
        columns = []
        for _, c_row in t_cols_df.iterrows():
            src_dt = c_row["SourceDataType"]
            columns.append({
                "name": c_row["ColumnName"],
                "source_datatype": src_dt,
                "target_datatype": map_to_fabric_datatype(src_dt),
                "nullable": bool(c_row.get("IsNullable", True)),
                "ordinal_position": int(c_row["OrdinalPosition"])
            })
            
        t_deps = []
        if dep_df is not None and not dep_df.empty:
            t_deps = dep_df[dep_df["parent_table"] == t_name]["referenced_table"].unique().tolist()
            
        objects.append({
            "name": t_name,
            "schema": s_name,
            "type": "table",
            "size_mb": t_size,
            "row_count": t_rows,
            "medallion_layer": layer,
            "layer_reason": reason,
            "primary_key": pk_col,
            "columns": columns,
            "dependencies": t_deps
        })
        
    for _, row in views_df.iterrows() if views_df is not None else []:
        v_name = row["view_name"]
        s_name = row["schema_name"]
        objects.append({
            "name": v_name,
            "schema": s_name,
            "type": "view",
            "dependencies": []
        })
        
    for _, row in procedures_df.iterrows() if procedures_df is not None else []:
        p_name = row["procedure_name"]
        s_name = row["schema_name"]
        objects.append({
            "name": p_name,
            "schema": s_name,
            "type": "procedure",
            "dependencies": []
        })

    relationships = []
    for _, row in dep_df.iterrows() if dep_df is not None else []:
        rel_str = parse_relationship(row)
        col_match = re.search(r"\\(([^)]+)\\)", rel_str)
        col_name = col_match.group(1) if col_match else row["fk_name"]
        
        relationships.append({
            "fk_name": row["fk_name"],
            "parent_schema": row["parent_schema"],
            "parent_table": row["parent_table"],
            "parent_column": col_name,
            "referenced_schema": row["referenced_schema"],
            "referenced_table": row["referenced_table"],
            "referenced_column": col_name
        })

    levels = {t: 1 for t in independent_tables}
    for _ in range(10):
        changed = False
        if dep_df is not None and not dep_df.empty:
            for _, row in dep_df.iterrows():
                parent = row["parent_table"]
                ref = row["referenced_table"]
                ref_level = levels.get(ref, 1)
                parent_level = levels.get(parent, 1)
                if ref_level >= parent_level:
                    levels[parent] = ref_level + 1
                    changed = True
        if not changed:
            break
            
    all_tables = [obj["name"] for obj in objects if obj["type"] == "table"]
    sorted_tables = sorted(all_tables, key=lambda t: levels.get(t, 1))
    
    batch_views = [obj["name"] for obj in objects if obj["type"] == "view"]
    batch_procs = [obj["name"] for obj in objects if obj["type"] == "procedure"]
    
    large_tables = []
    if stats_df is not None and not stats_df.empty:
        large_tables = stats_df[stats_df["size_mb"] >= 10.0]["table_name"].tolist()

    fabric_details = {
        "Lakehouse": "Central storage repository for raw and cleansed Delta tables.",
        "Pipelines": "Orchestrates schema copying, pipeline runs, and historical loads.",
        "OneLake": "Logical unified data lake storage engine using Delta Parquet format.",
        "Spark Notebooks": "Processes Silver normalizations and Gold analytical aggregations.",
        "Power BI Reports": "Consumes conformed Gold semantic tables for business intelligence dashboards."
    }

    data = {
        "metadata": {
            "scan_id": scan_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "source_platform": get_source_display_name(source_hint),
            "target_platform": "Microsoft Fabric (OneLake)",
            "total_tables": total_tables,
            "total_columns": total_columns,
            "total_views": total_views,
            "total_procedures": total_procedures,
            "total_size_mb": total_size,
            "total_row_count": total_rows
        },
        "source": {
            "database_name": db_name,
            "schemas": distinct_schemas
        },
        "target": {
            "lakehouse_name": f"LH_{source_hint.replace(' ', '_').lower() if source_hint else 'target'}",
            "medallion_architecture": "Bronze-Silver-Gold",
            "fabric_components": fabric_details
        },
        "objects": objects,
        "relationships": relationships,
        "execution_plan": {
            "migration_sequence": sorted_tables,
            "batches": {
                "Batch 1 (Independent Tables)": independent_tables,
                "Batch 2 (Medium Dependency Tables)": [],
                "Batch 3 (Highly Dependent Tables)": dependent_tables,
                "Batch 4 (Views)": batch_views,
                "Batch 5 (Stored Procedures)": batch_procs
            },
            "strategies": {
                "table_execution_logic": "All tables in Batch 1 can be executed in parallel due to independence. Batch 3 tables should be executed sequentially after their dependencies are satisfied.",
                "incremental_load_candidates": large_tables
            }
        }
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    print(f"[INFO] Successfully saved Fabric Migration Metadata JSON: {output_path}")
    return data
