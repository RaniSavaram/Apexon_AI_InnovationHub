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


def _update_progress(scan_id, progress=None, current_message=None, log_entry=None, log_type="Scan Info"):
    try:
        from Migrator.views import update_scan_job_state
        update_scan_job_state(scan_id, progress, current_message, log_entry, log_type)
    except Exception:
        pass


def Agents_PipeLine(metadata: dict = None, source_hint: str = None, scan_id: str = None):
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
        tables_df, columns_df, stats_df, views_df, procedures_df, functions_df, volumes_df, dep_df = metadata_to_dataframes(
            metadata, label=metadata.get("database", "live_scan")
        )
    else:
        if not os.path.exists(data_dir):
            print(f"[ERROR] Data directory not found at: {data_dir}")
            print("[ERROR] Please upload source files to the 'data/' directory.")
            sys.exit(1)
        print("[INFO] Collecting metadata from files in data/ directory...")
        tables_df, columns_df, stats_df, views_df, procedures_df, functions_df, volumes_df, dep_df = collect_metadata(data_dir)

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
        return _run_pipeline(orchestrator, tables_df, columns_df, stats_df, views_df, procedures_df, dep_df, output_dir, source_hint, scan_id, functions_df=functions_df, volumes_df=volumes_df)
    except Exception as exc:
        _update_progress(scan_id, log_entry=f"[FAILED] Layer 2 stopped: {exc}", log_type="Harness Layer2")
        _update_progress(scan_id, log_entry=f"[ERROR] Assessment pipeline failed: {exc}", log_type="Scan Info")
        _update_progress(scan_id, log_entry=traceback.format_exc(), log_type="Scan Info")
        raise
    finally:
        if orchestrator.tokens_used["total"]:
            _update_progress(scan_id, log_entry={
                "total": orchestrator.tokens_used["total"],
                "prompt": orchestrator.tokens_used["prompt"],
                "completion": orchestrator.tokens_used["completion"],
                "cost": round(
                    (orchestrator.tokens_used["prompt"] / 1000000.0) * 0.15
                    + (orchestrator.tokens_used["completion"] / 1000000.0) * 0.60,
                    5,
                ),
            }, log_type="Token Info")
        # Always clean up agents created on Azure AI Foundry, even if the
        # pipeline fails partway through - otherwise the next run's
        # create_agents() collides with these leftovers (409 conflict).
        orchestrator.cleanup_agents()


def _run_pipeline(orchestrator, tables_df, columns_df, stats_df, views_df, procedures_df, dep_df, output_dir, source_hint=None, scan_id=None, functions_df=None, volumes_df=None):
    # Create agents using Microsoft AI Foundry SDK
    _update_progress(scan_id, progress=65, current_message="Initializing Harness Layer 2 AI agents...", log_entry="HARNESS LAYER 2:\nStarting evaluator-generator agents.", log_type="Harness Layer2")
    orchestrator.create_agents()

    _update_progress(scan_id, progress=69, current_message="Harness Layer 2 AI agents initialized.", log_entry="Evaluator-generator agents created successfully.", log_type="Harness Layer2")
    time.sleep(1.2)

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
    for idx, (_, r) in enumerate(tables_df.iterrows()):
        t_name = r["table_name"]
        s_name = r["schema_name"]

        # Calculate dynamic table summary progress between 70% and 80%
        current_pct = int(70 + (idx / total_tables) * 10)
        _update_progress(scan_id, progress=current_pct, current_message=f"Analyzing table schema: {s_name}.{t_name} ({idx+1}/{total_tables})", log_entry=f"[INFO] Running Table Summarizer Agent for table '{t_name}' (Schema: '{s_name}')...", log_type="Scan Info")

        summary = orchestrator.run_table_summarizer_agent(t_name, schema_name=s_name)
        table_summaries.append(summary)
        _update_progress(scan_id, log_entry=f"[INFO] Summary for '{s_name}.{t_name}' generated successfully.", log_type="Scan Info")
        print(f"[INFO] Summary for '{s_name}.{t_name}' generated successfully.")

    source_name = (source_hint or "database").replace(" ", "_").lower()
    table_summary_filename = f"{source_name}_Assessment_Report.docx"
    migration_plan_filename = f"{source_name}_Migration_Plan.docx"
    table_summary_docx_path = os.path.join(output_dir, table_summary_filename)

    _update_progress(scan_id, progress=80, current_message=f"Compiling table assessment report document", log_entry=f"[INFO] Generating Assessment Report: {table_summary_filename}", log_type="Scan Info")

    create_table_summary_document(
        overall_summary=overall_summary,
        table_summaries=table_summaries,
        output_path=table_summary_docx_path,
        source_hint=source_hint,
        tables_df=tables_df,
        columns_df=columns_df,
        stats_df=stats_df,
        views_df=views_df,
        procedures_df=procedures_df,
        functions_df=functions_df,
        volumes_df=volumes_df
    )

    _update_progress(scan_id, log_entry=f"[INFO] Successfully saved Assessment Report: {table_summary_filename}", log_type="Scan Info")
    time.sleep(1.2)

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

    _update_progress(
        scan_id,
        progress=85,
        current_message=f"Generating {source_name} Migration Roadmap with Azure AI",
        log_entry=f"[INFO] Generating comprehensive Migration Roadmap (Sections 1 to 9) via Azure AI Foundry (estimated 30-45s)...",
        log_type="Scan Info"
    )
    time.sleep(1.0)

    agent_writeups = orchestrator.run_migration_generator_agent(metadata_summary_str)

    _update_progress(
        scan_id,
        progress=90,
        current_message=f"Azure AI Migration Roadmap completed",
        log_entry=f"[INFO] Successfully received Migration Roadmap from Azure AI Foundry.",
        log_type="Scan Info"
    )

    migration_plan_docx_path = os.path.join(output_dir, migration_plan_filename)
    _update_progress(scan_id, progress=92, current_message="Compiling execution order and Medallion plan", log_entry=f"[INFO] Compiling execution order and Medallion plan for {source_name}...", log_type="Scan Info")
    time.sleep(1.2)

    create_migration_plan_document(
        tables_df=tables_df,
        columns_df=columns_df,
        stats_df=stats_df,
        dep_df=dep_df,
        views_df=views_df,
        procedures_df=procedures_df,
        functions_df=functions_df,
        volumes_df=volumes_df,
        agent_writeups=agent_writeups,
        output_path=migration_plan_docx_path,
        tokens_used=orchestrator.tokens_used if orchestrator.client_type else None,
        source_hint=source_hint
    )
    _update_progress(scan_id, log_entry=f"[INFO] Successfully saved Migration Plan: {migration_plan_filename}", log_type="Scan Info")

    # Generate Fabric JSON Metadata
    fabric_json_filename = f"{source_name}_Fabric_Migration_Metadata.json"
    fabric_json_path = os.path.join(output_dir, fabric_json_filename)
    _update_progress(scan_id, progress=95, current_message="Generating Fabric JSON Metadata", log_entry=f"[INFO] Generating Fabric migration metadata JSON for {source_name}...", log_type="Scan Info")

    try:
        from AI_Agent_Pipeline.src.fabric_json_generator import generate_fabric_json_metadata
        generate_fabric_json_metadata(
            tables_df=tables_df,
            columns_df=columns_df,
            stats_df=stats_df,
            dep_df=dep_df,
            views_df=views_df,
            procedures_df=procedures_df,
            agent_writeups=agent_writeups,
            output_path=fabric_json_path,
            source_hint=source_hint,
            scan_id=scan_id
        )
        _update_progress(scan_id, log_entry="Fabric JSON metadata generated successfully.", log_type="Harness Layer2")
    except Exception as json_err:
        print(f"[WARN] Failed to generate Fabric JSON metadata: {json_err}")
        _update_progress(scan_id, log_entry=f"[WARN] Fabric JSON metadata generation failed: {json_err}", log_type="Harness Layer2")

    # Replicate files for compatibility with frontend and other components
    import shutil
    try:
        shutil.copy2(table_summary_docx_path, os.path.join(output_dir, "Assesment Report.docx"))
        shutil.copy2(table_summary_docx_path, os.path.join(output_dir, "Metadata_Report.docx"))
        shutil.copy2(migration_plan_docx_path, os.path.join(output_dir, "AI_Migration_Plan.docx"))
        shutil.copy2(migration_plan_docx_path, os.path.join(output_dir, "Migration_Assessment.docx"))
        if os.path.exists(fabric_json_path):
            shutil.copy2(fabric_json_path, os.path.join(output_dir, "Fabric_Migration_Metadata.json"))
        print("[INFO] Replicated reports for frontend compatibility.")
    except Exception as copy_err:
        print(f"[WARN] Failed to copy files for compatibility: {copy_err}")

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

    generated_sections = sum(
        f"SECTION {section}" in str(agent_writeups).upper()
        for section in range(1, 10)
    )
    _update_progress(scan_id, log_entry=f"[INFO] Assessment Pipeline Executed Successfully for {source_name} ({len(tables_df)} tables, {len(columns_df)} columns).", log_type="Scan Info")
    _update_progress(scan_id, progress=98, current_message="Finalizing database scanner outputs", log_entry=f"[INFO] Reports verified: {Path(table_summary_docx_path).name}, {Path(migration_plan_docx_path).name}.", log_type="Scan Info")
    time.sleep(1.0)

    return {
        "assessment_report": table_summary_filename,
        "migration_plan": migration_plan_filename,
        "fabric_migration_metadata": fabric_json_filename,
    }
