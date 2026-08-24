import json
from pathlib import Path

import requests

from Metadata_Scanner.extractors.base_extractor import BaseExtractor


class Dynamics365Extractor(BaseExtractor):
    """
    Dynamics 365 does NOT expose a raw SQL connection the way the other
    extractors assume - under the hood it's Microsoft Dataverse, and the
    supported way to read metadata is the Dataverse Web API over HTTPS,
    authenticated with an Azure AD (Entra ID) app registration using the
    OAuth2 client-credentials flow. There's no username/password here.

    "Tables" = Dataverse entities (e.g. "account", "contact", custom
    entities like "new_project"). "Columns" = entity attributes.

    Field mapping:
      Creds.get_servername()       -> the org URL, e.g. "https://yourorg.crm.dynamics.com"
      Creds.get_extra("tenant_id")     -> required, Azure AD tenant ID
      Creds.get_extra("client_id")     -> required, app registration (client) ID
      Creds.get_extra("client_secret") -> required, app registration client secret

    The app registration needs an Application User created in Dynamics 365
    (Settings > Users > Application Users) with a security role granting at
    least read access, and API permissions for
    "Dynamics CRM > user_impersonation" (admin-consented).

    Install: pip install requests   (already installed - used elsewhere in the project)
    """

    def __init__(self, Creds):
        self.org_url = (Creds.get_servername() or "").rstrip("/")
        self.tenant_id = Creds.get_extra("tenant_id")
        self.client_id = Creds.get_extra("client_id")
        self.client_secret = Creds.get_extra("client_secret")
        self.access_token = None

    def connect(self):

        print("Org URL   :", repr(self.org_url))
        print("Tenant ID :", repr(self.tenant_id))
        print("Client ID :", repr(self.client_id))

        if not self.org_url:
            raise ValueError("Dynamics 365 org URL is empty (Server field).")
        if not self.tenant_id:
            raise ValueError("Tenant ID is empty (extra['tenant_id']).")
        if not self.client_id:
            raise ValueError("Client ID is empty (extra['client_id']).")
        if not self.client_secret:
            raise ValueError("Client secret is empty (extra['client_secret']).")

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": f"{self.org_url}/.default",
            },
            timeout=15,
        )
        response.raise_for_status()
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
        response.raise_for_status()
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

        # List entities (custom entities only, to avoid pulling in the
        # ~800 built-in system entities on a stock environment). Drop the
        # IsCustomEntity filter if you want everything.
        entities_result = self._api_get(
            "EntityDefinitions",
            params={
                "$select": "LogicalName,EntitySetName,DisplayName",
                "$filter": "IsCustomEntity eq true",
            },
        )
        entities = entities_result.get("value", [])

        # Same one-table-per-schema sampling used elsewhere - here that
        # means just the first entity found, since there's only one
        # pseudo-schema.
        if entities:
            entities = [entities[0]]

        for entity in entities:

            logical_name = entity["LogicalName"]

            table_object = {
                "name": logical_name,
                "type": "ENTITY",
                "columns": []
            }

            attrs_result = self._api_get(
                f"EntityDefinitions(LogicalName='{logical_name}')/Attributes",
                params={"$select": "LogicalName,AttributeType,MaxLength,Precision,RequiredLevel"},
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
