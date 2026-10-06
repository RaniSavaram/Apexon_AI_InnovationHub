"""
Builds the entity-relationship model behind the UI's "ER Diagrams" tab
from a scan's <source>_Fabric_Migration_Metadata.json (written by
fabric_json_generator.py at the end of Agents_PipeLine).

Keys declared in the source database (read by the extractors, see
Metadata_Scanner/extractors/declared_keys.py) come first:
  - Primary key: a table's declared primary key; failing that, the
    "primary_key" fabric_json_generator.py guessed from column names.
  - Relationships: the declared foreign keys ("inferred": False, drawn as
    solid lines). Only when the scan has none at all - keys couldn't be
    read, or the database just doesn't declare them - are relationships
    inferred ("inferred": True, dashed lines): *_id / *_key columns matched
    to a table named after them, singular or plural, bare or prefixed
    (ORDERS.CUSTOMER_ID -> CUSTOMERS, FACT_SALE.CARD_ID -> DIM_CARD).

The metadata JSON's own "relationships" list is deliberately not used:
metadataProcessor.py misses plurals (so a typical Snowflake schema gets
none) and matches any name suffix (payment_id -> FACT_LOAN_REPAYMENT).
"""


def _name_variants(base):
    variants = {base, base + "s", base + "es"}
    if base.endswith("y"):
        variants.add(base[:-1] + "ies")
    return variants


def _match_tier(table_name, base):
    """0 = exact (CUSTOMERS for customer), 1 = prefixed (DIM_CUSTOMER), None = no match."""
    name = table_name.lower()
    variants = _name_variants(base)
    if name in variants:
        return 0
    if any(name.endswith("_" + v) for v in variants):
        return 1
    return None


def _key_base(column_name):
    col = column_name.lower()
    for suffix in ("_id", "_key"):
        if col.endswith(suffix) and len(col) > len(suffix):
            return col[: -len(suffix)]
    return None


def build_er_model(fabric_metadata):
    tables = {}
    declared_fks = {}
    keys_read = False
    for obj in fabric_metadata.get("objects", []):
        if obj.get("type") != "table":
            continue
        schema, name = obj.get("schema") or "dbo", obj.get("name")
        if not name:
            continue
        table_id = f"{schema}.{name}"
        declared_pk = obj.get("declared_primary_key")
        keys_read = keys_read or isinstance(declared_pk, list)
        declared_fks[table_id] = obj.get("declared_foreign_keys") or []
        tables[table_id] = {
            "id": table_id,
            "schema": schema,
            "name": name,
            "row_count": obj.get("row_count"),
            "primary_key": declared_pk[0] if declared_pk else obj.get("primary_key"),
            "primary_key_columns": declared_pk or ([obj["primary_key"]] if obj.get("primary_key") else []),
            "primary_key_declared": bool(declared_pk),
            "columns": [
                {
                    "name": col.get("name"),
                    "type": col.get("source_datatype") or col.get("target_datatype") or "",
                    "nullable": col.get("nullable", True),
                }
                for col in obj.get("columns", [])
                if col.get("name")
            ],
        }

    def column_named(table, column_name):
        wanted = column_name.lower()
        return next((c["name"] for c in table["columns"] if c["name"].lower() == wanted), None)

    relationships = []
    fk_columns = set()
    for from_id, fks in declared_fks.items():
        for fk in fks:
            to_id = f"{fk.get('ref_schema')}.{fk.get('ref_table')}"
            # A foreign key can point at a table this scan didn't include
            # (MAX_SCAN_TABLES); there is nothing to draw it to.
            if to_id not in tables:
                continue
            relationships.append({
                "from": from_id,
                "from_column": ", ".join(fk.get("columns") or []),
                "to": to_id,
                "to_column": ", ".join(fk.get("ref_columns") or []),
                "name": fk.get("name"),
                "inferred": False,
            })
            fk_columns.update((from_id, c.lower()) for c in fk.get("columns") or [])

    has_declared_fks = any(declared_fks.values())
    for from_id, table in ({} if has_declared_fks else tables).items():
        own_key = (table["primary_key"] or "").lower()
        for col in table["columns"]:
            base = _key_base(col["name"])
            if not base:
                continue
            is_own_key = col["name"].lower() == own_key
            # A table's key named after the table itself (ORDERS.ORDER_ID,
            # STG_ORDERS.ORDER_ID) identifies it and references nothing.
            if is_own_key and _match_tier(table["name"], base) is not None:
                continue
            # Best candidates first: exact name over prefixed name, and
            # within each, tables keyed on this very column.
            ranked = {}
            for to_id, target in tables.items():
                if to_id == from_id:
                    continue
                tier = _match_tier(target["name"], base)
                if tier is None:
                    continue
                same_key = (target["primary_key"] or "").lower() == col["name"].lower()
                # Any other table key only references a table keyed the same
                # way - a 1:1 extension like CUSTOMER_DETAILS.CUSTOMER_ID.
                if is_own_key and not same_key:
                    continue
                ranked.setdefault(tier * 2 + (0 if same_key else 1), []).append(to_id)
            if ranked:
                best = ranked[min(ranked)]
                # Among equally good matches the least-prefixed name wins
                # (AUTH_GROUP over STG_AUTH_GROUP); same-named tables in
                # different schemas all stay.
                shortest = min(len(tables[to_id]["name"]) for to_id in best)
                for to_id in best:
                    if len(tables[to_id]["name"]) != shortest:
                        continue
                    relationships.append({
                        "from": from_id,
                        "from_column": col["name"],
                        "to": to_id,
                        "to_column": column_named(tables[to_id], col["name"]) or tables[to_id]["primary_key"],
                        "inferred": True,
                    })
                    fk_columns.add((from_id, col["name"].lower()))

    for table_id, table in tables.items():
        pk_columns = {c.lower() for c in table.pop("primary_key_columns")}
        for col in table["columns"]:
            col["pk"] = col["name"].lower() in pk_columns
            col["fk"] = (table_id, col["name"].lower()) in fk_columns

    meta = fabric_metadata.get("metadata", {})
    return {
        "source": meta.get("source_platform"),
        "database": fabric_metadata.get("source", {}).get("database_name"),
        "generated_at": meta.get("generated_at"),
        # Whether the extractor could read the source's key catalog at all,
        # and where the relationships below came from.
        "declared_keys_read": keys_read,
        "relationship_source": "declared" if has_declared_fks else "inferred",
        "tables": list(tables.values()),
        "relationships": relationships,
    }
