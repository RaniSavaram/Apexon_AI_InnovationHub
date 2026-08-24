import os
import sys
import time
import traceback
import pandas as pd
from datetime import datetime
from pathlib import Path

# Add src to python path just in case
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from AI_Agent_Pipeline.src import (
    AzureAIOrchestrator,
    collect_metadata,
    metadata_to_dataframes,
    create_table_summary_document,
    create_migration_plan_document
)
from Logs import Logs

# Reconfigure stdout to use UTF-8 just in case
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass


def Agents_PipeLine(metadata: dict = None, source_hint: str = None):
    """
    metadata: the dict a Metadata_Scanner extractor's extract() just
    returned for the source that was scanned this request. When given,
    it's converted directly into dataframes so the Assessment Report
    reflects exactly that scan. When omitted (standalone/manual runs),
    falls back to scanning whatever CSV/JSON files are sitting in the
    data/ directory - the original behavior.

    source_hint: the normalized source platform identifier (e.g.
    "databricks") already known from the scan request, forwarded to the
    orchestrator so it can ground the agents with the matching RAG
    knowledge base instead of guessing the source from raw metadata.
    """
    print(f"==================================================\nstarting Database Migration Assessment Pipeline\n==================================================")

    # 1. Directories setup

    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir.parent / "data"
    output_dir = base_dir / "output"

    if not os.path.exists(output_dir):
        print(f"[INFO] Creating output directory: {output_dir}")
        os.makedirs(output_dir)

    # 2. Collect Metadata - prefer the live scan's metadata dict when given,
    # otherwise fall back to scanning data/ for CSV/JSON uploads.
    if metadata is not None:
        print("[INFO] Using metadata from the live scan just performed...")
        tables_df, columns_df, stats_df, views_df, procedures_df, dep_df = metadata_to_dataframes(
            metadata, label=metadata.get("database", "live_scan")
        )
    else:
        if not os.path.exists(data_dir):
            print(f"[ERROR] Data directory not found at: {data_dir}")
            print("[ERROR] Please upload source files to the 'data/' directory.")
            sys.exit(1)
        print("[INFO] Collecting metadata from files in data/ directory...")
        tables_df, columns_df, stats_df, views_df, procedures_df, dep_df = collect_metadata(data_dir)

    if tables_df.empty:
        print("[ERROR] No tables found in the scanned metadata.")
        sys.exit(1)

    print(f"[INFO] Successfully collected metadata for {len(tables_df)} tables.")
    
    # 3. Initialize Orchestrator
    orchestrator = AzureAIOrchestrator(
        tables_df, columns_df, stats_df, views_df, procedures_df, dep_df,
        source_hint=source_hint
    )

    source_name = (source_hint or "database").replace(" ", "_").lower()
    try:
        return _run_pipeline(orchestrator, tables_df, columns_df, stats_df, views_df, procedures_df, dep_df, output_dir, source_hint)
    except Exception as exc:
        Logs["Harness Layer2"].append(f"[FAILED] Layer 2 stopped: {exc}")
        Logs["Scan Info"].append(f"[ERROR] Assessment pipeline failed: {exc}")
        Logs["Scan Info"].append(traceback.format_exc())
        raise
    finally:
        if orchestrator.tokens_used["total"]:
            Logs["Token Info"].append({
                "total": orchestrator.tokens_used["total"],
                "prompt": orchestrator.tokens_used["prompt"],
                "completion": orchestrator.tokens_used["completion"],
                "cost": round(
                    (orchestrator.tokens_used["prompt"] / 1000000.0) * 0.15
                    + (orchestrator.tokens_used["completion"] / 1000000.0) * 0.60,
                    5,
                ),
            })
        # Always clean up agents created on Azure AI Foundry, even if the
        # pipeline fails partway through - otherwise the next run's
        # create_agents() collides with these leftovers (409 conflict).
        orchestrator.cleanup_agents()


def _run_pipeline(orchestrator, tables_df, columns_df, stats_df, views_df, procedures_df, dep_df, output_dir, source_hint=None):
    # Create agents using Microsoft AI Foundry SDK
    Logs["Harness Layer2"] = [
        "HARNESS LAYER 2:",
        "Starting evaluator-generator agents.",
    ]
    orchestrator.create_agents()
    Logs["Harness Layer2"].append("Evaluator-generator agents created successfully.")

    # 4. Generate Table-Wise Summary Report
    print("\n--------------------------------------------------")
    print("Phase 1: Generating Table Summaries")
    print("--------------------------------------------------\n")
    
    # Programmatically calculate overall summary metrics
    total_tables = len(tables_df)
    total_columns = len(columns_df)
    total_size = round(stats_df["size_mb"].sum(), 4)
    total_rows = stats_df["row_count"].sum()
    distinct_schemas = ", ".join(tables_df["schema_name"].unique())
    refresh_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Top 5 largest by size
    top5_size_df = stats_df.sort_values(by="size_mb", ascending=False).head(5)
    top5_size_list = []
    for idx, (_, r) in enumerate(top5_size_df.iterrows()):
        top5_size_list.append(f"  {idx+1}. {r['table_name']} ({r['size_mb']} MB)")
    top5_size_str = "\n".join(top5_size_list) if top5_size_list else "  None"
    
    # Top 5 largest by row count
    top5_rows_df = stats_df.sort_values(by="row_count", ascending=False).head(5)
    top5_rows_list = []
    for idx, (_, r) in enumerate(top5_rows_df.iterrows()):
        top5_rows_list.append(f"  {idx+1}. {r['table_name']} ({r['row_count']} rows)")
    top5_rows_str = "\n".join(top5_rows_list) if top5_rows_list else "  None"
    
    overall_summary = f"""Total Number of Tables: {total_tables}\n
Total Number of Columns: {total_columns}\n
Total Data Size: {total_size} MB\n
Total Row Count: {total_rows}\n
Top 5 Largest Tables by Size:\n
{top5_size_str}\n
Top 5 Largest Tables by Row Count:\n
{top5_rows_str}\n
Distinct Schemas: {distinct_schemas}\n
Metadata Refresh Date (if available): {refresh_date}\n"""
    
    print("[INFO] Programmatic Overall Summary Calculated:")
    print(overall_summary)
    
    table_summaries = []
    for _, r in tables_df.iterrows():
        t_name = r["table_name"]
        s_name = r["schema_name"]
        summary = orchestrator.run_table_summarizer_agent(t_name, schema_name=s_name)
        table_summaries.append(summary)
        print(f"[INFO] Summary for '{s_name}.{t_name}' generated successfully.")
        
    source_name = (source_hint or "database").replace(" ", "_").lower()
    table_summary_filename = f"{source_name}_Assessment_Report.docx"
    migration_plan_filename = f"{source_name}_Migration_Plan.docx"
    table_summary_docx_path = os.path.join(output_dir, table_summary_filename)
    Logs["Harness Layer2"].append("Table summarizer agent completed.")
    Logs["Scan Info"].append("[INFO] Starting assessment DOCX generation.")
    create_table_summary_document(overall_summary, table_summaries, table_summary_docx_path)
    Logs["Harness Layer2"].append("Assessment DOCX generated successfully.")
    
    # 5. Generate Database Migration Assessment Report
    print("\n--------------------------------------------------")
    print("Phase 2: Generating Migration Assessment Plan")
    print("--------------------------------------------------")
    
    # Prepare text summary of all local metadata to feed the assessment agent
    stats_summary_list = []
    for _, r in stats_df.iterrows():
        stats_summary_list.append(f"- Table: {r['table_name']}, Schema: {r['schema_name']}, Rows: {r['row_count']}, Size: {r['size_mb']} MB")
        
    cols_summary_list = []
    for _, r in columns_df.head(100).iterrows(): # first 100 columns for context size stability
        cols_summary_list.append(f"- Col: {r['TableName']}.{r['ColumnName']} ({r['SourceDataType']})")
        
    dep_summary_list = []
    if not dep_df.empty:
        for _, r in dep_df.iterrows():
            dep_summary_list.append(f"- ForeignKey: {r['parent_table']}({r['fk_name']}) -> {r['referenced_table']}")
            
    stats_summary_str = "\n".join(stats_summary_list)
    dep_summary_str = "\n".join(dep_summary_list) if dep_summary_list else "None"
    cols_summary_str = "\n".join(cols_summary_list)
    metadata_summary_str = f"""Overall Stats:
- Total tables: {total_tables}
- Total columns: {total_columns}
- Distinct schemas: {distinct_schemas}

Tables and sizes:
{stats_summary_str}

Dependencies:
{dep_summary_str}

Columns Sample:
{cols_summary_str}
"""
    
    Logs["Harness Layer2"].append("Migration plan generator agent started.")
    Logs["Scan Info"].append("[INFO] Starting migration plan agent.")
    agent_writeups = orchestrator.run_migration_generator_agent(metadata_summary_str)
    
    migration_plan_docx_path = os.path.join(output_dir, migration_plan_filename)
    create_migration_plan_document(
        tables_df=tables_df,
        columns_df=columns_df,
        stats_df=stats_df,
        dep_df=dep_df,
        views_df=views_df,
        procedures_df=procedures_df,
        agent_writeups=agent_writeups,
        output_path=migration_plan_docx_path,
        tokens_used=orchestrator.tokens_used if orchestrator.client_type else None
    )
    Logs["Harness Layer2"].append("Migration Plan DOCX generated successfully.")
    print("\n==================================================")
    print("Assessment Pipeline Executed Successfully!")
    print(f"Total API Tokens Used: {orchestrator.tokens_used['total']}")
    print(f"  Prompt Tokens: {orchestrator.tokens_used['prompt']}")
    # Calculate estimated cost
    prompt_cost = (orchestrator.tokens_used["prompt"] / 1000000.0) * 0.15
    comp_cost = (orchestrator.tokens_used["completion"] / 1000000.0) * 0.60
    total_cost = prompt_cost + comp_cost
    print(f"  Completion Tokens: {orchestrator.tokens_used['completion']}")
    print(f"  Total Estimated Cost: ${total_cost:.5f} USD")
    print(f"[INFO] Reports generated in output folder:")
    print(f"  - Table Summaries: {table_summary_docx_path}")
    print(f"  - Migration Plan: {migration_plan_docx_path}")
    print("==================================================")
    Logs["Harness Layer2"] = [
        "HARNESS LAYER 2:",
        "Evaluator-generator agents completed.",
        "Metadata loaded successfully.",
        "Schema relationships analyzed.",
        "Assessment report generated.",
        "Migration plan generated.",
        "AI output validated and ready for human review.",
    ]
    return {
        "assessment_report": table_summary_filename,
        "migration_plan": migration_plan_filename,
    }