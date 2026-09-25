"""
Demo Configuration: Unsupported / Corrupt Data Types Negative Use Case
======================================================================

Allows easily enabling and injecting data types unsupported by Microsoft Fabric OneLake
(such as 'hierarchyid', 'geometry', 'sql_variant', or 'custom_blob') for demonstrating
the negative validation scenario.

When enabled:
    Harness Layer 1 & 2 validation will detect the incompatible type and flag:
    UNSUPPORTED_DATA_TYPE: Column uses data type 'hierarchyid' incompatible with Fabric OneLake.

Usage:
    - In Code: Set DEMO_INJECT_UNSUPPORTED_DATA_TYPE = True below.
    - Or Environment Variable: Set DEMO_INJECT_UNSUPPORTED_DATA_TYPE=true in .env
"""

import os
from typing import Any, Dict

# ==============================================================================
# DEMO TOGGLE 1: Unsupported / Corrupt Data Types (Harness Layer 1 & 2)
# True  -> Injects unsupported data type into table metadata for the demo.
# False -> Normal clean run (Positive Case / Default).
# ==============================================================================
DEMO_INJECT_UNSUPPORTED_DATA_TYPE = (
    os.getenv("DEMO_INJECT_UNSUPPORTED_DATA_TYPE", "true").lower() in ("true", "1", "yes")
)

# Supported options: "hierarchyid", "geometry", "sql_variant", "custom_blob"
DEMO_UNSUPPORTED_DATA_TYPE = os.getenv("DEMO_UNSUPPORTED_DATA_TYPE", "hierarchyid")
DEMO_UNSUPPORTED_COLUMN_NAME = os.getenv("DEMO_UNSUPPORTED_COLUMN_NAME", "org_node")


# ==============================================================================
# DEMO TOGGLE 2: Medallion Architecture Generator Rule Breach (Harness Layer 2)
# True  -> Injects prohibited medallion reference into agent summary to test Layer 2
# False -> Normal clean run (Positive Case / Default).
# ==============================================================================
DEMO_INJECT_MEDALLION_LEAKAGE = (
    os.getenv("DEMO_INJECT_MEDALLION_LEAKAGE", "false").lower() in ("true", "1", "yes")
)


def apply_demo_injections(raw_metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Injects the unsupported data type column into the first table of the scanned metadata
    when DEMO_INJECT_UNSUPPORTED_DATA_TYPE is True.
    """
    if not isinstance(raw_metadata, dict):
        return raw_metadata

    if DEMO_INJECT_UNSUPPORTED_DATA_TYPE:
        schemas = raw_metadata.get("schemas", [])
        if schemas:
            for schema in schemas:
                tables = schema.get("tables", [])
                if tables:
                    target_table = tables[0]
                    existing_cols = [c.get("name") for c in target_table.get("columns", [])]
                    if DEMO_UNSUPPORTED_COLUMN_NAME not in existing_cols:
                        target_table.setdefault("columns", []).append({
                            "name": DEMO_UNSUPPORTED_COLUMN_NAME,
                            "datatype": DEMO_UNSUPPORTED_DATA_TYPE,
                            "max_length": None,
                            "precision": None,
                            "scale": None,
                            "nullable": "YES"
                        })
                    break

    return raw_metadata

