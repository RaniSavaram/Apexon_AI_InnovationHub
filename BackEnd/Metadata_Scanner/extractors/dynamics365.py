import json
from pathlib import Path

import requests

from Metadata_Scanner.extractors.base_extractor import BaseExtractor


class Dynamics365Extractor(BaseExtractor):
    """
    Dynamics 365 does NOT expose a raw SQL connection the way the other
    extractors assume - under the hood it's Microsoft Dataverse, and the
    supported way to read metadata is the Dataverse Web API over HTTPS,
    authenticated against Azure AD (Entra ID) with one of two OAuth2 flows:

    1. Client-credentials flow (needs an app registration): org URL +
       tenant/client id/secret. Used for production setups that have an
       Application User and app registration configured.
    2. Resource-owner-password flow (no app registration or client secret
       needed): org URL + the user's own Dynamics 365 username/password.
       This is the practical option for free/developer/trial environments
       where no app registration exists yet - it authenticates as
       Microsoft's own well-known multi-tenant Dynamics CRM client, which
       doesn't require a secret. Note Azure AD blocks this flow for
       accounts with MFA/Security Defaults/Conditional Access enabled,
       which is common on some tenants.

    "Tables" = Dataverse entities (e.g. "account", "contact", custom
    entities like "new_project"). "Columns" = entity attributes.

    Field mapping:
      Creds.get_servername()       -> the org URL, e.g. "https://yourorg.crm.dynamics.com"
      Creds.get_extra("tenant_id")     -> Azure AD tenant ID (required for flow 1, optional for flow 2)
      Creds.get_extra("client_id")     -> app registration (client) ID (required for flow 1, optional for flow 2)
      Creds.get_extra("client_secret") -> app registration client secret (required for flow 1 only)
      Creds.get_username() / get_password() -> Dynamics 365 sign-in (required for flow 2 only)

    For flow 1, the app registration needs an Application User created in
    Dynamics 365 (Settings > Users > Application Users) with a security
    role granting at least read access, and API permissions for
    "Dynamics CRM > user_impersonation" (admin-consented).

    Install: pip install requests   (already installed - used elsewhere in the project)
    """

    # Microsoft's well-known, multi-tenant "Dynamics CRM" native app
    # registration - a public client, so it doesn't require (or accept) a
    # client secret. Used as the default client_id for the resource-owner-
    # password flow when the caller hasn't registered their own app.
    DEFAULT_PUBLIC_CLIENT_ID = "51f81489-12ee-4a9e-aaae-a2591f45987d"

    def __init__(self, Creds):
        self.org_url = (Creds.get_servername() or "").strip().rstrip("/")
        self.tenant_id = (Creds.get_extra("tenant_id") or "").strip()
        self.client_id = (Creds.get_extra("client_id") or "").strip()
        self.client_secret = (Creds.get_extra("client_secret") or "").strip()
        self.username = (Creds.get_username() or "").strip()
        self.password = Creds.get_password() or ""
        self.access_token = None

    def connect(self):

        print("Org URL   :", repr(self.org_url))
        print("Tenant ID :", repr(self.tenant_id))
        print("Client ID :", repr(self.client_id))

        if not self.org_url:
            raise ValueError("Dynamics 365 org URL is empty (Server field).")
        if "powerapps.com" in self.org_url or "powerplatform.microsoft.com" in self.org_url:
            # Common mistake: pasting the maker portal / admin center link
            # (e.g. https://make.powerapps.com/environments/<id>/home)
            # instead of the actual Dataverse Web API base URL. Azure AD
            # would otherwise accept the token request and only fail later
            # with a cryptic AADSTS500011 "resource principal not found".
            raise ValueError(
                "Organization URL looks like a Power Apps/Power Platform portal link, not "
                "the Dataverse environment URL. Use the API base URL instead, e.g. "
                "'https://yourorg.crm.dynamics.com' - find it in the Power Platform Admin "
                "Center under the environment's Details ('Environment URL'), or inside the "
                "Dynamics 365 app under Settings > Customizations > Developer Resources "
                "('Instance Web API' base URL)."
            )
        using_client_secret = bool(self.client_secret)
        using_username_password = bool(self.username and self.password)

        if not using_client_secret and not using_username_password:
            raise ValueError(
                "Provide either a Client Secret (app registration flow) or a "
                "Username and Password (free/developer account flow) to "
                "connect to Dynamics 365."
            )

        if using_client_secret:
            if not self.tenant_id:
                raise ValueError("Tenant ID is empty (extra['tenant_id']).")
            if not self.client_id:
                raise ValueError("Client ID is empty (extra['client_id']).")
            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            token_data = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": f"{self.org_url}/.default",
            }
        else:
            token_url = f"https://login.microsoftonline.com/{self.tenant_id or 'common'}/oauth2/v2.0/token"
            token_data = {
                "grant_type": "password",
                "client_id": self.client_id or self.DEFAULT_PUBLIC_CLIENT_ID,
                "username": self.username,
                "password": self.password,
                "scope": f"{self.org_url}/.default",
            }

        response = requests.post(token_url, data=token_data, timeout=15)
        if not response.ok:
            # Azure AD puts the actual reason (invalid secret, unknown
            # tenant, app not found, missing admin consent, etc.) in the
            # JSON body as error/error_description - raise_for_status()
            # alone only surfaces the generic "400 Client Error" status
            # line, which hides that detail from the user.
            try:
                error_body = response.json()
                detail = error_body.get("error_description") or error_body.get("error") or response.text
            except ValueError:
                detail = response.text
            raise ValueError(f"Dynamics 365 authentication failed: {detail}")
        self.access_token = response.json()["access_token"]

    def close(self):
        # Stateless REST calls - nothing to tear down.
        self.access_token = None

    def _api_get(self, path, params=None):
        url = f"{self.org_url}/api/data/v9.2/{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if not response.ok:
            # Dataverse puts the actual reason (missing privilege, user not
            # provisioned in this environment, no license, etc.) in the JSON
            # body under error.message - raise_for_status() alone only
            # surfaces the generic "403 Client Error" status line.
            try:
                detail = response.json().get("error", {}).get("message") or response.text
            except ValueError:
                detail = response.text
            raise ValueError(
                f"Dynamics 365 API request to '{path}' failed ({response.status_code}): {detail}"
            )
        return response.json()

    def extract(self, output_file="data/metadata.json"):

        self.connect()

        metadata = {
            "database": self.org_url,
            "schemas": []
        }

        # Dataverse has no schema concept above the org itself, so
        # everything goes in a single "dataverse" pseudo-schema - kept
        # for consistency with the shared metadata.json shape.
        schema_name = "dataverse"
        schema_map = {schema_name: {"name": schema_name, "tables": []}}

        # List entities (custom, unmanaged only - i.e. tables actually
        # built by the maker, not the ~800 built-in system entities on a
        # stock environment, and not entities like "aaduser" that ship
        # pre-flagged IsCustomEntity=true as part of a Microsoft-managed
        # solution such as the Teams/AAD virtual-entity data source. Drop
        # the $filter entirely if you want every entity.
        entities_result = self._api_get(
            "EntityDefinitions",
            params={
                "$select": "LogicalName,EntitySetName,DisplayName",
                "$filter": "IsCustomEntity eq true and IsManaged eq false",
            },
        )
        entities = entities_result.get("value", [])

        for entity in entities:

            logical_name = entity["LogicalName"]

            table_object = {
                "name": logical_name,
                "type": "ENTITY",
                "columns": []
            }

            # MaxLength/Precision live only on derived attribute types
            # (StringAttributeMetadata, DecimalAttributeMetadata, ...), not
            # on the base AttributeMetadata type that /Attributes returns
            # polymorphically - selecting them here 400s, so they're left
            # out and the columns below just get None for those fields.
            attrs_result = self._api_get(
                f"EntityDefinitions(LogicalName='{logical_name}')/Attributes",
                params={"$select": "LogicalName,AttributeType,RequiredLevel"},
            )

            for attr in attrs_result.get("value", []):
                required_level = (attr.get("RequiredLevel") or {}).get("Value", "None")
                table_object["columns"].append({
                    "name": attr.get("LogicalName"),
                    "datatype": attr.get("AttributeType"),
                    "max_length": attr.get("MaxLength"),
                    "precision": attr.get("Precision"),
                    "scale": None,
                    "nullable": "NO" if required_level in ("SystemRequired", "ApplicationRequired") else "YES",
                })

            # Row count via $count on the entity set.
            try:
                entity_set_name = entity.get("EntitySetName", logical_name)
                count_result = self._api_get(
                    entity_set_name,
                    params={"$select": logical_name + "id", "$top": 1, "$count": "true"},
                )
                table_object["row_count"] = count_result.get("@odata.count")
            except Exception as e:
                print(f"[WARNING] Could not get row count for {logical_name}: {e}")
                table_object["row_count"] = None

            schema_map[schema_name]["tables"].append(table_object)

        metadata["schemas"] = list(schema_map.values())

        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as fp:
            json.dump(metadata, fp, indent=4)

        self.close()
        return metadata
