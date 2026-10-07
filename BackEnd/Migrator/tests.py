import json
import os
import shutil
import tempfile
import pandas as pd
from django.test import TestCase
from AI_Agent_Pipeline.src.fabric_json_generator import generate_fabric_json_metadata

class FabricJsonGeneratorTests(TestCase):
    def test_json_generation_success(self):
        # Create mock dataframes
        tables_df = pd.DataFrame([
            {"schema_name": "dbo", "table_name": "Customers", "file_name": "test_db", "full_table_name": "dbo.Customers"},
            {"schema_name": "dbo", "table_name": "Orders", "file_name": "test_db", "full_table_name": "dbo.Orders"}
        ])
        
        columns_df = pd.DataFrame([
            {"SchemaName": "dbo", "TableName": "Customers", "FileName": "test_db", "ColumnName": "CustomerID", "SourceDataType": "int", "TargetDataType": "int", "OrdinalPosition": 1, "IsNullable": False, "IsActive": 1},
            {"SchemaName": "dbo", "TableName": "Customers", "FileName": "test_db", "ColumnName": "CustomerName", "SourceDataType": "varchar(100)", "TargetDataType": "varchar(100)", "OrdinalPosition": 2, "IsNullable": True, "IsActive": 1},
            {"SchemaName": "dbo", "TableName": "Orders", "FileName": "test_db", "ColumnName": "OrderID", "SourceDataType": "int", "TargetDataType": "int", "OrdinalPosition": 1, "IsNullable": False, "IsActive": 1},
            {"SchemaName": "dbo", "TableName": "Orders", "FileName": "test_db", "ColumnName": "CustomerID", "SourceDataType": "int", "TargetDataType": "int", "OrdinalPosition": 2, "IsNullable": False, "IsActive": 1}
        ])
        
        stats_df = pd.DataFrame([
            {"row_count": 100, "schema_name": "dbo", "size_mb": 0.5, "table_name": "Customers", "file_name": "test_db", "table_type": "BASE TABLE"},
            {"row_count": 500, "schema_name": "dbo", "size_mb": 1.2, "table_name": "Orders", "file_name": "test_db", "table_type": "BASE TABLE"}
        ])
        
        dep_df = pd.DataFrame([
            {
                "fk_name": "FK_Orders_CustomerID_Customers",
                "parent_schema": "dbo",
                "parent_table": "Orders",
                "referenced_schema": "dbo",
                "referenced_table": "Customers"
            }
        ])
        
        views_df = pd.DataFrame([
            {"schema_name": "dbo", "view_name": "CustomerOrdersView"}
        ])
        
        procedures_df = pd.DataFrame([
            {"procedure_name": "GetCustomerOrders", "schema_name": "dbo"}
        ])
        
        agent_writeups = "SECTION 1\nMetadata summary.\nSECTION 5\n- Customers: Bronze layer raw table\n- Orders: Silver layer dependent"
        
        # Temp output file
        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, "test_metadata.json")
        
        try:
            # Generate JSON
            data = generate_fabric_json_metadata(
                tables_df=tables_df,
                columns_df=columns_df,
                stats_df=stats_df,
                dep_df=dep_df,
                views_df=views_df,
                procedures_df=procedures_df,
                agent_writeups=agent_writeups,
                output_path=output_path,
                source_hint="sqlserver",
                scan_id="test-scan-uuid"
            )
            
            # Assertions
            self.assertTrue(os.path.exists(output_path))
            
            # Verify file contents
            with open(output_path, "r", encoding="utf-8") as f:
                loaded_data = json.load(f)
                
            self.assertEqual(loaded_data["metadata"]["scan_id"], "test-scan-uuid")
            self.assertEqual(loaded_data["metadata"]["source_platform"], "SQL Server")
            self.assertEqual(loaded_data["metadata"]["total_tables"], 2)
            self.assertEqual(loaded_data["metadata"]["total_columns"], 4)
            self.assertEqual(loaded_data["metadata"]["total_views"], 1)
            self.assertEqual(loaded_data["metadata"]["total_procedures"], 1)
            
            # Validate target datatypes mapping
            customers_obj = [obj for obj in loaded_data["objects"] if obj["name"] == "Customers"][0]
            orders_obj = [obj for obj in loaded_data["objects"] if obj["name"] == "Orders"][0]
            
            self.assertEqual(customers_obj["primary_key"], "CustomerID")
            cust_name_col = [col for col in customers_obj["columns"] if col["name"] == "CustomerName"][0]
            self.assertEqual(cust_name_col["target_datatype"], "STRING")
            
            # Verify topological execution order
            self.assertEqual(loaded_data["execution_plan"]["migration_sequence"], ["Customers", "Orders"])
            self.assertIn("Customers", loaded_data["execution_plan"]["batches"]["Batch 1 (Independent Tables)"])
            self.assertIn("Orders", loaded_data["execution_plan"]["batches"]["Batch 3 (Highly Dependent Tables)"])
            
            print("All test assertions passed successfully!")
            
        finally:
            shutil.rmtree(temp_dir)


class ErDiagramModelTests(TestCase):
    def _table(self, schema, name, columns, pk=None):
        return {
            "type": "table", "schema": schema, "name": name, "primary_key": pk,
            "columns": [{"name": c, "source_datatype": "NUMBER(38,0)"} for c in columns],
        }

    def _links(self, *tables):
        from Migrator.er_diagram import build_er_model
        model = build_er_model({"objects": list(tables)})
        return model, {(r["from"], r["from_column"], r["to"], r["to_column"]) for r in model["relationships"]}

    def test_plural_and_prefixed_table_names(self):
        _, links = self._links(
            self._table("SALES", "CUSTOMERS", ["CUSTOMER_ID"], pk="CUSTOMER_ID"),
            self._table("SALES", "ORDERS", ["ORDER_ID", "CUSTOMER_ID"], pk="ORDER_ID"),
            self._table("cards", "dim_card", ["card_id"], pk="card_id"),
            self._table("cards", "fact_sale", ["sale_id", "card_id"], pk="sale_id"),
        )
        self.assertEqual(links, {
            ("SALES.ORDERS", "CUSTOMER_ID", "SALES.CUSTOMERS", "CUSTOMER_ID"),
            ("cards.fact_sale", "card_id", "cards.dim_card", "card_id"),
        })

    def test_own_key_is_not_a_reference(self):
        _, links = self._links(
            # STG_ORDERS.ORDER_ID names its own table - not a link to ORDERS.
            self._table("SALES", "ORDERS", ["ORDER_ID"], pk="ORDER_ID"),
            self._table("SALES", "STG_ORDERS", ["ORDER_ID"], pk="ORDER_ID"),
            # A key only links to a table keyed the same way.
            self._table("payments", "fact_upi_payment", ["upi_txn_id"], pk="upi_txn_id"),
            self._table("payments", "fact_neft_rtgs", ["payment_id"], pk="payment_id"),
            self._table("crm", "customers", ["customer_id"], pk="customer_id"),
            self._table("crm", "customer_details", ["customer_id"], pk="customer_id"),
        )
        self.assertEqual(links, {("crm.customer_details", "customer_id", "crm.customers", "customer_id")})

    def test_junction_tables_and_least_prefixed_match(self):
        model, links = self._links(
            self._table("dbo", "auth_group", ["id", "name"], pk="id"),
            self._table("dbo", "stg_auth_group", ["id"], pk="id"),
            self._table("dbo", "auth_permission", ["id", "codename"], pk="id"),
            self._table("dbo", "auth_user", ["id", "username"], pk="id"),
            self._table("dbo", "auth_group_permissions", ["id", "group_id", "permission_id"], pk="id"),
            self._table("dbo", "auth_user_groups", ["id", "user_id", "group_id"], pk="id"),
        )
        self.assertEqual(links, {
            ("dbo.auth_group_permissions", "group_id", "dbo.auth_group", "id"),
            ("dbo.auth_group_permissions", "permission_id", "dbo.auth_permission", "id"),
            ("dbo.auth_user_groups", "user_id", "dbo.auth_user", "id"),
            ("dbo.auth_user_groups", "group_id", "dbo.auth_group", "id"),
        })
        permissions = next(t for t in model["tables"] if t["name"] == "auth_group_permissions")
        flags = {c["name"]: (c["pk"], c["fk"]) for c in permissions["columns"]}
        self.assertEqual(flags, {"id": (True, False), "group_id": (False, True), "permission_id": (False, True)})


class DeclaredKeysTests(TestCase):
    def test_attach_declared_keys_groups_composite_keys_in_order(self):
        from Metadata_Scanner.extractors.declared_keys import attach_declared_keys
        schema_map = {"dbo": {"name": "dbo", "tables": [
            {"name": "order_lines", "type": "BASE TABLE"},
            {"name": "orders", "type": "BASE TABLE"},
            {"name": "v_orders", "type": "VIEW"},
        ]}}
        pk_rows = [("dbo", "order_lines", "line_no", 2), ("dbo", "order_lines", "order_id", 1), ("dbo", "orders", "order_id", 1)]
        fk_rows = [("FK_lines_orders", "dbo", "order_lines", "order_id", "dbo", "orders", "order_id", 1)]
        self.assertEqual(attach_declared_keys(schema_map, pk_rows, fk_rows), (2, 1))
        lines, orders, view = schema_map["dbo"]["tables"]
        self.assertEqual(lines["primary_key"], ["order_id", "line_no"])
        self.assertEqual(lines["foreign_keys"], [{
            "name": "FK_lines_orders", "columns": ["order_id"],
            "ref_schema": "dbo", "ref_table": "orders", "ref_columns": ["order_id"],
        }])
        self.assertEqual((orders["primary_key"], orders["foreign_keys"]), (["order_id"], []))
        self.assertNotIn("primary_key", view)

    def test_er_model_prefers_declared_keys(self):
        from Migrator.er_diagram import build_er_model

        def table(name, columns, pk, fks):
            return {"type": "table", "schema": "S", "name": name, "primary_key": columns[0],
                    "declared_primary_key": pk, "declared_foreign_keys": fks,
                    "columns": [{"name": c, "source_datatype": "INT"} for c in columns]}

        model = build_er_model({"objects": [
            table("CUSTOMERS", ["CUSTOMER_ID"], ["CUSTOMER_ID"], []),
            # BUYER_ID would never be inferred from its name; CUSTOMER_ID would be, but isn't declared.
            table("ORDERS", ["ORDER_ID", "BUYER_ID", "CUSTOMER_ID"], ["ORDER_ID"], [
                {"name": "FK_BUYER", "columns": ["BUYER_ID"], "ref_schema": "S", "ref_table": "CUSTOMERS", "ref_columns": ["CUSTOMER_ID"]},
                {"name": "FK_OUTSIDE_SCAN", "columns": ["ORDER_ID"], "ref_schema": "S", "ref_table": "NOT_SCANNED", "ref_columns": ["ID"]},
            ]),
            table("ORDER_LINES", ["ORDER_ID", "LINE_NO"], ["ORDER_ID", "LINE_NO"], []),
        ]})
        self.assertEqual((model["declared_keys_read"], model["relationship_source"]), (True, "declared"))
        self.assertEqual(
            [(r["from"], r["from_column"], r["to"], r["to_column"], r["inferred"]) for r in model["relationships"]],
            [("S.ORDERS", "BUYER_ID", "S.CUSTOMERS", "CUSTOMER_ID", False)],
        )
        lines = next(t for t in model["tables"] if t["name"] == "ORDER_LINES")
        self.assertTrue(lines["primary_key_declared"])
        self.assertEqual([c["pk"] for c in lines["columns"]], [True, True])

    def test_er_model_infers_when_nothing_is_declared(self):
        from Migrator.er_diagram import build_er_model
        model = build_er_model({"objects": [
            {"type": "table", "schema": "S", "name": n, "primary_key": cols[0], "declared_primary_key": [cols[0]],
             "declared_foreign_keys": [], "columns": [{"name": c} for c in cols]}
            for n, cols in (("CUSTOMERS", ["CUSTOMER_ID"]), ("ORDERS", ["ORDER_ID", "CUSTOMER_ID"]))
        ]})
        self.assertEqual((model["declared_keys_read"], model["relationship_source"]), (True, "inferred"))
        self.assertEqual([(r["from_column"], r["inferred"]) for r in model["relationships"]], [("CUSTOMER_ID", True)])

    def test_limit_metadata_tables_preserves_views_and_procedures(self):
        from Migrator.views import _limit_metadata_tables
        raw_metadata = {
            "database": "test_db",
            "schemas": [
                {
                    "name": "dbo",
                    "tables": [
                        {"name": f"Table_{i}", "type": "BASE TABLE", "columns": [{"name": "id"}]}
                        for i in range(10)
                    ] + [
                        {"name": f"View_{i}", "type": "VIEW", "columns": [{"name": "id"}]}
                        for i in range(8)
                    ],
                    "procedures": [{"name": f"sp_{i}"} for i in range(4)],
                    "functions": [{"name": f"fn_{i}"} for i in range(3)],
                    "volumes": [{"name": "vol_1"}],
                }
            ]
        }
        limited = _limit_metadata_tables(raw_metadata, max_tables=5)
        schema = limited["schemas"][0]
        base_tables = [t for t in schema["tables"] if t.get("type") == "BASE TABLE"]
        views = [t for t in schema["tables"] if t.get("type") == "VIEW"]
        self.assertEqual(len(base_tables), 5)
        self.assertEqual(len(views), 8)
        self.assertEqual(len(schema["procedures"]), 4)
        self.assertEqual(len(schema["functions"]), 3)
        self.assertEqual(len(schema["volumes"]), 1)

