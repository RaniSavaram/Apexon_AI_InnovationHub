"""
Registry of source-specific RAG knowledge bases, plus the one common
Microsoft Fabric knowledge base that applies regardless of source.

To add support for a new source database's RAG in the future (e.g.
Dynamics 365), only two things are needed:
  1. Drop a new markdown knowledge file under `rag/knowledge/`.
  2. Add one entry to SOURCE_RAG_FILES below mapping the source's
     identifier to that file.

No other pipeline code needs to change - Agent 1 (source metadata agent)
looks up the appropriate knowledge base dynamically through this registry.
"""
import os

from .knowledge_base import get_knowledge_base

_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "knowledge")

# Common Fabric knowledge - always loaded, independent of source platform.
COMMON_FABRIC_RAG_FILE = os.path.join(_KNOWLEDGE_DIR, "common_fabric_rag.md")

# Source-specific knowledge, keyed by normalized source type identifier.
# Add new source types here as their RAG knowledge files are authored.
SOURCE_RAG_FILES = {
    "databricks": os.path.join(_KNOWLEDGE_DIR, "databricks_to_fabric_rag.md"),
    "sqlserver": os.path.join(_KNOWLEDGE_DIR, "sqlserver_to_fabric_rag.md"),
    # "dynamics365": os.path.join(_KNOWLEDGE_DIR, "dynamics365_to_fabric_rag.md"),
}


def get_common_fabric_kb():
    """Returns the always-on common Microsoft Fabric knowledge base."""
    return get_knowledge_base(COMMON_FABRIC_RAG_FILE)


def get_source_kb(source_type: str):
    """
    Returns the source-specific knowledge base for the given (already
    normalized) source_type identifier, or None if no source-specific RAG
    has been authored for that source yet. Callers should treat None as
    "fall back to common Fabric knowledge only" rather than as an error,
    so unrecognized/unregistered sources degrade gracefully instead of
    breaking the pipeline.
    """
    if not source_type:
        return None
    path = SOURCE_RAG_FILES.get(source_type)
    if not path:
        return None
    return get_knowledge_base(path)


def available_sources():
    """Lists the source types that currently have a dedicated RAG."""
    return sorted(SOURCE_RAG_FILES.keys())
