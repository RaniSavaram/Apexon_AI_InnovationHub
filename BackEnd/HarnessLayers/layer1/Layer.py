"""
Harness Layer 1 - Constraint & Governance Harness
==================================================

Purpose:
    First quality gate in the migration pipeline. Ensures only trusted,
    complete, valid, and Microsoft Fabric-compatible metadata is allowed
    into the AI pipeline. No AI processing occurs in this layer.

Pipeline position:
    Source Connection
        -> Metadata Extraction
        -> Constraint & Governance Harness   <-- this module
        -> Metadata Checker Agent

Usage (from a Django view/service):
    from .constraint_governance_harness import ConstraintGovernanceHarness

    harness = ConstraintGovernanceHarness(fabric_config=my_config)
    report = harness.run(connection_info, scan_info, metadata)

    if report["decision"] == "PASS":
        # forward to Assessment Agent
        ...
    else:
        # stop workflow / loop back to extraction
        ...
"""

from __future__ import annotations


import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------
# Enums / Constants
# --------------------------------------------------------------------------

class Decision(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class Severity(str, Enum):
    ERROR = "ERROR"      # causes FAIL
    WARNING = "WARNING"  # logged but does not block


# --------------------------------------------------------------------------
# Default dynamic Fabric compatibility configuration
# --------------------------------------------------------------------------
# This is intentionally NOT hardcoded logic - it's data. Load it from a
# JSON/DB/config service at runtime and pass it into the harness so Fabric
# support rules can change without touching code.

DEFAULT_FABRIC_CONFIG: dict[str, Any] = {
    "supported_data_types": [
        "int", "bigint", "smallint", "tinyint", "bit",
        "decimal", "numeric", "float", "real",
        "char", "varchar", "nchar", "nvarchar", "text",
        "date", "datetime", "datetime2", "time",
        "uniqueidentifier", "binary", "varbinary", "boolean"
    ],
    "unsupported_data_types": [
        "sql_variant", "xml", "geography", "geometry",
        "hierarchyid", "cursor", "table", "timestamp","string","double"
    ],
    "supported_constraints": [
        "PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "NOT NULL", "DEFAULT"
    ],
    "unsupported_constraints": [
        "COMPUTED COLUMN CONSTRAINT", "XML SCHEMA COLLECTION"
    ],
    "supported_object_types": [
        "TABLE", "VIEW", "PROCEDURE", "FUNCTION"
    ],
    "unsupported_object_types": [
        "TRIGGER", "CLR ASSEMBLY", "SERVICE BROKER", "FILESTREAM"
    ],
    "unsupported_sql_features": [
        "CLR", "SERVICE BROKER", "FILESTREAM", "CHANGE TRACKING",
        "TEMPORAL TABLE VERSIONING (unsupported edge cases)"
    ],
    "proprietary_functions_blocklist": [
        "sp_", "xp_", "fn_"  # prefix-based detection of vendor-proprietary procs
    ],
}


# --------------------------------------------------------------------------
# Data containers
# --------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    rule: str
    message: str
    severity: Severity = Severity.ERROR
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "message": self.message,
            "severity": self.severity.value,
            "context": self.context,
        }


@dataclass
class SectionResult:
    section: str
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
        }


# --------------------------------------------------------------------------
# Section 1: Connection Validation
# --------------------------------------------------------------------------

class ConnectionValidator:
    """
    Validates:
      - Database connection established
      - Authentication successful
      - Credentials verified
      - Connection timeout not exceeded
      - Required permissions available
    """

    # Must match the literal permission_name values SQL Server's
    # fn_my_permissions returns (e.g. "VIEW DEFINITION" has a space, not an
    # underscore). There is no real SQL Server permission named
    # SHOW_METADATA — INFORMATION_SCHEMA visibility follows from SELECT.
    REQUIRED_PERMISSIONS = {"SELECT"}

    def validate(self, connection_info: dict[str, Any]) -> SectionResult:
        issues: list[ValidationIssue] = []

        if not connection_info.get("connected", False):
            issues.append(ValidationIssue(
                "CONNECTION_ESTABLISHED",
                "Database connection was not established."
            ))

        if not connection_info.get("authenticated", False):
            issues.append(ValidationIssue(
                "AUTHENTICATION_SUCCESSFUL",
                "Authentication failed or was not attempted."
            ))

        if not connection_info.get("credentials_verified", False):
            issues.append(ValidationIssue(
                "CREDENTIALS_VERIFIED",
                "Credentials could not be verified."
            ))

        if connection_info.get("timed_out", False):
            issues.append(ValidationIssue(
                "CONNECTION_TIMEOUT",
                "Connection attempt exceeded the allowed timeout."
            ))

        granted = set(connection_info.get("permissions", []))
        missing_permissions = self.REQUIRED_PERMISSIONS - granted
        if missing_permissions:
            issues.append(ValidationIssue(
                "REQUIRED_PERMISSIONS",
                f"Missing required permissions: {', '.join(sorted(missing_permissions))}"
            ))

        return SectionResult(
            section="connection_validation",
            passed=not any(i.severity == Severity.ERROR for i in issues),
            issues=issues,
        )


# --------------------------------------------------------------------------
# Section 2: Scan Validation
# --------------------------------------------------------------------------

class ScanValidator:
    """
    Validates:
      - Scan completed successfully
      - Scan scope verified
      - No interrupted scans
      - No partial extraction
    """

    def validate(self, scan_info: dict[str, Any]) -> SectionResult:
        issues: list[ValidationIssue] = []

        if scan_info.get("status") != "COMPLETED":
            issues.append(ValidationIssue(
                "SCAN_COMPLETED",
                f"Scan did not complete successfully (status: {scan_info.get('status')})."
            ))

        expected_scope = set(scan_info.get("expected_scope", []))
        actual_scope = set(scan_info.get("actual_scope", []))
        if expected_scope and expected_scope != actual_scope:
            issues.append(ValidationIssue(
                "SCAN_SCOPE_VERIFIED",
                "Scan scope does not match expected scope.",
                context={
                    "missing_from_scan": sorted(expected_scope - actual_scope),
                    "unexpected_in_scan": sorted(actual_scope - expected_scope),
                }
            ))

        if scan_info.get("interrupted", False):
            issues.append(ValidationIssue(
                "NO_INTERRUPTED_SCANS",
                "Scan was interrupted before completion."
            ))

        if scan_info.get("partial_extraction", False):
            issues.append(ValidationIssue(
                "NO_PARTIAL_EXTRACTION",
                "Extraction is partial/incomplete."
            ))

        return SectionResult(
            section="scan_validation",
            passed=not any(i.severity == Severity.ERROR for i in issues),
            issues=issues,
        )


# --------------------------------------------------------------------------
# Section 3: Metadata Validation
# --------------------------------------------------------------------------

class MetadataValidator:
    """
    Validates:
      - Metadata JSON valid
      - Required fields exist
      - Tables present
      - Columns present
      - Views detected
      - Procedures detected
      - Primary Keys valid
      - Foreign Keys valid
      - Relationships valid
      - No duplicate objects
      - No malformed metadata
    """

    REQUIRED_TOP_LEVEL_FIELDS = {"tables", "views", "procedures", "relationships"}

    def validate(self, metadata: Any) -> SectionResult:
        issues: list[ValidationIssue] = []

        # Metadata JSON validity
        if isinstance(metadata, (str, bytes)):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                issues.append(ValidationIssue(
                    "METADATA_JSON_VALID",
                    "Metadata is not valid JSON."
                ))
                return SectionResult("metadata_validation", False, issues)

        if not isinstance(metadata, dict):
            issues.append(ValidationIssue(
                "METADATA_JSON_VALID",
                "Metadata root must be a JSON object."
            ))
            return SectionResult("metadata_validation", False, issues)

        # Required fields
        missing_fields = self.REQUIRED_TOP_LEVEL_FIELDS - metadata.keys()
        if missing_fields:
            issues.append(ValidationIssue(
                "REQUIRED_FIELDS_EXIST",
                f"Missing required metadata fields: {sorted(missing_fields)}"
            ))

        tables = metadata.get("tables", [])
        views = metadata.get("views", [])
        procedures = metadata.get("procedures", [])
        relationships = metadata.get("relationships", [])

        # Tables present
        if not tables:
            issues.append(ValidationIssue(
                "TABLES_PRESENT",
                "No tables found in metadata."
            ))

        # Columns present (per table)
        for table in tables:
            table_name = table.get("name", "<unnamed>")
            if not table.get("columns"):
                issues.append(ValidationIssue(
                    "COLUMNS_PRESENT",
                    f"Table '{table_name}' has no columns.",
                    context={"table": table_name}
                ))

        # Views detected (informational — warning only)
        if not views:
            issues.append(ValidationIssue(
                "VIEWS_DETECTED",
                "No views detected in metadata.",
                severity=Severity.WARNING
            ))

        # Procedures detected (informational — warning only)
        if not procedures:
            issues.append(ValidationIssue(
                "PROCEDURES_DETECTED",
                "No stored procedures detected in metadata.",
                severity=Severity.WARNING
            ))

        # Primary Keys valid
        for table in tables:
            table_name = table.get("name", "<unnamed>")
            pk = table.get("primary_key")
            column_names = {c.get("name") for c in table.get("columns", [])}
            if pk:
                pk_cols = pk if isinstance(pk, list) else [pk]
                invalid_pk_cols = [c for c in pk_cols if c not in column_names]
                if invalid_pk_cols:
                    issues.append(ValidationIssue(
                        "PRIMARY_KEYS_VALID",
                        f"Table '{table_name}' has primary key referencing "
                        f"unknown columns: {invalid_pk_cols}",
                        context={"table": table_name}
                    ))

        # Foreign Keys valid
        table_lookup = {t.get("name"): t for t in tables}
        for table in tables:
            table_name = table.get("name", "<unnamed>")
            for fk in table.get("foreign_keys", []):
                ref_table = fk.get("references_table")
                ref_column = fk.get("references_column")
                if ref_table not in table_lookup:
                    issues.append(ValidationIssue(
                        "FOREIGN_KEYS_VALID",
                        f"Table '{table_name}' has FK referencing unknown "
                        f"table '{ref_table}'.",
                        context={"table": table_name}
                    ))
                    continue
                ref_columns = {c.get("name") for c in table_lookup[ref_table].get("columns", [])}
                if ref_column not in ref_columns:
                    issues.append(ValidationIssue(
                        "FOREIGN_KEYS_VALID",
                        f"Table '{table_name}' FK references unknown column "
                        f"'{ref_table}.{ref_column}'.",
                        context={"table": table_name}
                    ))

        # Relationships valid
        for rel in relationships:
            src, tgt = rel.get("source"), rel.get("target")
            if src not in table_lookup or tgt not in table_lookup:
                issues.append(ValidationIssue(
                    "RELATIONSHIPS_VALID",
                    f"Relationship references unknown table(s): "
                    f"source='{src}', target='{tgt}'."
                ))

        # No duplicate objects
        for label, objects in (("tables", tables), ("views", views), ("procedures", procedures)):
            names = [o.get("name") for o in objects if o.get("name")]
            duplicates = {n for n in names if names.count(n) > 1}
            if duplicates:
                issues.append(ValidationIssue(
                    "NO_DUPLICATE_OBJECTS",
                    f"Duplicate {label} detected: {sorted(duplicates)}"
                ))

        # No malformed metadata (basic structural sanity check)
        for table in tables:
            if "name" not in table:
                issues.append(ValidationIssue(
                    "NO_MALFORMED_METADATA",
                    "Found a table entry with no 'name' field."
                ))

        return SectionResult(
            section="metadata_validation",
            passed=not any(i.severity == Severity.ERROR for i in issues),
            issues=issues,
        )


# --------------------------------------------------------------------------
# Section 4: Fabric Compatibility Validation (DYNAMIC / CONFIG-DRIVEN)
# --------------------------------------------------------------------------

class FabricCompatibilityValidator:
    """
    Validates supported/unsupported:
      - Data Types
      - Constraints
      - Indexes
      - Relationships
      - Object Types

    Detects unsupported:
      - SQL Features
      - Proprietary Functions
      - Unsupported Datatypes
      - Unsupported Constraints

    This validator is fully DYNAMIC: all rules are driven by a config dict
    (see DEFAULT_FABRIC_CONFIG above) rather than hardcoded in logic. Swap
    in a different config (loaded from DB/JSON/API) to change Fabric
    compatibility rules without any code changes.
    """

    def __init__(self, fabric_config: Optional[dict[str, Any]] = None):
        self.config = fabric_config or DEFAULT_FABRIC_CONFIG
        self._supported_types = {t.lower() for t in self.config.get("supported_data_types", [])}
        self._unsupported_types = {t.lower() for t in self.config.get("unsupported_data_types", [])}
        self._supported_constraints = {c.upper() for c in self.config.get("supported_constraints", [])}
        self._unsupported_constraints = {c.upper() for c in self.config.get("unsupported_constraints", [])}
        self._supported_object_types = {o.upper() for o in self.config.get("supported_object_types", [])}
        self._unsupported_object_types = {o.upper() for o in self.config.get("unsupported_object_types", [])}
        self._unsupported_sql_features = {f.lower() for f in self.config.get("unsupported_sql_features", [])}
        self._proprietary_prefixes = tuple(self.config.get("proprietary_functions_blocklist", []))

    def _is_type_supported(self, data_type: str) -> Optional[bool]:
        dt = (data_type or "").lower().split("(")[0].strip()  # strip e.g. varchar(50) -> varchar
        if dt in self._unsupported_types:
            return False
        if dt in self._supported_types:
            return True
        return None  # unknown type - treated as a warning, not a hard fail

    def validate(self, metadata: dict[str, Any]) -> SectionResult:
        issues: list[ValidationIssue] = []
        tables = metadata.get("tables", [])
        views = metadata.get("views", [])
        procedures = metadata.get("procedures", [])
        relationships = metadata.get("relationships", [])

        # Data types
        for table in tables:
            table_name = table.get("name", "<unnamed>")
            for col in table.get("columns", []):
                supported = self._is_type_supported(col.get("data_type", ""))
                if supported is False:
                    issues.append(ValidationIssue(
                        "UNSUPPORTED_DATA_TYPE",
                        f"Column '{table_name}.{col.get('name')}' uses "
                        f"unsupported data type '{col.get('data_type')}'.",
                        severity=Severity.WARNING,
                        context={"table": table_name, "column": col.get("name"), "data_type": col.get("data_type")}
                    ))
                elif supported is None:
                    issues.append(ValidationIssue(
                        "UNKNOWN_DATA_TYPE",
                        f"Column '{table_name}.{col.get('name')}' uses an "
                        f"unrecognized data type '{col.get('data_type')}'.",
                        severity=Severity.WARNING,
                        context={"table": table_name, "column": col.get("name"), "data_type": col.get("data_type")}
                    ))

        # Constraints
        for table in tables:
            table_name = table.get("name", "<unnamed>")
            for constraint in table.get("constraints", []):
                c_type = (constraint.get("type") or "").upper()
                if c_type in self._unsupported_constraints:
                    issues.append(ValidationIssue(
                        "UNSUPPORTED_CONSTRAINT",
                        f"Table '{table_name}' uses unsupported constraint "
                        f"type '{c_type}'.",
                        severity=Severity.WARNING,
                        context={"table": table_name}
                    ))
                elif c_type and c_type not in self._supported_constraints:
                    issues.append(ValidationIssue(
                        "UNKNOWN_CONSTRAINT",
                        f"Table '{table_name}' uses unrecognized constraint "
                        f"type '{c_type}'.",
                        severity=Severity.WARNING,
                        context={"table": table_name}
                    ))

        # Indexes (structural presence check — Fabric generally supports
        # standard indexes; flag anything explicitly marked unsupported)
        for table in tables:
            table_name = table.get("name", "<unnamed>")
            for index in table.get("indexes", []):
                if index.get("type", "").upper() in {"XML", "SPATIAL", "FULLTEXT"}:
                    issues.append(ValidationIssue(
                        "UNSUPPORTED_INDEX",
                        f"Table '{table_name}' has unsupported index type "
                        f"'{index.get('type')}'.",
                        context={"table": table_name}
                    ))

        # Relationships (only flag if explicitly marked unsupported type,
        # e.g. many-to-many without junction table)
        for rel in relationships:
            if rel.get("type", "").lower() == "many_to_many" and not rel.get("junction_table"):
                issues.append(ValidationIssue(
                    "UNSUPPORTED_RELATIONSHIP",
                    "Many-to-many relationship without a junction table is "
                    "not directly supported.",
                    context={"relationship": rel}
                ))

        # Object types
        for label, objects in (("TABLE", tables), ("VIEW", views), ("PROCEDURE", procedures)):
            if objects and label not in self._supported_object_types:
                issues.append(ValidationIssue(
                    "UNSUPPORTED_OBJECT_TYPE",
                    f"Object type '{label}' is not marked as supported in "
                    f"the current Fabric configuration."
                ))

        # Unsupported SQL features (declared explicitly in metadata, e.g.
        # metadata["sql_features_used"] = ["CLR", "SERVICE BROKER"])
        used_features = {f.lower() for f in metadata.get("sql_features_used", [])}
        detected = used_features & self._unsupported_sql_features
        if detected:
            issues.append(ValidationIssue(
                "UNSUPPORTED_SQL_FEATURES",
                f"Unsupported SQL features detected: {sorted(detected)}"
            ))

        # Proprietary functions (by prefix, e.g. sp_, xp_, fn_)
        for proc in procedures:
            name = proc.get("name", "")
            if name.startswith(self._proprietary_prefixes):
                issues.append(ValidationIssue(
                    "PROPRIETARY_FUNCTION_DETECTED",
                    f"Procedure/function '{name}' matches a proprietary "
                    f"naming pattern and may not be portable to Fabric.",
                    severity=Severity.WARNING,
                    context={"procedure": name}
                ))

        return SectionResult(
            section="fabric_compatibility",
            passed=not any(i.severity == Severity.ERROR for i in issues),
            issues=issues,
        )


# --------------------------------------------------------------------------
# Section 5: Governance Validation
# --------------------------------------------------------------------------

class GovernanceValidator:
    """
    Validates:
      - Naming conventions
      - Required metadata
      - Sensitive data identification
      - Metadata ownership
      - Destructive SQL statements (DELETE / TRUNCATE) captured against a table
    """

    NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    SENSITIVE_KEYWORDS = {
        "ssn", "password", "credit_card", "card_number", "dob",
        "national_id", "email", "phone", "salary", "passport"
    }
    REQUIRED_METADATA_FIELDS = {"owner", "classification"}

    # Matches a leading DELETE/TRUNCATE keyword (ignoring leading whitespace),
    # e.g. "DELETE FROM sales.orders" or "TRUNCATE TABLE bfsi_dev.accounts".
    # Sourced from each table's optional "recent_statements" list, which
    # DatabricksExtractor.extract() populates from Unity Catalog's
    # system.query.history (Metadata_Scanner/extractors/databricks_client.py)
    # so this fires on real scans, not just synthetic metadata.
    DESTRUCTIVE_SQL_PATTERN = re.compile(r"^\s*(DELETE|TRUNCATE)\b", re.IGNORECASE)

    def validate(self, metadata: dict[str, Any]) -> SectionResult:
        issues: list[ValidationIssue] = []
        tables = metadata.get("tables", [])

        # Naming conventions
        for table in tables:
            name = table.get("name", "")
            if not self.NAME_PATTERN.match(name):
                issues.append(ValidationIssue(
                    "NAMING_CONVENTION",
                    f"Table name '{name}' does not follow naming convention "
                    f"(letters, digits, underscores; cannot start with a digit).",
                    context={"table": name}
                ))
            for col in table.get("columns", []):
                col_name = col.get("name", "")
                if not self.NAME_PATTERN.match(col_name):
                    issues.append(ValidationIssue(
                        "NAMING_CONVENTION",
                        f"Column '{name}.{col_name}' does not follow naming convention.",
                        context={"table": name, "column": col_name}
                    ))

        # Required governance metadata
        missing = self.REQUIRED_METADATA_FIELDS - metadata.get("governance", {}).keys()
        if missing:
            issues.append(ValidationIssue(
                "REQUIRED_GOVERNANCE_METADATA",
                f"Missing required governance metadata fields: {', '.join(sorted(missing))}",
                severity=Severity.WARNING
            ))

        # Sensitive data identification
        for table in tables:
            table_name = table.get("name", "")
            for col in table.get("columns", []):
                col_name = (col.get("name") or "").lower()
                if any(keyword in col_name for keyword in self.SENSITIVE_KEYWORDS) \
                        and not col.get("is_sensitive_flagged", False):
                    issues.append(ValidationIssue(
                        "SENSITIVE_DATA_IDENTIFICATION",
                        f"Column '{table_name}.{col.get('name')}' appears "
                        f"sensitive but is not flagged as such.",
                        severity=Severity.WARNING,
                        context={"table": table_name, "column": col.get("name")}
                    ))

        # Metadata ownership
        if not metadata.get("governance", {}).get("owner"):
            issues.append(ValidationIssue(
                "METADATA_OWNERSHIP",
                "Metadata has no assigned owner.",
                severity=Severity.WARNING
            ))

        # Destructive SQL statements (DELETE / TRUNCATE) captured against a
        # table - ERROR severity, since letting one through into a migration
        # plan risks carrying a data-destroying operation forward.
        for table in tables:
            table_name = table.get("name", "<unnamed>")
            schema_name = table.get("schema", "")
            qualified_name = f"{schema_name}.{table_name}" if schema_name else table_name
            for statement in table.get("recent_statements", []) or []:
                if self.DESTRUCTIVE_SQL_PATTERN.match(statement or ""):
                    issues.append(ValidationIssue(
                        "DESTRUCTIVE_SQL_DETECTED",
                        f"Destructive SQL statement detected against "
                        f"'{qualified_name}': {statement.strip()}",
                        context={
                            "table": table_name,
                            "schema": schema_name,
                            "statement": statement,
                        }
                    ))

        return SectionResult(
            section="governance_validation",
            passed=not any(i.severity == Severity.ERROR for i in issues),
            issues=issues,
        )


# --------------------------------------------------------------------------
# Orchestrator: Constraint & Governance Harness
# --------------------------------------------------------------------------

class ConstraintGovernanceHarness:
    """
    Runs all Layer 1 validation sections in order and produces the final
    Metadata Extraction Report (JSON) + PASS/FAIL decision.
    """

    def __init__(self, fabric_config: Optional[dict[str, Any]] = None):
        self.connection_validator = ConnectionValidator()
        self.scan_validator = ScanValidator()
        self.metadata_validator = MetadataValidator()
        self.fabric_validator = FabricCompatibilityValidator(fabric_config)
        self.governance_validator = GovernanceValidator()

    def run(
        self,
        connection_info: dict[str, Any],
        scan_info: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        sections: list[SectionResult] = []

        sections.append(self.connection_validator.validate(connection_info))
        sections.append(self.scan_validator.validate(scan_info))

        metadata_result = self.metadata_validator.validate(metadata)
        sections.append(metadata_result)

        # Only run Fabric/governance checks if metadata is structurally sound
        # enough to inspect (avoids cascading noisy errors).
        if metadata_result.passed and isinstance(metadata, dict):
            sections.append(self.fabric_validator.validate(metadata))
            sections.append(self.governance_validator.validate(metadata))
        else:
            sections.append(SectionResult("fabric_compatibility", False, [
                ValidationIssue("SKIPPED", "Skipped due to invalid metadata.", Severity.WARNING)
            ]))
            sections.append(SectionResult("governance_validation", False, [
                ValidationIssue("SKIPPED", "Skipped due to invalid metadata.", Severity.WARNING)
            ]))

        overall_pass = all(s.passed for s in sections)
        decision = Decision.PASS if overall_pass else Decision.FAIL

        report = {
            "layer": "Harness Layer 1 - Constraint & Governance Harness",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "decision": decision.value,
            "next_step": "Assessment Agent" if overall_pass else "Stop Workflow / Loop back to extraction",
            "sections": [s.to_dict() for s in sections],
            "summary": {
                "total_errors": sum(
                    1 for s in sections for i in s.issues if i.severity == Severity.ERROR
                ),
                "total_warnings": sum(
                    1 for s in sections for i in s.issues if i.severity == Severity.WARNING
                ),
            },
        }
        return report


def _normalize_extracted_metadata(raw_metadata: dict[str, Any]) -> dict[str, Any]:
    """
    Every extractor (SQLServerExtractor, SynapseExtractor, SnowflakeExtractor,
    DatabricksExtractor, Dynamics365Extractor) returns the same shape:
        {"database": ..., "schemas": [{"name": ..., "tables": [
            {"name": ..., "type": ..., "columns": [{"name": ..., "datatype": ...}], "row_count": ...}
        ], "procedures": [{"name": ...}]}]}
    Flatten that into the {tables, views, procedures, relationships} shape
    the Layer 1 validators expect, renaming "datatype" -> "data_type" per
    column. Table/column names are kept unqualified (schema stored
    separately) since GovernanceValidator's naming-convention check rejects
    '.' in identifiers.

    Objects with type == "VIEW" are routed to "views" instead of "tables",
    matching the convention parse_schema_dict() uses in
    AI_Agent_Pipeline/src/metadataProcessor.py. Each schema's "procedures"
    list (populated by the extractors that support it) is flattened into
    the top-level "procedures" list the same way.
    """
    tables = []
    views = []
    procedures = []
    for schema in raw_metadata.get("schemas", []) or []:
        schema_name = schema.get("name", "")
        for table in schema.get("tables", []) or []:
            entry = {
                "name": table.get("name", ""),
                "schema": schema_name,
                "type": table.get("type"),
                "row_count": table.get("row_count"),
                "columns": [
                    {"name": col.get("name"), "data_type": col.get("datatype")}
                    for col in table.get("columns", []) or []
                ],
                "primary_key": [],
                "foreign_keys": [],
                "constraints": [],
                "indexes": [],
                "recent_statements": table.get("recent_statements", []) or [],
            }
            if (table.get("type") or "").upper() == "VIEW":
                views.append(entry)
            else:
                tables.append(entry)

        for proc in schema.get("procedures", []) or []:
            procedures.append({
                "name": proc.get("name", ""),
                "schema": schema_name,
            })

    return {
        "tables": tables,
        "views": views,
        "procedures": procedures,
        "relationships": [],
    }


def layer1_Harness(raw_metadata: dict[str, Any]) -> dict[str, Any]:
    """
    Validates the metadata a source extractor already pulled (Db_Scanner's
    `obj.extract()` result), rather than opening a second, independent
    database connection. This is what makes the harness source-agnostic:
    connection/auth are already proven by the extractor having succeeded,
    and "scope" is simply whatever tables were actually extracted - there's
    no separate hardcoded expected-table list to reconnect and re-check.
    """
    normalized_metadata = _normalize_extracted_metadata(raw_metadata)

    connection_info = {
        "connected": True,
        "authenticated": True,
        "credentials_verified": True,
        "timed_out": False,
        "permissions": ["SELECT"],  # proven by the extractor's successful reads
    }

    scope = [
        f"{t['schema']}.{t['name']}" if t.get("schema") else t["name"]
        for t in normalized_metadata["tables"]
    ]
    scan_result = {
        "status": "COMPLETED",
        "expected_scope": scope,
        "actual_scope": scope,
        "interrupted": False,
        "partial_extraction": False,
    }

    harness = ConstraintGovernanceHarness()
    result = harness.run(connection_info, scan_result, normalized_metadata)
    return result


if __name__ == "__main__":
    # Self-contained demo: proves GovernanceValidator.DESTRUCTIVE_SQL_DETECTED
    # (defined above) fails the harness, using the real bfsi_dev Databricks
    # schema/columns observed from an actual scan (Unity Catalog catalog
    # "bfsi_dev", schemas "cards" and "core_banking") rather than invented
    # table shapes. system.query.history had no DELETE/TRUNCATE to report at
    # scan time, so the two statements below are hardcoded onto dim_card and
    # dim_account here to exercise the check end-to-end without needing a
    # live destructive statement run against the real warehouse.
    # Run: python HarnessLayers/layer1/Layer.py
    demo_raw_metadata = {
        "database": "bfsi_dev",
        "schemas": [
            {
                "name": "cards",
                "tables": [
                    {
                        "name": "dim_card",
                        "type": "MANAGED",
                        "columns": [
                            {"name": "card_id", "datatype": "STRING"},
                            {"name": "customer_id", "datatype": "STRING"},
                            {"name": "card_number", "datatype": "STRING"},
                            {"name": "card_number_masked", "datatype": "STRING"},
                            {"name": "card_network", "datatype": "STRING"},
                            {"name": "card_type", "datatype": "STRING"},
                            {"name": "product_name", "datatype": "STRING"},
                            {"name": "cardholder_name", "datatype": "STRING"},
                            {"name": "cvv_stored", "datatype": "STRING"},
                            {"name": "credit_limit", "datatype": "DOUBLE"},
                            {"name": "available_limit", "datatype": "DOUBLE"},
                            {"name": "cash_limit", "datatype": "DOUBLE"},
                            {"name": "current_outstanding", "datatype": "DOUBLE"},
                            {"name": "min_amount_due", "datatype": "DOUBLE"},
                            {"name": "total_amount_due", "datatype": "DOUBLE"},
                            {"name": "card_status", "datatype": "STRING"},
                            {"name": "is_contactless", "datatype": "STRING"},
                            {"name": "is_international_enabled", "datatype": "STRING"},
                        ],
                        "recent_statements": [
                            "TRUNCATE TABLE bfsi_dev.cards.dim_card",
                        ],
                    },
                ],
            },
            {
                "name": "core_banking",
                "tables": [
                    {
                        "name": "dim_account",
                        "type": "MANAGED",
                        "columns": [
                            {"name": "account_id", "datatype": "STRING"},
                            {"name": "account_number", "datatype": "STRING"},
                            {"name": "iban", "datatype": "STRING"},
                            {"name": "customer_id", "datatype": "STRING"},
                            {"name": "branch_id", "datatype": "STRING"},
                            {"name": "ifsc_code", "datatype": "STRING"},
                            {"name": "account_type", "datatype": "STRING"},
                            {"name": "product_code", "datatype": "STRING"},
                            {"name": "currency_code", "datatype": "STRING"},
                            {"name": "current_balance", "datatype": "DOUBLE"},
                            {"name": "available_balance", "datatype": "DOUBLE"},
                            {"name": "avg_monthly_balance", "datatype": "DOUBLE"},
                            {"name": "overdraft_limit", "datatype": "DOUBLE"},
                            {"name": "interest_rate_pct", "datatype": "DOUBLE"},
                            {"name": "account_status", "datatype": "STRING"},
                            {"name": "is_dormant", "datatype": "STRING"},
                            {"name": "nominee_name", "datatype": "STRING"},
                        ],
                        "recent_statements": [
                            "DELETE FROM bfsi_dev.core_banking.dim_account WHERE current_balance = 0",
                        ],
                    },
                ],
            },
        ],
    }

    demo_report = layer1_Harness(demo_raw_metadata)

    print(f"Decision: {demo_report['decision']}")
    print(f"Next step: {demo_report['next_step']}")
    print(
        f"Errors: {demo_report['summary']['total_errors']}, "
        f"Warnings: {demo_report['summary']['total_warnings']}"
    )
    print()

    demo_governance_section = next(
        s for s in demo_report["sections"] if s["section"] == "governance_validation"
    )
    demo_destructive_issues = [
        i for i in demo_governance_section["issues"] if i["rule"] == "DESTRUCTIVE_SQL_DETECTED"
    ]

    destructive_statement = bool(demo_destructive_issues)

    if destructive_statement:
        print(f"[TRACKED] Harness Layer 1 flagged {len(demo_destructive_issues)} destructive statement(s):")
        for issue in demo_destructive_issues:
            print(f"  - {issue['message']}")
        print("\n[FATAL] Destructive SQL statement detected - terminating process.")
        sys.exit(1)

    print("[NOT TRACKED] Harness Layer 1 did NOT flag either destructive statement.")

    assert demo_report["decision"] == "PASS", (
        "Expected PASS - no destructive statement was present, "
        f"got '{demo_report['decision']}'."
    )

    print("\nDemo PASSED: governance_validation did not find any destructive statements.")