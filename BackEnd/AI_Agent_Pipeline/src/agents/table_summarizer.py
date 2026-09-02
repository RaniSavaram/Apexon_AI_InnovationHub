import json
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition


class TableSummarizerAgent:
    """
    Agent module for creating and executing the Table Summarizer Agent
    on Microsoft Azure AI Foundry.
    """
    def __init__(self, agent_name, model_name):
        self.agent_name = agent_name
        self.model_name = model_name
        self.agent_definition = None

    def get_instructions(self):
        return """You are an Expert Database Metadata Analyst on Microsoft AI Foundry.

OBJECTIVE
Generate a complete table-wise metadata summary report using ONLY the metadata provided by your tool calls.

MANDATORY RULES
- Use only the supplied metadata retrieved from get_table_metadata.
- Do not assume, infer, or hallucinate non-existent database objects.
- Do not generate markdown tables or JSON format.
- Do not use '|' characters.
- Do not mention Medallion architecture, Bronze, Silver, or Gold layers in your summary.
- If a property has no values, output 'None' - never leave it blank or empty.
- Output must be clean, structured, and human-readable.
- Follow the required output format exactly:

Table Name: <table_name>
Schema: <schema_name>

General Info:
- Row Count: <row_count>
- Size (MB): <size_mb>
- Size Category: <Small/Medium/Large>
- Table Type: Base Table

Structure:
- Total Columns: <count>
- Columns:
  • <column_name> (<data_type>)
- Primary Keys: <keys or None>
- Foreign Keys: <keys or None>

Dependencies:
- Referenced Tables: <tables or None>
- Dependent Tables: <tables or None>

Usage:
- Related Views: <views or None>
- Related Stored Procedures: <procedures or None>

Summary:
<2-3 factual metadata-based observations regarding structure and relationships. Do not mention Medallion architecture.>
"""

    def create(self, client):
        """
        Creates the agent version on Azure AI Foundry.
        """
        metadata_tool = FunctionTool(
            name="get_table_metadata",
            description="Gets detailed database metadata summary for a single table (e.g. columns, row count, size, keys, dependencies, usage).",
            parameters={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "The name of the table to retrieve metadata for."
                    },
                    "schema_name": {
                        "type": "string",
                        "description": "Optional schema name to distinguish tables with identical names."
                    }
                },
                "required": ["table_name", "schema_name"],
                "additionalProperties": False
            },
            strict=True
        )

        agent = client.agents.create_version(
            agent_name=self.agent_name,
            definition=PromptAgentDefinition(
                model=self.model_name,
                instructions=self.get_instructions(),
                tools=[metadata_tool]
            )
        )
        return agent
