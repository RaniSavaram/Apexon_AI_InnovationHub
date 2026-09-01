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
import time

import requests
from azure.identity import DefaultAzureCredential

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"

_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def get_onelake_token():
    """Bearer token scoped for OneLake/ADLS Gen2 storage access."""
    return _get_credential().get_token("https://storage.azure.com/.default").token


def get_fabric_api_token():
    """Bearer token scoped for the Fabric REST API (workspace/item management)."""
    return _get_credential().get_token("https://api.fabric.microsoft.com/.default").token


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
        poll_resp.raise_for_status()
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
        resp.raise_for_status()
        body = resp.json()

        for item in body.get("value", []):
            yield item

        if body.get("continuationUri"):
            url = body["continuationUri"]
        elif body.get("continuationToken"):
            url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_type}?continuationToken={body['continuationToken']}"
        else:
            url = None


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

    resp.raise_for_status()
    raise RuntimeError(
        f"Unexpected response creating {item_type} '{display_name}': "
        f"{resp.status_code} {resp.text}"
    )
