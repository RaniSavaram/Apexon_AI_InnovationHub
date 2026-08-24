"""
Persists saved connection details per source in a local temp file outside the
repository, so the app can remember a user's connection without committing
secrets to GitHub or workspace files.

This file is intentionally kept outside the project directory so deployment
platforms or GitHub commits do not include credential material.
"""
import json
import os
import tempfile

from django.conf import settings

SAVED_CONNECTIONS_PATH = os.environ.get(
    "DB_SCANNER_SAVED_CONNECTIONS_PATH",
    os.path.join(tempfile.gettempdir(), "db_scanner_saved_connections.json"),
)


def _load_all() -> dict:
    if not os.path.exists(SAVED_CONNECTIONS_PATH):
        return {}
    try:
        with open(SAVED_CONNECTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_all(connections: dict) -> None:
    os.makedirs(os.path.dirname(SAVED_CONNECTIONS_PATH), exist_ok=True)
    with open(SAVED_CONNECTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(connections, f, indent=2)


def _as_profile_list(value) -> list:
    """Normalizes a source's stored value (legacy single dict, or the
    current list-of-profiles shape) into a list, so callers never need to
    branch on which format an existing file was written in."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def save_connection(source: str, connection: dict) -> None:
    """Upserts `connection` into the source's profile list, keyed by
    server - connecting again to a previously-saved server updates that
    profile in place rather than growing duplicates."""
    if not source:
        return
    connections = _load_all()
    profiles = _as_profile_list(connections.get(source))
    server = connection.get("server")
    profiles = [p for p in profiles if p.get("server") != server]
    profiles.append(connection)
    connections[source] = profiles
    _write_all(connections)


def get_saved_connection(source: str) -> dict | None:
    """Most recently saved profile for `source` - back-compat single-result
    lookup for callers that only want one connection to pre-fill with."""
    if not source:
        return None
    profiles = _as_profile_list(_load_all().get(source))
    return profiles[-1] if profiles else None


def get_saved_connections(source: str) -> list:
    """All saved profiles for `source`, oldest first, so the frontend can
    offer a picker instead of just the last-used connection."""
    if not source:
        return []
    return _as_profile_list(_load_all().get(source))
