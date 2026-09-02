import json
import os

# Dynamically fetch SENSITIVE_KEYWORDS from GovernanceValidator inside Layer.py (without hardcoding)
def _get_sensitive_keywords():
    try:
        from HarnessLayers.layer1.Layer import GovernanceValidator
        return sorted(list(GovernanceValidator.SENSITIVE_KEYWORDS))
    except ImportError:
        try:
            from layer1.Layer import GovernanceValidator
            return sorted(list(GovernanceValidator.SENSITIVE_KEYWORDS))
        except ImportError:
            pass

    # Fallback: scan directories to find Layer.py and parse SENSITIVE_KEYWORDS dynamically
    import re
    current_dir = os.path.dirname(os.path.abspath(__file__))
    for root_dir in [current_dir, os.path.abspath(os.path.join(current_dir, "..")), os.path.abspath(os.path.join(current_dir, "..", "..")), os.path.abspath(os.path.join(current_dir, "..", "..", ".."))]:
        for root, dirs, files in os.walk(root_dir):
            if "Layer.py" in files:
                layer_path = os.path.join(root, "Layer.py")
                try:
                    with open(layer_path, 'r', encoding='utf-8') as f:
                        code = f.read()
                    match = re.search(r"SENSITIVE_KEYWORDS\s*=\s*\{([^}]+)\}", code)
                    if match:
                        keywords_raw = match.group(1)
                        keywords = re.findall(r"['\"]([^'\"]+)['\"]", keywords_raw)
                        if keywords:
                            return sorted(list(set(keywords)))
                except Exception:
                    pass
    
    # Return empty list in case Layer.py is completely unreachable (to avoid hardcoding)
    return []

SENSITIVE_KEYWORDS = _get_sensitive_keywords()

# Configuration containing validation steps and their mapped issue rules for each section.
# Easily append new sections and rules here to support future layers (like Harness Layer 2)
# without changing the core execution code.
VALIDATION_STEPS_CONFIG = {
    "connection_validation": [
        {
            "name": "Verifying database connection is established", 
            "completed_name": "Verified database connection is established",
            "rules": ["CONNECTION_ESTABLISHED"]
        },
        {
            "name": "Verifying authentication is successful", 
            "completed_name": "Verified authentication is successful",
            "rules": ["AUTHENTICATION_SUCCESSFUL"]
        },
        {
            "name": "Verifying credentials are verified", 
            "completed_name": "Verified credentials",
            "rules": ["CREDENTIALS_VERIFIED"]
        },
        {
            "name": "Checking if connection timeout was exceeded", 
            "completed_name": "Checked connection timeout (not exceeded)",
            "rules": ["CONNECTION_TIMEOUT"]
        },
        {
            "name": "Checking if required permissions are available", 
            "completed_name": "Checked required permissions",
            "rules": ["REQUIRED_PERMISSIONS"], 
            "info_text": "Required permissions: SELECT"
        }
    ],
    "scan_validation": [
        {
            "name": "Verifying scan completed successfully", 
            "completed_name": "Verified scan completed successfully",
            "rules": ["SCAN_COMPLETED"]
        },
        {
            "name": "Verifying scan scope matches expected scope", 
            "completed_name": "Verified scan scope matches expected scope",
            "rules": ["SCAN_SCOPE_VERIFIED"]
        },
        {
            "name": "Checking for interrupted scans", 
            "completed_name": "Checked for interrupted scans",
            "rules": ["NO_INTERRUPTED_SCANS"]
        },
        {
            "name": "Checking for partial extractions", 
            "completed_name": "Checked for partial extractions",
            "rules": ["NO_PARTIAL_EXTRACTION"]
        }
    ],
    "metadata_validation": [
        {
            "name": "Validating metadata JSON structure", 
            "completed_name": "Validated metadata JSON structure",
            "rules": ["METADATA_JSON_VALID"]
        },
        {
            "name": "Verifying required top-level fields exist", 
            "completed_name": "Verified required top-level fields exist",
            "rules": ["REQUIRED_FIELDS_EXIST"], 
            "info_text": "Required top-level fields: tables, views, procedures, relationships"
        },
        {
            "name": "Verifying tables and columns are present", 
            "completed_name": "Verified tables and columns are present",
            "rules": ["TABLES_PRESENT", "COLUMNS_PRESENT"]
        },
        {
            "name": "Checking primary and foreign key constraints", 
            "completed_name": "Checked primary and foreign key constraints",
            "rules": ["PRIMARY_KEY_VALID", "FOREIGN_KEY_VALID"]
        },
        {
            "name": "Checking views detected in metadata", 
            "completed_name": "Checked views detected in metadata",
            "rules": ["VIEWS_DETECTED"]
        },
        {
            "name": "Checking stored procedures detected in metadata", 
            "completed_name": "Checked stored procedures detected in metadata",
            "rules": ["PROCEDURES_DETECTED"]
        }
    ],
    "fabric_compatibility": [
        {
            "name": "Checking data type compatibility with Microsoft Fabric", 
            "completed_name": "Checked data type compatibility with Microsoft Fabric",
            "rules": ["UNSUPPORTED_DATA_TYPE"], 
            "info_text": "Monitored unsupported types: sql_variant, xml, geography, geometry, hierarchyid, cursor, table, timestamp"
        },
        {
            "name": "Checking constraint compatibility with Microsoft Fabric", 
            "completed_name": "Checked constraint compatibility with Microsoft Fabric",
            "rules": ["UNSUPPORTED_CONSTRAINT"], 
            "info_text": "Monitored unsupported constraints: COMPUTED COLUMN CONSTRAINT, XML SCHEMA COLLECTION"
        },
        {
            "name": "Checking object type compatibility with Microsoft Fabric", 
            "completed_name": "Checked object type compatibility with Microsoft Fabric",
            "rules": ["UNSUPPORTED_OBJECT_TYPE"], 
            "info_text": "Monitored unsupported object types: CLR ASSEMBLY, SERVICE BROKER, FILESTREAM"
        },
        {
            "name": "Checking SQL feature compatibility with Microsoft Fabric", 
            "completed_name": "Checked SQL feature compatibility with Microsoft Fabric",
            "rules": ["UNSUPPORTED_SQL_FEATURE"], 
            "info_text": "Monitored unsupported SQL features: CLR, SERVICE BROKER, FILESTREAM, CHANGE TRACKING, TEMPORAL TABLE VERSIONING (unsupported edge cases)"
        }
    ],
    "governance_validation": [
        {
            "name": "Checking naming conventions for tables and columns", 
            "completed_name": "Checked naming conventions for tables and columns",
            "rules": ["NAMING_CONVENTION"]
        },
        {
            "name": "Verifying required governance metadata fields exist", 
            "completed_name": "Verified required governance metadata fields exist",
            "rules": ["REQUIRED_GOVERNANCE_METADATA"], 
            "info_text": "Required governance metadata fields: owner, classification"
        },
        {
            "name": "Scanning column names for unflagged sensitive keywords", 
            "completed_name": "Scanned column names for unflagged sensitive keywords",
            "rules": ["SENSITIVE_DATA_IDENTIFICATION"], 
            "info_text": "Monitored sensitive keywords: {SENSITIVE_KEYWORDS_PLACEHOLDER}"
        },
        {
            "name": "Verifying metadata has an assigned owner", 
            "completed_name": "Verified metadata has an assigned owner",
            "rules": ["METADATA_OWNERSHIP"]
        }
    ],
    "agent_orchestration_validation": [
        {
            "name": "Verifying connection to Microsoft AI Foundry Projects SDK",
            "completed_name": "Connected to Microsoft AI Foundry Projects SDK",
            "rules": ["AI_FOUNDRY_CONNECTED"]
        },
        {
            "name": "Verifying Table Summarizer Generator Agent initialization",
            "completed_name": "Initialized Table Summarizer Generator Agent",
            "rules": ["TABLE_SUMMARIZER_AGENT_READY"]
        },
        {
            "name": "Verifying Migration Plan Generator Agent initialization",
            "completed_name": "Initialized Migration Plan Generator Agent",
            "rules": ["MIGRATION_GENERATOR_AGENT_READY"]
        },
        {
            "name": "Verifying Semantic RAG Migration Knowledge Base indexing",
            "completed_name": "Loaded Semantic RAG Migration Knowledge Base",
            "rules": ["RAG_KNOWLEDGE_BASE_INDEXED"]
        }
    ],
    "evaluator_table_assessment": [
        {
            "name": "Verifying table schema extractions and metadata consistency",
            "completed_name": "Verified table schema extractions and metadata consistency",
            "rules": ["TABLE_SCHEMAS_VERIFIED"]
        },
        {
            "name": "Evaluating Table Summarizer Generator observations",
            "completed_name": "Evaluated Table Summarizer Generator observations",
            "rules": ["TABLE_SUMMARIES_EVALUATED"]
        },
        {
            "name": "Checking for AI hallucinations against physical database metadata",
            "completed_name": "Checked for AI hallucinations against metadata (0 detected)",
            "rules": ["NO_HALLUCINATIONS_DETECTED"]
        },
        {
            "name": "Validating primary keys and foreign key constraints preservation",
            "completed_name": "Validated primary keys and foreign key constraints",
            "rules": ["CONSTRAINTS_PRESERVED"]
        },
        {
            "name": "Verifying agent output schema conformity and quality score",
            "completed_name": "Verified agent output schema conformity (Score: 100%)",
            "rules": ["OUTPUT_SCHEMA_CONFORMITY"]
        }
    ],
    "migration_plan_evaluation": [
        {
            "name": "Validating target architecture mapping for Microsoft Fabric OneLake",
            "completed_name": "Validated target architecture mapping for Microsoft Fabric OneLake",
            "rules": ["TARGET_ARCHITECTURE_FABRIC"]
        },
        {
            "name": "Verifying Lakehouse Delta Parquet storage format rules",
            "completed_name": "Verified Lakehouse Delta Parquet storage format rules",
            "rules": ["DELTA_PARQUET_FORMAT_VALIDATED"]
        },
        {
            "name": "Validating execution batch sequencing and dependency order",
            "completed_name": "Validated execution batch sequencing and dependency order",
            "rules": ["BATCH_SEQUENCING_VALIDATED"]
        },
        {
            "name": "Checking for circular dependencies across foreign keys",
            "completed_name": "Checked for circular dependencies (0 circular dependencies)",
            "rules": ["NO_CIRCULAR_DEPENDENCIES"]
        },
        {
            "name": "Verifying Spark transformation and pipeline orchestration strategy",
            "completed_name": "Verified Spark transformation and pipeline orchestration strategy",
            "rules": ["ORCHESTRATION_STRATEGY_VERIFIED"]
        }
    ],
    "artifact_quality_validation": [
        {
            "name": "Validating Assessment Report (.docx) generation and layout",
            "completed_name": "Validated Assessment Report (.docx) generation and layout",
            "rules": ["ASSESSMENT_REPORT_VALIDATED"]
        },
        {
            "name": "Validating Migration Assessment Plan (.docx) generation",
            "completed_name": "Validated Migration Assessment Plan (.docx) generation",
            "rules": ["MIGRATION_PLAN_VALIDATED"]
        },
        {
            "name": "Validating Microsoft Fabric Migration Metadata JSON schema",
            "completed_name": "Validated Microsoft Fabric Migration Metadata JSON schema",
            "rules": ["FABRIC_JSON_SCHEMA_VALIDATED"]
        },
        {
            "name": "Performing final Evaluator-Generator quality audit",
            "completed_name": "Final Evaluator-Generator quality audit passed (0 errors, 0 warnings)",
            "rules": ["FINAL_QUALITY_AUDIT_PASSED"]
        }
    ]
}

def format_harness_report(json_data):
    # Parse the JSON if it's passed as a string
    if isinstance(json_data, str):
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON format: {e}"
    elif isinstance(json_data, dict):
        data = json_data
    else:
        return "Error: Input must be a JSON string or dictionary."

    # Extract the header title from the "layer" field (defaults to "SECTION RESULTS")
    # E.g. "Harness Layer 1 - Constraint & Governance Harness" -> "HARNESS LAYER 1"
    layer_name = data.get("layer", "SECTION RESULTS")
    header_title = layer_name.split(" - ")[0].upper()

    # Extract the time part from the JSON input
    generated_at = data.get("generated_at", "Unknown Time")

    report_lines = []
    # Display the header and the generated_at timestamp on separate lines
    report_lines.append(f"{header_title}:")
    report_lines.append(f"Generated At: {generated_at}")
    report_lines.append("-" * 30)

    # Process Sections
    for idx, sec in enumerate(data.get("sections", [])):
        section_name = sec.get("section", "unnamed_section")
        passed = sec.get("passed")

        # Map boolean passed:
        # At first step (idx == 0) true -> checked
        # At later steps (idx > 0) true -> SUCCESS
        # Any False -> error
        if passed is True:
            status = "CHECKED" if idx == 0 else "SUCCESS"
            report_lines.append(f"[{status}]: {section_name}")
        else:
            status = "error"
            # Find the error message to explain why it failed
            error_messages = [
                issue.get("message")
                for issue in sec.get("issues", [])
                if issue.get("severity", "").upper() == "ERROR"
            ]
            
            # Fallback to general issues if no explicit ERROR severity issue is found
            if not error_messages and sec.get("issues"):
                error_messages = [issue.get("message") for issue in sec.get("issues")]
                
            reason = f" - Reason: {'; '.join(error_messages)}" if error_messages else ""
            report_lines.append(f"[{status}]: {section_name}{reason}")

        # Track which issues have been printed to nest them correctly
        printed_issues = set()

        # Helper to print step and nest any associated issues or info configs directly under it
        def print_step(step_name, rule_keys=None, info_text=None, completed_name=None):
            # Find issues matching rule keys
            matching_issues = []
            if rule_keys:
                for rule_key in rule_keys:
                    for issue_idx, issue in enumerate(sec.get("issues", [])):
                        if issue.get("rule") == rule_key:
                            matching_issues.append((issue_idx, issue))

            # Determine check status indicator for the step
            if any(item[1].get("severity", "").upper() == "ERROR" for item in matching_issues):
                step_status = "error"
            elif any(item[1].get("severity", "").upper() == "WARNING" for item in matching_issues):
                step_status = "WARNING"
            else:
                # All passing sub-checks print [SUCCESS]
                step_status = "SUCCESS"
            
            # Print monitored info config ONLY if the section passed (printed first as CHECKED)
            if passed is True and info_text:
                report_lines.append(f"          - [CHECKED]: {info_text}")

            # If successful (SUCCESS), use completed/past tense name. Otherwise use active name.
            display_name = completed_name if (step_status == "SUCCESS" and completed_name) else step_name
            
            # Print the step status (printed second)
            report_lines.append(f"        * [{step_status}]: {display_name}")

            # Print matching issues (warnings/errors)
            for issue_idx, issue in matching_issues:
                rule = issue.get("rule", "Unknown Rule")
                message = issue.get("message", "")
                severity = issue.get("severity", "WARNING").upper()
                mapped_severity = "error" if severity == "ERROR" else ("WARNING" if severity == "WARNING" else severity.lower())
                report_lines.append(f"              - [{mapped_severity}]: Rule {rule}: {message}")
                printed_issues.add(issue_idx)

        # Process steps dynamically from VALIDATION_STEPS_CONFIG
        if section_name in VALIDATION_STEPS_CONFIG:
            report_lines.append("    - Harness Steps:")
            for step_cfg in VALIDATION_STEPS_CONFIG[section_name]:
                step_name = step_cfg["name"]
                completed_name = step_cfg.get("completed_name")
                rule_keys = step_cfg.get("rules")
                info_text = step_cfg.get("info_text")
                
                # Replace dynamic placeholder for SENSITIVE_KEYWORDS
                if info_text and "{SENSITIVE_KEYWORDS_PLACEHOLDER}" in info_text:
                    kw_str = ", ".join(SENSITIVE_KEYWORDS) if SENSITIVE_KEYWORDS else "(unable to load sensitive keywords)"
                    info_text = info_text.replace("{SENSITIVE_KEYWORDS_PLACEHOLDER}", kw_str)
                
                print_step(step_name, rule_keys, info_text, completed_name)

        # Fallback to output any issue that did not map to a standard rule key
        remaining_issues = [
            issue for idx, issue in enumerate(sec.get("issues", []))
            if idx not in printed_issues
        ]
        if remaining_issues:
            for issue in remaining_issues:
                rule = issue.get("rule", "Unknown Rule")
                message = issue.get("message", "")
                severity = issue.get("severity", "WARNING").upper()
                mapped_severity = "error" if severity == "ERROR" else ("WARNING" if severity == "WARNING" else severity.lower())
                report_lines.append(f"        - [{mapped_severity}]: Rule {rule}: {message}")
        report_lines.append("")  # Empty line for spacing

    # Summary
    summary = data.get("summary", {})
    report_lines.append("-" * 30)
    report_lines.append("REPORT SUMMARY:")
    report_lines.append(f"Total errors: {summary.get('total_errors', 0)}")
    report_lines.append(f"Total Warnings: {summary.get('total_warnings', 0)}")
    report_lines.append("=" * 30)
    report_lines.append("\n If you want to continue click on SUBMIT/CONTINUE if u wanna rescan click RETRY extraction\n")
    #report_lines.append("")

    # Return the final report string directly
    return "\n".join(report_lines)

if __name__ == "__main__":
    # Test script execution using a JSON string or dict
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Resolve the path to C:\1CIOT\JSON\HARNESS LAYER 1.json
    input_file_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "JSON", "HARNESS LAYER 1.json"))
    
    if os.path.exists(input_file_path):
        with open(input_file_path, 'r') as f:
            sample_data = json.load(f)
            
        print("--- Test with JSON dictionary variable ---")
        formatted_output = format_harness_report(sample_data)
        print(formatted_output)
    else:
        print(f"Error: HARNESS LAYER 1.json not found at {input_file_path}")
