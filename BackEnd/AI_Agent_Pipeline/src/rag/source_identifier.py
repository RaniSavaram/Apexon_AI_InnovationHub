"""
Identifies which source database platform a metadata payload came from.

Deliberately does not assume a fixed metadata structure: different
extractors (and future ones) may describe the source differently. This
resolves the source type through several fallbacks, in order of
confidence, and defaults to a generic "unknown" identifier rather than
raising, so an unrecognized/variable metadata shape never breaks the
pipeline - it just falls back to source-agnostic (common Fabric only)
guidance.
"""

# Synonyms map to the identifiers used as keys in rag.registry.SOURCE_RAG_FILES.
# Mirrors the normalization Migrator/views.py already does for the
# `source` field it receives from the frontend, so the same hint values
# work here.
_SOURCE_SYNONYMS = {
    "databricks": "databricks",
    "sql": "sqlserver",
    "sqlserver": "sqlserver",
    "sql server": "sqlserver",
    "mssql": "sqlserver",
    "microsoft sql server": "sqlserver",
    "synapse": "synapse",
    "snowflake": "snowflake",
    "dynamics365": "dynamics365",
    "dynamics 365": "dynamics365",
    "d365": "dynamics365",
}

# Keys that a metadata dict might self-describe its source under, checked
# in case a future/variable extractor payload embeds this directly instead
# of (or in addition to) relying on an external hint.
_SELF_DESCRIBING_KEYS = ("source_type", "platform", "vendor", "source_platform", "engine", "db_type")


def _normalize(value: str):
    if not value:
        return None
    normalized = str(value).strip().lower()
    return _SOURCE_SYNONYMS.get(normalized)


def identify_source_type(metadata: dict = None, hint: str = None) -> str:
    """
    Resolves a normalized source type identifier (e.g. "databricks") from,
    in order of precedence:
      1. An explicit hint (e.g. the `source` value the scan request/API
         already knows, before the metadata dict was ever built).
      2. A self-describing field inside the metadata dict, for extractors
         that choose to include one.
      3. Lightweight structural heuristics over whatever shape the
         metadata happens to have.
    Falls back to "unknown" (never raises) so pipelines without a
    resolvable source still run with common Fabric guidance only.
    """
    resolved = _normalize(hint)
    if resolved:
        return resolved

    if isinstance(metadata, dict):
        for key in _SELF_DESCRIBING_KEYS:
            resolved = _normalize(metadata.get(key))
            if resolved:
                return resolved

        resolved = _heuristic_from_structure(metadata)
        if resolved:
            return resolved

    return "unknown"


def _heuristic_from_structure(metadata: dict):
    """
    Best-effort structural guesses for sources that don't (yet) tag
    themselves. Only covers a couple of unambiguous shape signals -
    intentionally conservative so it doesn't mislabel unfamiliar sources.
    """
    database_field = str(metadata.get("database", "")).lower()
    if "dynamics.com" in database_field or "crm.dynamics" in database_field:
        return "dynamics365"

    for schema in metadata.get("schemas", []) or []:
        if not isinstance(schema, dict):
            continue
        if str(schema.get("name", "")).lower() == "dataverse":
            return "dynamics365"
        for table in schema.get("tables", []) or []:
            if not isinstance(table, dict):
                continue
            if str(table.get("type", "")).upper() == "ENTITY":
                return "dynamics365"

    return None
