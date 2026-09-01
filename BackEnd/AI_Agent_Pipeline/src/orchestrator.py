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
from docx_generator import create_table_summary_document, create_migration_plan_document, set_cell_margins, get_source_display_name
from tools.database_tools import table_summary_tool, get_size_category
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
    def __init__(self, tables_df, columns_df, stats_df, views_df, procedures_df, dep_df, source_hint=None):
        load_dotenv()
        self.endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
        self.base_agent_name = os.getenv("AZURE_AI_FOUNDRY_AGENT_NAME", "MyAgent")
        self.model_name = os.getenv("AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
        
        # Save DataFrames as attributes to expose to tools
        self.tables_df = tables_df
        self.columns_df = columns_df
        self.stats_df = stats_df
        self.views_df = views_df
        self.procedures_df = procedures_df
        self.dep_df = dep_df
        
        self.client_type = None
        self.client = None

        # Resolve the source platform once so the RAG knowledge bases can be
        # looked up dynamically (see rag/registry.py) instead of hardcoding
        # a source type here. Falls back to common Fabric guidance only when
        # no source-specific knowledge base has been authored yet.
        self.source_type = identify_source_type(hint=source_hint)
        self._common_kb = get_common_fabric_kb()
        self._source_kb = get_source_kb(self.source_type)
        Logs["Scan Info"].append(
            f"[INFO] RAG source type resolved to '{self.source_type}' "
            f"(source-specific KB {'found' if self._source_kb else 'not available - common Fabric guidance only'})."
        )
        print(f"[INFO] RAG source type resolved to '{self.source_type}'.")
        
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

    def initialize_client(self):
        if not self.endpoint:
            raise ValueError("[ERROR] AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is missing in `.env` file.")

        Logs["Scan Info"].append(f"[INFO] Initializing Azure AI Projects Client...")
        print("[INFO] Initializing Azure AI Projects Client...")
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
        self.client = AIProjectClient(
            endpoint=self.endpoint,
            credential=DefaultAzureCredential()
        )
        self.client_type = "projects"
        Logs["Scan Info"].append(f"[INFO] Azure AI Projects Client initialized successfully.")
        print("[INFO] Azure AI Projects Client initialized successfully.")

    def get_table_metadata(self, table_name: str, schema_name: str = None) -> str:
        """
        Tool function to get database metadata summary for a single table.
        Wraps table_summary_tool using the preloaded dataframes.
        """
        Logs["Scan Info"].append(f"  [TOOL RUN] Fetching metadata summary for table: '{table_name}' (Schema: '{schema_name}')...")
        print(f"  [TOOL RUN] Fetching metadata summary for table: '{table_name}' (Schema: '{schema_name}')...")
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

    def _get_rag_context(self, query, top_k=3, max_chars_per_chunk=900):
        """
        Retrieves the most relevant knowledge chunks from the common Fabric
        KB and (if available) the source-specific KB for the given query.
        Returns "" when nothing is relevant, so callers can splice this
        straight into a prompt without extra branching.
        """
        sections = []
        source_name_clean = (self.source_type or "").lower().strip()
        
        if self._common_kb:
            # Retrieve slightly more than top_k to account for filtered items
            raw_sections = self._common_kb.retrieve(query, top_k=top_k + 8, max_chars_per_chunk=max_chars_per_chunk)
            filtered_sections = []
            for heading, text in raw_sections:
                # If active source is NOT databricks, exclude any databricks-specific guidelines
                if source_name_clean != "databricks":
                    if "databricks" in heading.lower() or "databricks" in text.lower():
                        continue
                filtered_sections.append((heading, text))
            
            for heading, text in filtered_sections[:top_k]:
                sections.append(f"[{self._common_kb.label}] {heading}\n{text}")
                
        if self._source_kb:
            raw_sections = self._source_kb.retrieve(query, top_k=top_k, max_chars_per_chunk=max_chars_per_chunk)
            for heading, text in raw_sections:
                sections.append(f"[{self._source_kb.label}] {heading}\n{text}")
                
        return "\n\n".join(sections)

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
        """
        Pulls real token counts off a Responses API result and adds them
        to the running total. Handles both the modern Responses API shape
        (input_tokens/output_tokens/total_tokens) and, defensively, the
        older Chat Completions shape (prompt_tokens/completion_tokens).
        """
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            Logs["Scan Info"].append(f"[WARNING] No usage data returned on this response - token count not updated.")
            print("[WARNING] No usage data returned on this response - token count not updated.")
            return

        def _get(field, *alt_fields):
            for f in (field, *alt_fields):
                value = getattr(usage, f, None)
                if value is None and isinstance(usage, dict):
                    value = usage.get(f)
                if value is not None:
                    return value
            return 0

        prompt = _get("input_tokens", "prompt_tokens")
        completion = _get("output_tokens", "completion_tokens")
        total = _get("total_tokens") or (prompt + completion)

        self.tokens_used["prompt"] += prompt
        self.tokens_used["completion"] += completion
        self.tokens_used["total"] += total

    def run_agent_with_tool_calling(self, agent_name, user_msg, tool_map=None):
        Logs["Harness Layer2"].append(f"[AGENT START] {agent_name}")
        # 1. Get OpenAI client bound to this agent
        openai_client = self.client.get_openai_client(agent_name=agent_name)
        
        # 2. Create conversation
        conversation = openai_client.conversations.create()
        
        # 3. Request initial response
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
        
        # 4. Handle tool execution loops
        while True:
            tool_calls = [item for item in response.output if item.type == "function_call"]
            if not tool_calls:
                break
                
            input_list = []
            for item in tool_calls:
                func_name = item.name
                func_args = json.loads(item.arguments)
                Logs["Harness Layer2"].append(
                    f"[TOOL REQUEST] {agent_name} requested {func_name} for "
                    f"{func_args.get('schema_name', '')}.{func_args.get('table_name', '')}."
                )
                Logs["Scan Info"].append(f"  [AGENT RUN] Agent requested function '{func_name}' with args {func_args}...")
                print(f"  [AGENT RUN] Agent requested function '{func_name}' with args {func_args}...")
                
                if tool_map and func_name in tool_map:
                    try:
                        output_str = tool_map[func_name](**func_args)
                    except Exception as e:
                        Logs["Harness Layer2"].append(f"[TOOL FAILED] {func_name}: {e}")
                        Logs["Scan Info"].append(f"Error executing tool: {e}")
                        output_str = f"Error executing tool: {e}"
                else:
                    Logs["Scan Info"].append(f"Error: Tool '{func_name}' is not registered.")
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
            Logs["Scan Info"].append(f"   [AGENT RUN] Submitting tool outputs back to agent...")
            print(f"  [AGENT RUN] Submitting tool outputs back to agent...")
            response = openai_client.responses.create(
                conversation=conversation.id,
                input=input_list
            )
            self._accumulate_usage(response) 
            Logs["Harness Layer2"].append(
                f"[AGENT RESPONSE] {agent_name}: follow-up response received."
            )
            
        # 5. Clean up conversation if supported, then return text
        try:
            openai_client.conversations.delete(conversation_id=conversation.id)
        except Exception:
            print(Exception)
        output_text = response.output_text
        Logs["Harness Layer2"].append(
            f"[AGENT COMPLETE] {agent_name}: generated {len(output_text or '')} characters."
        )
        return output_text

    def run_table_summarizer_agent(self, table_name, schema_name=None):
        Logs["Scan Info"].append(f"[INFO] Running Table Summarizer Agent for table '{table_name}' (Schema: '{schema_name}')...")
        print(f"[INFO] Running Table Summarizer Agent for table '{table_name}' (Schema: '{schema_name}')...")
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

        return self.run_agent_with_tool_calling(
            agent_name=self.table_summarizer_name,
            user_msg=user_msg,
            tool_map=tool_map
        )

    def run_migration_generator_agent(self, metadata_summary_str):
        Logs["Scan Info"].append(f"[INFO] Running Migration Plan Generator Agent...")
        print("[INFO] Running Migration Plan Generator Agent...")

        rag_context = self._get_rag_context(
            "medallion architecture bronze silver gold layer mapping fabric lakehouse warehouse "
            "onelake target selection object mapping data type mapping migration strategy load "
            "strategy full load incremental load batch execution sequence dependency parallel "
            "migration risk",
            top_k=6,
            max_chars_per_chunk=1200
        )
        rag_block = ""
        if rag_context:
            rag_block = "Reference migration knowledge (ground SECTION 5-8 in this guidance; do not copy it verbatim and do not cite it as a source):\n" + rag_context + "\n\n"

        source_display_name = get_source_display_name(self.source_type)
        user_msg = rag_block + f"The source database platform is {source_display_name}.\nHere is the database metadata summary gathered from files:\n\n" + metadata_summary_str + f"\n\nPlease generate the detailed text content and write-ups for Sections 1 to 9 of the Database Migration Plan for {source_display_name}."

        return self.run_agent_with_tool_calling(
            agent_name=self.migration_plan_name,
            user_msg=user_msg
        )

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
