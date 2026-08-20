"""
Negative-scenario check for Harness Layer 1 (Constraint & Governance
Harness): does it correctly catch a destructive SQL statement (DELETE /
TRUNCATE) captured against a table during Databricks metadata extraction?

DatabricksExtractor.extract() (Metadata_Scanner/extractors/databricks_client.py)
only reads information_schema.tables/columns today - it doesn't pull query
history, so it never populates a table's "recent_statements" list. This
script builds metadata in the same shape extract() produces, with that field
filled in for two tables (one DELETE, one TRUNCATE, in two different
schemas), and runs it through the real layer1_Harness() entry point to
confirm GovernanceValidator's DESTRUCTIVE_SQL_DETECTED rule flags both and
fails the harness decision.

Run directly:
    python HarnessLayers/layer1/test_databricks_negative_scenario.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Layer.py is imported as "HarnessLayers.layer1.Layer" - put BackEnd/ (two
# levels up from this file) on sys.path so that import resolves when this
# script is run directly rather than via Django/pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from HarnessLayers.layer1.Layer import layer1_Harness  # noqa: E402


def build_negative_scenario_metadata() -> dict:
    """
    Databricks-shaped raw metadata (matches DatabricksExtractor.extract()'s
    {"database", "schemas": [{"name", "tables": [...]}]} shape) covering two
    schemas, each with one table carrying a captured destructive statement:
      - sales.orders            -> TRUNCATE TABLE sales.orders
      - bfsi_dev.customer_accounts -> DELETE FROM bfsi_dev.customer_accounts WHERE balance = 0
    """
    return {
        "database": "bfsi_dev",
        "schemas": [
            {
                "name": "sales",
                "tables": [
                    {
                        "name": "orders",
                        "type": "BASE TABLE",
                        "row_count": 15320,
                        "columns": [
                            {"name": "order_id", "datatype": "bigint"},
                            {"name": "customer_id", "datatype": "bigint"},
                            {"name": "order_total", "datatype": "decimal"},
                        ],
                        "recent_statements": [
                            "TRUNCATE TABLE sales.orders",
                        ],
                    },
                ],
            },
            {
                "name": "bfsi_dev",
                "tables": [
                    {
                        "name": "customer_accounts",
                        "type": "BASE TABLE",
                        "row_count": 8420,
                        "columns": [
                            {"name": "account_id", "datatype": "bigint"},
                            {"name": "customer_name", "datatype": "varchar"},
                            {"name": "balance", "datatype": "decimal"},
                        ],
                        "recent_statements": [
                            "DELETE FROM bfsi_dev.customer_accounts WHERE balance = 0",
                        ],
                    },
                ],
            },
        ],
    }


def main() -> None:
    metadata = build_negative_scenario_metadata()
    report = layer1_Harness(metadata)

    print(f"Decision: {report['decision']}")
    print(f"Next step: {report['next_step']}")
    print(
        f"Errors: {report['summary']['total_errors']}, "
        f"Warnings: {report['summary']['total_warnings']}"
    )
    print()

    governance_section = next(
        s for s in report["sections"] if s["section"] == "governance_validation"
    )
    destructive_issues = [
        i for i in governance_section["issues"] if i["rule"] == "DESTRUCTIVE_SQL_DETECTED"
    ]

    if destructive_issues:
        print(f"[TRACKED] Harness Layer 1 flagged {len(destructive_issues)} destructive statement(s):")
        for issue in destructive_issues:
            print(f"  - {issue['message']}")
    else:
        print("[NOT TRACKED] Harness Layer 1 did NOT flag either destructive statement.")

    assert report["decision"] == "FAIL", (
        "Expected FAIL - a destructive statement should block the pipeline, "
        f"got '{report['decision']}'."
    )
    assert len(destructive_issues) == 2, (
        f"Expected 2 DESTRUCTIVE_SQL_DETECTED issues (one DELETE, one TRUNCATE), "
        f"got {len(destructive_issues)}."
    )

    print("\nNegative scenario PASSED: harness correctly tracks DELETE/TRUNCATE statements.")


if __name__ == "__main__":
    main()
