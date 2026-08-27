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
