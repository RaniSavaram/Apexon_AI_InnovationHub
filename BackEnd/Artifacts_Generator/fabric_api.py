"""
Thin client over the two Azure surfaces DB2_2_Fabric.py (and any future
target-specific generator) needs to talk to Microsoft Fabric:

  - OneLake (ADLS Gen2-compatible) - for the actual Delta table writes,
    via deltalake/delta-rs. Only a bearer token is needed; deltalake does
    the storage I/O itself.
  - The Fabric REST API (https://api.fabric.microsoft.com/v1) - for
    workspace-level item management, i.e. finding or creating the
    Lakehouse that will hold those tables.

Auth is DefaultAzureCredential in both cases (same credential chain
already used by SQL_2_Fabric.py's get_onelake_token()): environment
variables, managed identity, Azure CLI login, etc. - whichever is
available in the environment this runs in. No secrets are hardcoded here;
configure the credential via the standard Azure environment variables or
`az login` if running interactively.
"""
import base64
import json
import time
import uuid

import requests
from azure.identity import DefaultAzureCredential

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"

_credential = None


def _build_credential():
    # This installed azure-identity version's DefaultAzureCredential chain
    # is, in order: Environment, WorkloadIdentity, ManagedIdentity,
    # SharedTokenCache, VisualStudioCode, AzureCli, AzurePowerShell,
    # AzureDeveloperCli, then (default-included) a Windows-native
    # BrokerCredential (WAM) as the last resort before the
    # default-excluded InteractiveBrowserCredential. "Couldn't complete the
    # operation due to a system update. Close out this connection, sign in
    # again, and retry the operation." is BrokerCredential/pymsalruntime's
    # own error text for a stale OS-level broker/account-cache entry - one
    # a Windows update can invalidate, and which stays broken across
    # process restarts until Windows itself re-syncs it. It only surfaces
    # when every earlier credential in the chain also failed to produce a
    # token, which is why this was intermittent. SharedTokenCacheCredential
    # (the other Windows OS-cache-backed source, earlier in the chain) is
    # excluded too for the same reason. Skipping both leaves Azure CLI -
    # confirmed working (`az account show`) - as the effective source.
    return DefaultAzureCredential(
        exclude_shared_token_cache_credential=True,
        exclude_broker_credential=True,
    )


def _get_credential():
    global _credential
    if _credential is None:
        _credential = _build_credential()
    return _credential


def _get_token(scope):
    """
    Wraps credential.get_token() with a one-time reset-and-retry: if
    whichever source DefaultAzureCredential locked onto on its first
    successful call (it remembers and reuses only that one for the rest of
    this process's life) later goes stale mid-process, this discards the
    cached credential and retries the full chain once from scratch, rather
    than the whole app staying stuck on the same broken source until
    someone manually restarts it.
    """
    global _credential
    try:
        return _get_credential().get_token(scope).token
    except Exception:
        _credential = None
        return _get_credential().get_token(scope).token


def get_onelake_token():
    """Bearer token scoped for OneLake/ADLS Gen2 storage access."""
    return _get_token("https://storage.azure.com/.default")


def get_fabric_api_token():
    """Bearer token scoped for the Fabric REST API (workspace/item management)."""
    return _get_token("https://api.fabric.microsoft.com/.default")


def get_fabric_sql_token():
    """
    Azure AD token scoped for SQL (database.windows.net) - what a Fabric
    Warehouse's SQL analytics endpoint expects for AAD access-token auth
    (the same scope Azure SQL/Synapse use, since Fabric Warehouses speak
    the same TDS wire protocol). Only needed by fabric_warehouse_sql.py,
    which is a separate token scope from both get_onelake_token() (storage)
    and get_fabric_api_token() (item management).
    """
    return _get_token("https://database.windows.net/.default")


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _poll_lro(response, token, timeout_seconds=300):
    """
    Fabric item-creation calls that can't complete synchronously return
    202 Accepted with a `Location` header pointing at an operation-status
    resource, and a `Retry-After` header. This polls that resource until
    the operation reaches a terminal state, then fetches `/result` for the
    created item's body (id, displayName, etc.).
    """
    location = response.headers.get("Location")
    if not location:
        return response.json()

    retry_after = float(response.headers.get("Retry-After", 2))
    deadline = time.time() + timeout_seconds

    while True:
        poll_resp = requests.get(location, headers=_headers(token))
        if not poll_resp.ok:
            raise RuntimeError(f"Fabric operation-status poll failed: {poll_resp.status_code} {poll_resp.text}")
        body = poll_resp.json()
        status = body.get("status")

        if status == "Succeeded":
            result_resp = requests.get(f"{location}/result", headers=_headers(token))
            if result_resp.status_code == 200 and result_resp.content:
                return result_resp.json()
            return body

        if status == "Failed":
            raise RuntimeError(f"Fabric operation failed: {body.get('error', body)}")

        if time.time() > deadline:
            raise TimeoutError(f"Fabric operation timed out after {timeout_seconds}s: {location}")

        time.sleep(retry_after)


def list_items(workspace_id, item_type, token):
    """Yields every item of `item_type` (e.g. 'lakehouses') in a workspace, across pages."""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_type}"

    while url:
        resp = requests.get(url, headers=_headers(token))
        if not resp.ok:
            raise RuntimeError(f"Failed listing {item_type} in workspace {workspace_id}: {resp.status_code} {resp.text}")
        body = resp.json()

        for item in body.get("value", []):
            yield item

        if body.get("continuationUri"):
            url = body["continuationUri"]
        elif body.get("continuationToken"):
            url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_type}?continuationToken={body['continuationToken']}"
        else:
            url = None


def get_item(workspace_id, item_type, item_id, token):
    """Returns the full item body (id, displayName, properties, ...) for one item."""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_type}/{item_id}"
    resp = requests.get(url, headers=_headers(token))
    if not resp.ok:
        raise RuntimeError(f"Failed getting {item_type}/{item_id}: {resp.status_code} {resp.text}")
    return resp.json()


def create_onelake_directory(workspace_id, lakehouse_id, directory_path, onelake_token):
    """
    Creates an empty directory in OneLake via the ADLS Gen2 "Path - Create"
    REST API - e.g. directory_path="Files/cards/raw_files" - the closest
    Fabric Lakehouse equivalent to a Unity Catalog Volume: unstructured
    file storage backed by the same OneLake location a Lakehouse's Tables/
    folder lives in, just under Files/ instead. Idempotent: re-running
    against a directory that already exists still returns success.
    """
    clean_path = directory_path.strip("/")
    url = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/{clean_path}"
    resp = requests.put(
        url,
        params={"resource": "directory"},
        headers={"Authorization": f"Bearer {onelake_token}", "Content-Length": "0"},
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed creating OneLake directory '{directory_path}': {resp.status_code} {resp.text}")


def get_or_create_item(workspace_id, item_type, display_name, token):
    """
    Returns the id of the item named `display_name` of type `item_type`
    (e.g. 'lakehouses') in `workspace_id`, creating it first if it doesn't
    exist yet. Matching on displayName is case-insensitive, since Fabric
    item names are effectively case-preserving-but-not-case-sensitive in
    practice for this kind of lookup.
    """
    for item in list_items(workspace_id, item_type, token):
        if (item.get("displayName") or "").strip().lower() == display_name.strip().lower():
            return item["id"]

    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_type}"
    resp = requests.post(url, headers=_headers(token), json={"displayName": display_name})

    if resp.status_code == 201:
        return resp.json()["id"]

    if resp.status_code == 202:
        result = _poll_lro(resp, token)
        return result["id"]

    # Not raise_for_status() here - that raises with just the status line
    # ("400 Client Error: Bad Request for url: ...") and throws away
    # resp.text, which is where Fabric actually explains what was wrong
    # with the request body.
    raise RuntimeError(
        f"Unexpected response creating {item_type} '{display_name}': "
        f"{resp.status_code} {resp.text}"
    )


def _definition_part(path, obj):
    """
    Encodes one JSON-object part of an item's `definition.parts` array -
    Fabric's item-definition API always wants base64-encoded file content
    plus the virtual path it lives at within the item's definition (mirrors
    the on-disk layout Fabric Git integration uses for the same item
    types). For a plain-text/code part (e.g. a notebook's
    notebook-content.py source), use _definition_part_text() instead - this
    one JSON-serializes `obj`, which would mangle raw source text.
    """
    payload = base64.b64encode(json.dumps(obj, indent=2).encode("utf-8")).decode("ascii")
    return {"path": path, "payload": payload, "payloadType": "InlineBase64"}


def _definition_part_text(path, text):
    """Same as _definition_part(), but for a raw text/code part (e.g. a
    notebook's notebook-content.py source) rather than a JSON object."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"path": path, "payload": payload, "payloadType": "InlineBase64"}


def _platform_metadata(platform_type, display_name):
    """
    The `.platform` part every Fabric item definition ships alongside its
    content part(s) - same shape (schema URL, metadata.type/displayName,
    config.version/logicalId) regardless of item type. `platform_type` is
    the item's platform-metadata type label (e.g. "Notebook") - not
    necessarily the same string as the REST API's plural URL path segment
    (e.g. "notebooks"); see create_or_update_item_definition()'s
    `platform_type` parameter.
    """
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": platform_type, "displayName": display_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }


def create_or_update_item_definition(workspace_id, item_type, display_name, parts, token, platform_type=None):
    """
    Shared by every item type whose content is created/updated via the
    Fabric item-definition API (Pipelines, Notebooks, and any future
    definition-based item type) rather than plain property fields: finds
    an existing item named `display_name` and overwrites its definition,
    or creates a new item with that definition if none exists yet.

    `parts` is the item-type-specific content parts (e.g.
    pipeline-content.json, notebook-content.py) WITHOUT the trailing
    `.platform` part - that's appended here since every item type needs
    one, just with its own type label inside. `item_type` is the REST
    API's plural URL path segment (e.g. "dataPipelines", "notebooks");
    `platform_type` is the singular label the `.platform` part's
    metadata.type actually needs (e.g. "Notebook" for notebooks - Fabric
    rejects "notebooks" there with an "Invalid input parameter: Type"
    error). Defaults to `item_type` when omitted, which happens to be
    correct for dataPipelines (the only caller before notebooks needed
    this too).

    Returns the item's id.
    """
    existing_id = None
    for item in list_items(workspace_id, item_type, token):
        if (item.get("displayName") or "").strip().lower() == display_name.strip().lower():
            existing_id = item["id"]
            break

    definition = {"parts": parts + [_definition_part(".platform", _platform_metadata(platform_type or item_type, display_name))]}

    if existing_id:
        url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_type}/{existing_id}/updateDefinition"
        resp = requests.post(url, headers=_headers(token), json={"definition": definition})
        if resp.status_code == 202:
            _poll_lro(resp, token)
        elif resp.status_code not in (200, 204):
            raise RuntimeError(
                f"Unexpected response updating {item_type} '{display_name}' (id={existing_id}): "
                f"{resp.status_code} {resp.text}"
            )
        return existing_id

    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_type}"
    resp = requests.post(url, headers=_headers(token), json={"displayName": display_name, "definition": definition})

    if resp.status_code == 201:
        return resp.json()["id"]

    if resp.status_code == 202:
        result = _poll_lro(resp, token)
        return result["id"]

    # Not raise_for_status() here - see the comment in get_or_create_item()
    # above; resp.text carries Fabric's actual validation error message.
    raise RuntimeError(
        f"Unexpected response creating {item_type} '{display_name}': "
        f"{resp.status_code} {resp.text}"
    )


def create_or_update_pipeline(workspace_id, display_name, pipeline_content, token):
    """
    Creates (or overwrites, if one with this name already exists) a Fabric
    Data Pipeline item from `pipeline_content` (a pipeline-content.json-
    shaped dict, i.e. {"properties": {"activities": [...]}} - the same
    Data Factory/Fabric pipeline JSON schema the Fabric Studio pipeline
    canvas edits). Returns the pipeline item's id.
    """
    return create_or_update_item_definition(
        workspace_id, "dataPipelines", display_name,
        [_definition_part("pipeline-content.json", pipeline_content)],
        token,
    )


def create_or_update_notebook(workspace_id, display_name, notebook_py_source, token):
    """
    Creates (or overwrites) a Fabric Notebook item from `notebook_py_source`
    - a full "Fabric notebook source" formatted Python string (the
    `# Fabric notebook source` / `# CELL ********************` / `# META`
    marker format Fabric's Git integration and item-definition API use for
    notebooks; a plain .ipynb JSON part is NOT accepted here - it fails
    with a "PyToIPynbFailure" conversion error). Returns the notebook
    item's id.
    """
    return create_or_update_item_definition(
        workspace_id, "notebooks", display_name,
        [_definition_part_text("notebook-content.py", notebook_py_source)],
        token,
        platform_type="Notebook",
    )


def run_notebook_job(workspace_id, item_id, token, timeout_seconds=900, poll_interval_seconds=5):
    """
    Triggers a Fabric Notebook item's on-demand "RunNotebook" job and polls
    it to a terminal state. Returns the final job-instance body (dict with
    at least "status" and "failureReason"). Raises TimeoutError if it
    doesn't reach a terminal state within timeout_seconds, or RuntimeError
    if the job itself ends Failed/Cancelled/Deduped (rather than
    Completed) - the caller decides what to do with either, same pattern
    as _poll_lro() above for item-creation LROs.
    """
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{item_id}/jobs/instances?jobType=RunNotebook"
    resp = requests.post(url, headers=_headers(token), json={})
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Failed starting notebook run for item {item_id}: {resp.status_code} {resp.text}")

    location = resp.headers.get("Location")
    if not location:
        raise RuntimeError(f"Notebook run for item {item_id} started but returned no job-instance Location header.")

    deadline = time.time() + timeout_seconds
    while True:
        poll_resp = requests.get(location, headers=_headers(token))
        if not poll_resp.ok:
            raise RuntimeError(f"Failed polling notebook job status: {poll_resp.status_code} {poll_resp.text}")
        body = poll_resp.json()
        status = body.get("status")

        if status == "Completed":
            return body
        if status in ("Failed", "Cancelled", "Deduped"):
            raise RuntimeError(f"Notebook run ended with status '{status}': {body.get('failureReason') or body}")

        if time.time() > deadline:
            raise TimeoutError(f"Notebook run for item {item_id} timed out after {timeout_seconds}s (last status: {status})")

        time.sleep(poll_interval_seconds)


