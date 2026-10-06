"""
Standalone demo/test for the Harness Layer 2 phantom-column hallucination
check in Layer.py's evaluate_table_summary(). Exercises just the Evaluator
in isolation - no database connection, Azure AI Foundry call, or full scan
needed - by handing it a hand-built ground-truth columns_df and a
hand-written "Generator output" summary string that injects a column the
ground truth doesn't have.

Run directly:
    cd BackEnd
    python -m HarnessLayers.layer2.test_hallucination_demo
"""
import pandas as pd

from HarnessLayers.layer2.Layer import EvaluatorGeneratorHarness


def build_ground_truth_columns() -> pd.DataFrame:
    """The "real" schema, standing in for what a live DB scan would produce."""
    rows = [
        {"SchemaName": "dbo", "TableName": "Customers", "ColumnName": "CustomerID"},
        {"SchemaName": "dbo", "TableName": "Customers", "ColumnName": "Name"},
        {"SchemaName": "dbo", "TableName": "Customers", "ColumnName": "Email"},
        {"SchemaName": "dbo", "TableName": "Customers", "ColumnName": "CreatedDate"},
    ]
    return pd.DataFrame(rows)


def run_case(name, table_name, schema_name, summary_text, columns_df, expect_hallucination):
    harness = EvaluatorGeneratorHarness()
    result = harness.evaluate_table_summary(table_name, schema_name, summary_text, columns_df=columns_df)
    hallucinations = [i for i in result["issues"] if i["rule"] == "NO_HALLUCINATED_COLUMNS"]

    print(f"--- {name} ---")
    print(f"Summary text: {summary_text!r}")
    if hallucinations:
        for issue in hallucinations:
            print(f"  {issue['message']}")
    else:
        print("  No hallucinations detected.")

    found = bool(hallucinations)
    ok = found == expect_hallucination
    print("  RESULT:", "PASS" if ok else "FAIL", f"(expected hallucination={expect_hallucination}, got={found})")
    print()
    return ok


def run_rule_case(name, table_name, schema_name, summary_text, columns_df, expected_rule):
    """
    Sibling of run_case() for the structural "- Columns:" list check
    (_parse_claimed_columns() / evaluate_table_summary()'s section 5) -
    checks that a specific rule fires, rather than just the prose-quote
    NO_HALLUCINATED_COLUMNS check run_case() exercises.
    """
    harness = EvaluatorGeneratorHarness()
    result = harness.evaluate_table_summary(table_name, schema_name, summary_text, columns_df=columns_df)
    matches = [i for i in result["issues"] if i["rule"] == expected_rule]

    print(f"--- {name} ---")
    print(f"Summary text: {summary_text!r}")
    if matches:
        for issue in matches:
            print(f"  {issue['message']}")
    else:
        print(f"  No '{expected_rule}' issue raised.")

    ok = bool(matches)
    print("  RESULT:", "PASS" if ok else "FAIL", f"(expected rule={expected_rule!r} to fire)")
    print()
    return ok


def main():
    columns_df = build_ground_truth_columns()

    # Scenario 1 (from the test matrix): inject a non-existent column
    # ('ssn') into the Generator's output that isn't in columns_df.
    # Backtick/quote it the way the detector looks for real column
    # references - see _find_hallucinated_columns()'s docstring in Layer.py
    # for why bare, unquoted mentions are deliberately not matched.
    case1 = run_case(
        "Phantom column injected",
        table_name="Customers",
        schema_name="dbo",
        summary_text=(
            "The Customers table stores core customer profile data, including "
            "the `CustomerID` primary key and `Name`. It also appears to store "
            "the `ssn` column, which may contain sensitive PII."
        ),
        columns_df=columns_df,
        expect_hallucination=True,
    )

    # Scenario 2: control case - a clean summary referencing only real
    # columns should NOT be flagged.
    case2 = run_case(
        "Clean summary (no phantom columns)",
        table_name="Customers",
        schema_name="dbo",
        summary_text=(
            "The Customers table stores core customer profile data, including "
            "`CustomerID`, `Name`, and `Email`, with `CreatedDate` tracking "
            "when each record was added."
        ),
        columns_df=columns_df,
        expect_hallucination=False,
    )

    # Scenario 3: the Generator's structured "- Columns:" bullet list drops
    # a real column ('CreatedDate') that prose-only scanning (case1/case2
    # above) would never look at, since bullet lines aren't backtick-quoted.
    case3 = run_rule_case(
        "Columns list drops a real column",
        table_name="Customers",
        schema_name="dbo",
        summary_text=(
            "Table Name: Customers\nSchema: dbo\n\nGeneral Info:\n"
            "- Row Count: 100\n- Size (MB): 1\n- Size Category: Small\n"
            "- Table Type: Base Table\n\n"
            "- Total Columns: 3\n- Columns:\n"
            "  * CustomerID (int)\n  * Name (varchar)\n  * Email (varchar)\n"
            "- Primary Keys: CustomerID\n- Foreign Keys: None\n\n"
            "Summary: Stores core customer profile data."
        ),
        columns_df=columns_df,
        expected_rule="MISSING_COLUMNS_IN_SUMMARY",
    )

    # Scenario 4: the bullet list fabricates a column that isn't
    # backtick-quoted anywhere, so the prose-only check would miss it too.
    case4 = run_rule_case(
        "Columns list fabricates a column",
        table_name="Customers",
        schema_name="dbo",
        summary_text=(
            "Table Name: Customers\nSchema: dbo\n\nGeneral Info:\n"
            "- Row Count: 100\n- Size (MB): 1\n- Size Category: Small\n"
            "- Table Type: Base Table\n\n"
            "- Total Columns: 5\n- Columns:\n"
            "  * CustomerID (int)\n  * Name (varchar)\n  * Email (varchar)\n"
            "  * CreatedDate (datetime)\n  * ssn (varchar)\n"
            "- Primary Keys: CustomerID\n- Foreign Keys: None\n\n"
            "Summary: Stores core customer profile data."
        ),
        columns_df=columns_df,
        expected_rule="NO_HALLUCINATED_COLUMNS",
    )

    # Scenario 5: claimed "- Total Columns:" disagrees with the real count,
    # even though every individually-named column happens to be real.
    case5 = run_rule_case(
        "Total Columns count disagrees with ground truth",
        table_name="Customers",
        schema_name="dbo",
        summary_text=(
            "Table Name: Customers\nSchema: dbo\n\nGeneral Info:\n"
            "- Row Count: 100\n- Size (MB): 1\n- Size Category: Small\n"
            "- Table Type: Base Table\n\n"
            "- Total Columns: 6\n- Columns:\n"
            "  * CustomerID (int)\n  * Name (varchar)\n  * Email (varchar)\n"
            "  * CreatedDate (datetime)\n"
            "- Primary Keys: CustomerID\n- Foreign Keys: None\n\n"
            "Summary: Stores core customer profile data."
        ),
        columns_df=columns_df,
        expected_rule="COLUMN_COUNT_MISMATCH",
    )

    # Scenario 6: coverage check - a table that never gets an
    # evaluate_table_summary() call at all must not silently disappear.
    print("--- Table coverage check (a table silently skipped) ---")
    harness = EvaluatorGeneratorHarness()
    harness.evaluate_table_summary(
        "Customers", "dbo",
        "Table Name: Customers\nSchema: dbo\n- Total Columns: 4\n- Columns:\n"
        "  * CustomerID (int)\n  * Name (varchar)\n  * Email (varchar)\n  * CreatedDate (datetime)\n"
        "Summary: Stores core customer profile data.",
        columns_df=columns_df,
    )
    # Only 1 of 2 expected tables evaluated - simulates the second table's
    # agent call raising and being caught upstream in Agents_PipeLine.py.
    harness.finalize_table_evaluations(expected_table_count=2)
    coverage_section = next(s for s in harness.sections if s["section"] == "evaluator_table_assessment")
    coverage_issues = [i for i in coverage_section["issues"] if i["rule"] == "TABLE_COVERAGE_INCOMPLETE"]
    case6 = bool(coverage_issues) and not coverage_section["passed"]
    if coverage_issues:
        for issue in coverage_issues:
            print(f"  {issue['message']}")
    print("  RESULT:", "PASS" if case6 else "FAIL", "(expected TABLE_COVERAGE_INCOMPLETE to fire and fail the section)")
    print()

    if case1 and case2 and case3 and case4 and case5 and case6:
        print("All scenarios behaved as expected.")
    else:
        print("One or more scenarios did NOT behave as expected - see RESULT lines above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
