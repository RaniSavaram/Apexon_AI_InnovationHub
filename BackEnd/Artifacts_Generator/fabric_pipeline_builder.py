"""
Builds a Fabric Data Pipeline's pipeline-content.json (the Data
Factory/Fabric pipeline JSON schema: {"properties": {"activities": [...]}})
from the same table list DB2_2_Fabric.py already syncs into a Lakehouse, so
the migration plan produces a visible pipeline in Fabric Studio - not just
empty Delta tables - representing the plan's Bronze/Silver/Gold execution
order as one Copy activity per table.

Scope (structural, not yet runnable): each activity's sink is fully wired
to the real target Lakehouse table (workspace/lakehouse ids, schema, table
name) since that information is already known once DB2_2_Fabric.py has
resolved the target Lakehouse. Each activity's source is deliberately left
as a placeholder - no source connection is configured anywhere in this
pipeline yet, so opening the pipeline in Fabric Studio and pointing each
Copy activity's source at the real source system (Databricks/SQL Server/
etc.) is expected as a manual follow-up step, not something this script
does. This mirrors DB2_2_Fabric.py itself, which only creates empty Delta
tables (schema, no data) rather than actually moving data.
"""
import re

# Bronze feeds Silver feeds Gold in a typical medallion architecture, so
# activities are sequenced in this order - each layer's activities run in
# parallel, and depend on every activity in the previous non-empty layer
# completing first. Tables with no assigned layer run first, ahead of Bronze.
LAYER_ORDER = ["(no layer assigned)", "Bronze", "Silver", "Gold"]


def clean_activity_name(name):
    """
    Fabric/Data Factory activity names must be unique within the pipeline
    and are safest as alphanumerics/underscore/hyphen - mirrors
    DB2_2_Fabric.py's clean_identifier() but kept local here to avoid a
    circular import (DB2_2_Fabric.py is the one importing this module).
    """
    cleaned = "".join(c if (c.isalnum() or c in "_-") else "_" for c in (name or "").strip())
    return cleaned[:100] or "activity"


def build_pipeline_name(source_system, database_name):
    """e.g. source_system='databricks', database_name='sales_prod' -> 'databricks_sales_prod_pipeline'."""
    src = re.sub(r"[^A-Za-z0-9_]", "_", (source_system or "source").strip().lower())
    db = re.sub(r"[^A-Za-z0-9_]", "_", (database_name or "db").strip().lower())
    return f"{src}_{db}_pipeline"


def _lakehouse_sink(workspace_id, lakehouse_id, lakehouse_display_name, schema_name, table_name):
    return {
        "type": "LakehouseTableSink",
        "tableActionOption": "Append",
        "datasetSettings": {
            "annotations": [],
            "type": "LakehouseTable",
            "typeProperties": {
                "schema": schema_name,
                "table": table_name,
            },
            "linkedService": {
                "name": lakehouse_display_name,
                "properties": {
                    "type": "Lakehouse",
                    "typeProperties": {
                        "workspaceId": workspace_id,
                        "artifactId": lakehouse_id,
                        "rootFolder": "Tables",
                    },
                    "annotations": [],
                },
            },
        },
    }


def _placeholder_source(source_system, schema_name, table_name):
    """
    No source connection exists yet for this pipeline, so this activity's
    source can't be a real, runnable connector block. Left as a clearly
    labeled placeholder - opening the pipeline in Fabric Studio and
    pointing this Copy activity's source at the real source_system
    connection for {schema}.{table} is expected as a manual next step.
    """
    return {
        "type": "PLACEHOLDER_SOURCE_NOT_CONFIGURED",
        "datasetSettings": {
            "annotations": [
                f"TODO: configure the {source_system or 'source'} connection for "
                f"{schema_name}.{table_name} before running this pipeline."
            ],
        },
    }


def build_pipeline_content(synced_tables, workspace_id, lakehouse_id, lakehouse_display_name, source_system=None):
    """
    synced_tables: the same list DB2_2_Fabric.py's Generator() returns as
    `processed` - dicts with at least {"schema", "table", "layer",
    "load_strategy"}, i.e. exactly the tables that were actually synced
    into the Lakehouse (skipped/errored tables have no business getting a
    pipeline activity pointed at a table that was never created).

    Returns a pipeline-content.json dict ready to hand to
    fabric_api.create_or_update_pipeline().
    """
    by_layer = {}
    for t in synced_tables:
        by_layer.setdefault(t.get("layer") or "(no layer assigned)", []).append(t)

    activities = []
    used_names = set()
    previous_layer_activity_names = []

    for layer in LAYER_ORDER:
        layer_tables = by_layer.get(layer)
        if not layer_tables:
            continue

        this_layer_activity_names = []
        for t in layer_tables:
            schema_name, table_name = t["schema"], t["table"]
            base_name = clean_activity_name(f"Copy_{schema_name}_{table_name}")
            activity_name = base_name
            suffix = 2
            while activity_name in used_names:
                activity_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(activity_name)

            activities.append({
                "name": activity_name,
                "type": "Copy",
                "dependsOn": [
                    {"activity": prev_name, "dependencyConditions": ["Succeeded"]}
                    for prev_name in previous_layer_activity_names
                ],
                "policy": {
                    "timeout": "0.12:00:00",
                    "retry": 0,
                    "retryIntervalInSeconds": 30,
                    "secureOutput": False,
                    "secureInput": False,
                },
                "userProperties": [
                    {"name": "Medallion Layer", "value": layer},
                    {"name": "Load Strategy", "value": t.get("load_strategy") or "Full Load"},
                ],
                "typeProperties": {
                    "source": _placeholder_source(source_system, schema_name, table_name),
                    "sink": _lakehouse_sink(workspace_id, lakehouse_id, lakehouse_display_name, schema_name, table_name),
                },
            })
            this_layer_activity_names.append(activity_name)

        previous_layer_activity_names = this_layer_activity_names

    return {
        "properties": {
            "activities": activities,
            "annotations": [],
        }
    }
