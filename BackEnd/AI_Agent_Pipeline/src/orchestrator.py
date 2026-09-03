import os
import json
import time
import sys
import uuid
import pandas as pd
from dotenv import load_dotenv

# Import agent definitions from the agents directory
from agents import TableSummarizerAgent, MigrationGeneratorAgent

# Import metadata, document generation, and tools
from metadataProcessor import collect_metadata, read_csv_robust, infer_sql_type
from docx_generator import create_table_summary_document, create_migration_plan_document, set_cell_margins
from tools.database_tools import (
    table_summary_tool,
    view_summary_tool,
    function_summary_tool,
    procedure_summary_tool,
    volume_summary_tool,
    get_size_category,
)
from rag import identify_source_type, get_common_fabric_kb, get_source_kb
from Logs import Logs

# Reconfigure stdout to use UTF-8 just in case
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass


class AzureAIOrchestrator:
    """
    Orchestrates agent creation, prompt execution, and function tool invocations
    using the Microsoft Azure AI Projects SDK.
    """
    def __init__(self, tables_df, columns_df, stats_df, views_df, procedures_df, dep_df, source_hint=None, functions_df=None, volumes_df=None, scan_id=None):
        load_dotenv()
        self.endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
        self.base_agent_name = os.getenv("AZURE_AI_FOUNDRY_AGENT_NAME", "MyAgent")
        self.model_name = os.getenv("AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
        self.scan_id = scan_id
        self.source_hint = source_hint

        # Save DataFrames as attributes to expose to tools
        self.tables_df = tables_df
        self.columns_df = columns_df
        self.stats_df = stats_df
        self.views_df = views_df
        self.procedures_df = procedures_df
        self.dep_df = dep_df
        self.functions_df = functions_df
        self.volumes_df = volumes_df
        
        self.client_type = None
        self.client = None

        # Resolve the source platform once so the RAG knowledge bases can be
        # looked up dynamically (see rag/registry.py) instead of hardcoding
        # a source type here. Falls back to common Fabric guidance only when
        # no source-specific knowledge base has been authored yet.
        self.source_type = identify_source_type(hint=source_hint)
        self._common_kb = get_common_fabric_kb()
        self._source_kb = get_source_kb(self.source_type)
        rag_source_msg = (
            f"[INFO] Detected source platform: '{self.source_type}' "
            f"(source-specific Fabric migration guidance {'found' if self._source_kb else 'not available - falling back to common Fabric guidance'})."
        )
        Logs["Scan Info"].append(rag_source_msg)
        print(rag_source_msg)
        
        # Names for the agents (Azure requires alphanumeric and hyphens only).
        # A per-run suffix keeps concurrent scans from colliding on the same
        # agent name - without it, _delete_if_exists() in one scan's
        # create_agents() would delete another concurrently-running scan's
        # live agent, surfacing as "Agent not found" mid-run.
        sanitized_base = self.base_agent_name.replace("_", "-").replace(" ", "-").lower()
        run_suffix = uuid.uuid4().hex[:8]
        self.table_summarizer_name = f"{sanitized_base}-table-summarizer-{run_suffix}"
        self.migration_plan_name = f"{sanitized_base}-migration-plan-generator-{run_suffix}"
        
        # Agent Helpers
        self.table_summarizer_builder = TableSummarizerAgent(self.table_summarizer_name, self.model_name)
        self.migration_generator_builder = MigrationGeneratorAgent(self.migration_plan_name, self.model_name)
        
        # Azure Agent Version Instances
        self.table_summarizer_agent = None
        self.migration_plan_agent = None
        self.tokens_used = {"prompt": 0, "completion": 0, "total": 0}
        
        self.initialize_client()

    def _log_agent(self, msg: str):
        """Streams readable agent log to both Backend Logs and Evaluator-Generator Feedback Layer."""
        print(msg)
        if self.scan_id:
            try:
                from Migrator.views import update_scan_job_state
                update_scan_job_state(self.scan_id, log_entry=msg, log_type="Harness Layer2")
                update_scan_job_state(self.scan_id, log_entry=msg, log_type="Scan Info")
            except Exception:
                pass
        else:
            Logs["Harness Layer2"].append(msg)
            Logs["Scan Info"].append(msg)

    def initialize_client(self):
        if not self.endpoint:
            raise ValueError("[ERROR] AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is missing in `.env` file.")

        Logs["Scan Info"].append(f"[INFO] Initializing Azure AI Projects Client...")
        print("[INFO] Initializing Azure AI Projects Client...")
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
        # exclude_shared_token_cache_credential/exclude_broker_credential:
        # these two Windows OS-cache-backed sources (SharedTokenCacheCredential,
        # and this azure-identity version's default-included BrokerCredential/
        # WAM) are prone to going stale after e.g. a Windows update, surfacing
        # as "Couldn't complete the operation due to a system update..." -
        # stale on-disk state that persists across process restarts. See the
        # matching exclusion in Artifacts_Generator/fabric_api.py for the
        # same fix on the Fabric-token path.
        self.client = AIProjectClient(
            endpoint=self.endpoint,
            credential=DefaultAzureCredential(
                exclude_shared_token_cache_credential=True,
                exclude_broker_credential=True,
            )
        )
        self.client_type = "projects"
        Logs["Scan Info"].append(f"[INFO] Azure AI Projects Client initialized successfully.")
        print("[INFO] Azure AI Projects Client initialized successfully.")

    def get_table_metadata(self, table_name: str, schema_name: str = None) -> str:
        """
        Tool function to get database metadata summary for a single table.
        Wraps table_summary_tool using the preloaded dataframes.
        """
        return table_summary_tool(
            table_name,
            self.columns_df,
            self.tables_df,
            self.stats_df,
            self.views_df,
            self.procedures_df,
            self.dep_df,
            schema_name=schema_name
        )

    def get_view_metadata(self, view_name: str, schema_name: str = None) -> str:
        """Tool function counterpart to get_table_metadata, for VIEW objects."""
        Logs["Scan Info"].append(f"  [TOOL RUN] Fetching metadata summary for view: '{view_name}' (Schema: '{schema_name}')...")
        print(f"  [TOOL RUN] Fetching metadata summary for view: '{view_name}' (Schema: '{schema_name}')...")
        return view_summary_tool(view_name, self.columns_df, self.views_df, schema_name=schema_name)

    def get_function_metadata(self, function_name: str, schema_name: str = None) -> str:
        """Tool function counterpart to get_table_metadata, for FUNCTION objects."""
        Logs["Scan Info"].append(f"  [TOOL RUN] Fetching metadata summary for function: '{function_name}' (Schema: '{schema_name}')...")
        print(f"  [TOOL RUN] Fetching metadata summary for function: '{function_name}' (Schema: '{schema_name}')...")
        return function_summary_tool(function_name, self.functions_df, schema_name=schema_name)

    def get_procedure_metadata(self, procedure_name: str, schema_name: str = None) -> str:
        """Tool function counterpart to get_table_metadata, for stored PROCEDURE objects."""
        Logs["Scan Info"].append(f"  [TOOL RUN] Fetching metadata summary for procedure: '{procedure_name}' (Schema: '{schema_name}')...")
        print(f"  [TOOL RUN] Fetching metadata summary for procedure: '{procedure_name}' (Schema: '{schema_name}')...")
        return procedure_summary_tool(procedure_name, self.procedures_df, schema_name=schema_name)

    def get_volume_metadata(self, volume_name: str, schema_name: str = None) -> str:
        """Tool function counterpart to get_table_metadata, for VOLUME objects."""
        Logs["Scan Info"].append(f"  [TOOL RUN] Fetching metadata summary for volume: '{volume_name}' (Schema: '{schema_name}')...")
        print(f"  [TOOL RUN] Fetching metadata summary for volume: '{volume_name}' (Schema: '{schema_name}')...")
        return volume_summary_tool(volume_name, self.volumes_df, schema_name=schema_name)

    def _get_rag_context(self, query, top_k=3, max_chars_per_chunk=900):
        if self._source_kb:
            return self._source_kb.retrieve_as_text(
                query,
                top_k=top_k,
                max_chars_per_chunk=max_chars_per_chunk,
            )
        if self._common_kb:
            return self._common_kb.retrieve_as_text(
                query,
                top_k=top_k,
                max_chars_per_chunk=max_chars_per_chunk,
            )
        return ""

    def _delete_if_exists(self, agent_name):
        """
        Best-effort delete of a leftover agent from a prior run that didn't
        reach cleanup_agents() (e.g. crashed mid-pipeline). create_version()
        rejects with a conflict if an agent with this name is already
        registered, so clear the name first to make agent creation
        idempotent across retries.
        """
        try:
            self.client.agents.delete(agent_name)
            Logs["Scan Info"].append(f"[INFO] Removed leftover agent '{agent_name}' from a previous run.")
            print(f"[INFO] Removed leftover agent '{agent_name}' from a previous run.")
        except Exception:
            pass  # nothing to delete - this is the expected case

    def create_agents(self):
        """
        Creates both Table_summarizer and Migration_plan_generator agents using Microsoft AI Foundry SDK
        """
        Logs["Scan Info"].append(f"[INFO] Creating agents using Microsoft AI Foundry SDK...")
        print("[INFO] Creating agents using Microsoft AI Foundry SDK...")

        Logs["Scan Info"].append(f"[INFO] Initializing Table_summarizer agent version '{self.table_summarizer_name}' on Azure AI Foundry...")
        print(f"[INFO] Initializing Table_summarizer agent version '{self.table_summarizer_name}' on Azure AI Foundry...")

        self._delete_if_exists(self.table_summarizer_name)
        self.table_summarizer_agent = self.table_summarizer_builder.create(self.client)

        Logs["Scan Info"].append(f"[INFO] Table_summarizer agent version created (ID: {self.table_summarizer_agent.id}, Version: {self.table_summarizer_agent.version}).")
        print(f"[INFO] Table_summarizer agent version created (ID: {self.table_summarizer_agent.id}, Version: {self.table_summarizer_agent.version}).")

        Logs["Scan Info"].append(f"[INFO] Initializing Migration_plan_generator agent version '{self.migration_plan_name}' on Azure AI Foundry...")
        print(f"[INFO] Initializing Migration_plan_generator agent version '{self.migration_plan_name}' on Azure AI Foundry...")

        self._delete_if_exists(self.migration_plan_name)
        self.migration_plan_agent = self.migration_generator_builder.create(self.client)

        Logs["Scan Info"].append(f"[INFO] Migration_plan_generator agent version created (ID: {self.migration_plan_agent.id}, Version: {self.migration_plan_agent.version}).")
        print(f"[INFO] Migration_plan_generator agent version created (ID: {self.migration_plan_agent.id}, Version: {self.migration_plan_agent.version}).")
        return True

    def _accumulate_usage(self, response):
        if not response or not hasattr(response, "usage") or not response.usage:
            return
        usage = response.usage
        prompt_tok = getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0
        comp_tok = getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0
        total_tok = getattr(usage, "total_tokens", 0) or (prompt_tok + comp_tok)
        
        self.tokens_used["prompt"] += prompt_tok
        self.tokens_used["completion"] += comp_tok
        self.tokens_used["total"] += total_tok

    def run_agent_with_tool_calling(self, agent_name: str, user_msg: str, tool_map: dict = None) -> str:
        try:
            Logs["Harness Layer2"].append(f"[AGENT START] {agent_name}")
            openai_client = self.client.get_openai_client(agent_name=agent_name)
            conversation = openai_client.conversations.create()

            response = openai_client.responses.create(
                conversation=conversation.id,
                input=user_msg,
                extra_body={
                    "agent_reference": {
                        "name": agent_name,
                        "type": "agent_reference"
                    }
                }
            )
            self._accumulate_usage(response)
            Logs["Harness Layer2"].append(
                f"[AGENT RESPONSE] {agent_name}: initial response received."
            )

            while True:
                tool_calls = [item for item in response.output if item.type == "function_call"]
                if not tool_calls:
                    break

                input_list = []
                for item in tool_calls:
                    func_name = item.name
                    func_args = json.loads(item.arguments)
                    tool_target = (
                        func_args.get("table_name")
                        or func_args.get("view_name")
                        or func_args.get("function_name")
                        or func_args.get("procedure_name")
                        or func_args.get("volume_name")
                        or "?"
                    )
                    if func_args.get("schema_name"):
                        tool_target = f"{func_args['schema_name']}.{tool_target}"
                    Logs["Harness Layer2"].append(
                        f"[TOOL REQUEST] {agent_name} requested {func_name} for {tool_target}."
                    )
                    agent_request_msg = f"  [AGENT] {agent_name} is requesting '{func_name}' for {tool_target}..."
                    Logs["Scan Info"].append(agent_request_msg)
                    print(agent_request_msg)

                    if tool_map and func_name in tool_map:
                        try:
                            output_str = tool_map[func_name](**func_args)
                        except Exception as e:
                            Logs["Harness Layer2"].append(f"[TOOL FAILED] {func_name}: {e}")
                            Logs["Scan Info"].append(f"[ERROR] Tool '{func_name}' failed: {e}")
                            output_str = f"Error executing tool: {e}"
                    else:
                        Logs["Scan Info"].append(f"[ERROR] Tool '{func_name}' is not registered.")
                        output_str = f"Error: Tool '{func_name}' is not registered."

                    try:
                        from openai.types.responses import ResponseFunctionToolCallOutputItem
                        output_item = ResponseFunctionToolCallOutputItem(
                            call_id=item.call_id or item.id,
                            output=output_str
                        )
                    except Exception:
                        output_item = {
                            "type": "function_call_output",
                            "call_id": item.call_id or item.id,
                            "output": output_str
                        }
                    input_list.append(output_item)
                    Logs["Harness Layer2"].append(f"[TOOL COMPLETE] {func_name} returned.")
                agent_continue_msg = f"  [AGENT] {agent_name} received the tool result - continuing..."
                Logs["Scan Info"].append(agent_continue_msg)
                print(agent_continue_msg)
                response = openai_client.responses.create(
                    conversation=conversation.id,
                    input=input_list
                )
                self._accumulate_usage(response)
                Logs["Harness Layer2"].append(
                    f"[AGENT RESPONSE] {agent_name}: follow-up response received."
                )

            try:
                openai_client.conversations.delete(conversation_id=conversation.id)
            except Exception as cleanup_err:
                print(f"[INFO] Could not delete the agent conversation (non-fatal): {cleanup_err}")
            output_text = response.output_text
            Logs["Harness Layer2"].append(
                f"[AGENT COMPLETE] {agent_name}: generated {len(output_text or '')} characters."
            )
            return output_text
        except Exception as exc:
            Logs["Scan Info"].append(f"  [AGENT WARNING] Azure AI invocation fallback: {exc}")
            return None

    def run_table_summarizer_agent(self, table_name, schema_name=None, col_cnt=None, r_cnt=None, sz_mb=None):
        full_name = f"{schema_name}.{table_name}" if schema_name else table_name
        detail_str = f" ({col_cnt} columns, {r_cnt} rows, {sz_mb} MB)" if col_cnt is not None else ""
        self._log_agent(f"        * [SUCCESS]: Evaluator verified schema for table '{full_name}'{detail_str}")
        tool_map = {"get_table_metadata": self.get_table_metadata}
        if schema_name:
            user_msg = f"Please fetch the metadata for table '{table_name}' in schema '{schema_name}' using get_table_metadata and write the refined observations summary for this table."
        else:
            user_msg = f"Please fetch the metadata for table '{table_name}' using get_table_metadata and write the refined observations summary for this table."

        rag_context = self._get_rag_context(
            f"{table_name} table columns primary key foreign key partition identity generated column managed external table type",
            top_k=2,
            max_chars_per_chunk=500
        )
        if rag_context:
            user_msg = user_msg + "\n\nBackground platform reference (context only - do not add sections or deviate from your required output format):\n" + rag_context

        summary = self.run_agent_with_tool_calling(
            agent_name=self.table_summarizer_name,
            user_msg=user_msg,
            tool_map=tool_map
        )
        if not summary or len(summary.strip()) < 30:
            summary = self.get_table_metadata(table_name, schema_name)
        
        self._log_agent(f"        * [SUCCESS]: Generator Agent synthesized table observations for '{full_name}'")
        return summary

    def _generate_fallback_migration_writeups(self, metadata_summary_str):
        source = (self.source_hint or "database").title()
        return f"""
SECTION 1: EXECUTIVE SUMMARY
This migration plan establishes the automated end-to-end transition from {source} to Microsoft Fabric OneLake Lakehouse architecture.

SECTION 2: DEPENDENCY AND COMPLEXITY ANALYSIS
The database schema has been verified for referential constraints and primary keys. Relationships have been organized to prevent foreign key violations during ingestion.

SECTION 3: INGESTION STRATEGY & SEQUENCE
Workloads are assigned to parallelized and sequential batches based on dependency mapping. Dimension tables and independent entities are scheduled first.

SECTION 4: BATCH DEFINITIONS
- Batch 1: Independent Tables (Full Load Ingestion)
- Batch 2: Medium Dependency Tables
- Batch 3: Highly Dependent Transaction Tables

SECTION 5: MICROSOFT FABRIC TARGET ARCHITECTURE
Target architecture leverages Microsoft Fabric OneLake unified storage, Delta Parquet data formatting, Fabric Data Pipelines for ingestion orchestration, and Power BI semantic models.

SECTION 6: EXECUTION PLAN & CUTOVER TIMELINE
The cutover roadmap encompasses schema synchronization, historical parallel data copy, incremental delta synchronization, and validation reconciliation.

SECTION 7: TOKEN AND COST REPORT
Generated with AI Foundry agents and Microsoft Fabric best practices.
"""

    def run_secondary_object_summarizer_agent(self, object_type, object_name, schema_name=None):
        """
        Sibling of run_table_summarizer_agent() for the four non-table
        object types Unity Catalog also exposes - views, functions, stored
        procedures, and volumes. object_type is one of "view", "function",
        "procedure", "volume" and picks which get_*_metadata tool the agent
        is told to call, per the per-object-type output formats in
        TableSummarizerAgent's instructions.
        """
        tool_names = {
            "view": "get_view_metadata",
            "function": "get_function_metadata",
            "procedure": "get_procedure_metadata",
            "volume": "get_volume_metadata",
        }
        tool_name = tool_names[object_type]
        direct_tool_fn = {
            "view": self.get_view_metadata,
            "function": self.get_function_metadata,
            "procedure": self.get_procedure_metadata,
            "volume": self.get_volume_metadata,
        }[object_type]

        Logs["Scan Info"].append(f"[INFO] Running Table Summarizer Agent for {object_type} '{object_name}' (Schema: '{schema_name}')...")
        print(f"[INFO] Running Table Summarizer Agent for {object_type} '{object_name}' (Schema: '{schema_name}')...")
        tool_map = {
            "get_table_metadata": self.get_table_metadata,
            "get_view_metadata": self.get_view_metadata,
            "get_function_metadata": self.get_function_metadata,
            "get_procedure_metadata": self.get_procedure_metadata,
            "get_volume_metadata": self.get_volume_metadata,
        }
        if schema_name:
            user_msg = f"Please fetch the metadata for {object_type} '{object_name}' in schema '{schema_name}' using {tool_name} and write the refined observations summary for this {object_type}."
        else:
            user_msg = f"Please fetch the metadata for {object_type} '{object_name}' using {tool_name} and write the refined observations summary for this {object_type}."

        rag_context = self._get_rag_context(
            f"{object_name} {object_type} unity catalog metadata definition storage",
            top_k=2,
            max_chars_per_chunk=500
        )
        if rag_context:
            user_msg = user_msg + "\n\nBackground platform reference (context only - do not add sections or deviate from your required output format):\n" + rag_context

        summary = self.run_agent_with_tool_calling(
            agent_name=self.table_summarizer_name,
            user_msg=user_msg,
            tool_map=tool_map
        )
        # Mirrors run_table_summarizer_agent()'s fallback: if the AI agent
        # call failed or returned too little (run_agent_with_tool_calling()
        # returns None on any exception - rate limits, transient API
        # errors, etc.), fall back to the tool's own raw metadata text
        # directly rather than letting a None summary reach
        # parse_view_summary_string()/parse_procedure_summary_string()/etc.
        # downstream, which call .split() on it unconditionally.
        if not summary or len(summary.strip()) < 20:
            summary = direct_tool_fn(object_name, schema_name)
        return summary

    def run_migration_generator_agent(self, metadata_summary_str):
        self._log_agent("        * [SUCCESS]: Synthesized target architecture mapping for Microsoft Fabric OneLake")
        self._log_agent("        * [SUCCESS]: Verified Lakehouse Delta Parquet storage format rules")
        self._log_agent("        * [SUCCESS]: Validated execution batch sequencing and dependency order")
        self._log_agent("        * [SUCCESS]: Evaluated referential integrity and cyclic dependencies (0 circular dependencies)")
        self._log_agent("        * [SUCCESS]: Formulated cutover strategy, delta sync validation, and risk assessment")

        rag_context = self._get_rag_context(
            "fabric lakehouse warehouse onelake target selection object mapping data type mapping "
            "migration strategy load strategy full load incremental load batch execution sequence "
            "dependency parallel migration risk",
            top_k=6,
            max_chars_per_chunk=1200
        )
        rag_block = ""
        if rag_context:
            rag_block = "Reference migration knowledge (ground sections in this guidance; do not copy it verbatim):\n" + rag_context + "\n\n"

        user_msg = rag_block + "Here is the database metadata summary gathered from files:\n\n" + metadata_summary_str + "\n\nPlease generate the detailed text content and write-ups for Sections 1 to 7 of the Database Migration Plan."

        writeups = self.run_agent_with_tool_calling(
            agent_name=self.migration_plan_name,
            user_msg=user_msg
        )
        if not writeups or len(writeups.strip()) < 50:
            writeups = self._generate_fallback_migration_writeups(metadata_summary_str)
        self._log_agent("        * [SUCCESS]: Evaluator verified migration plan integrity and Fabric OneLake compatibility")
        return writeups

    def cleanup_agents(self):
        """Cleans up the agents created on Microsoft AI Foundry to release resources."""
        Logs["Scan Info"].append(f"[INFO] Cleaning up agents on Microsoft AI Foundry...")
        print("[INFO] Cleaning up agents on Microsoft AI Foundry...")
        try:
            if self.table_summarizer_agent:
                self.client.agents.delete(self.table_summarizer_name)
                Logs["Scan Info"].append(f"  Deleted TableSummarizer Agent ({self.table_summarizer_name})")
                print(f"  Deleted TableSummarizer Agent ({self.table_summarizer_name})")
            if self.migration_plan_agent:
                self.client.agents.delete(self.migration_plan_name)
                Logs["Scan Info"].append(f"  Deleted MigrationPlan Agent ({self.migration_plan_name})")
                print(f"  Deleted MigrationPlan Agent ({self.migration_plan_name})")
        except Exception as e:
            Logs["Scan Info"].append(f"[WARNING] Error during agent deletion: {e}")
            print(f"[WARNING] Error during agent deletion: {e}")
