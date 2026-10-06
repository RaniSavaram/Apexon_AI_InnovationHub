"""
Snowflake -> Microsoft Fabric artifact generator.

Thin, source-pinned entry point over fabric_generator_core.Generator() - see
that module's docstring for what actually gets created (Delta tables, a
Data Pipeline scaffold, Volume folders, and best-effort View/Stored
Procedure placeholders) from migration_plan.json. This file just fixes
source_system="snowflake" and reports its own filename back as
generator_script, so callers/UI see which script actually ran.

Snowflake has a pre-provisioned Lakehouse in
fabric_generator_core.SOURCE_LAKEHOUSE_MAP (same as Databricks/SQL Server/
Dynamics 365): workspace "Fabric Insights" (bae3b540-d044-45e0-8c52-
3cf4ee3dcb31), lakehouse "Snowflake_Lakehouse" (1fef9fdf-9c8c-47f2-9b08-
4fa690a8b754). resolve_artifact_lakehouse() uses it directly - no
get-or-create call needed.

Usage
-----
    python snowflake2_fabric.py [--json path/to/migration_plan.json] \\
        [--dry-run] [--database-name sales_prod]

--dry-run skips Azure auth, the Lakehouse get-or-create call, and OneLake
writes entirely; it just prints what would be created/updated and where.
"""
import argparse
from pathlib import Path

try:
    from Artifacts_Generator import fabric_generator_core as core
except ImportError:
    # Fallback for running this script directly (e.g. `python
    # snowflake2_fabric.py` from inside Artifacts_Generator/) where
    # BackEnd isn't on sys.path as a package root the way Django's app
    # loading puts it.
    import fabric_generator_core as core

SOURCE_SYSTEM = "snowflake"
SCRIPT_NAME = Path(__file__).name


def Generator(json_path=None, dry_run=False, database_name=None, workspace_id=None):
    """Generate/synchronize Snowflake-sourced Fabric artifacts. See
    fabric_generator_core.Generator() for the full behavior."""
    return core.Generator(
        json_path=json_path,
        dry_run=dry_run,
        source_system=SOURCE_SYSTEM,
        database_name=database_name,
        workspace_id=workspace_id,
        generator_script=SCRIPT_NAME,
    )


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--json", default=None, help="Path to migration_plan.json")
    parser.add_argument("--dry-run", action="store_true", help="Skip Azure auth / OneLake writes")
    parser.add_argument(
        "--database-name",
        default=None,
        help="Source database name used to build the artifact lakehouse name, e.g. 'sales_prod'"
    )

    args = parser.parse_args()

    json_path = Path(args.json).resolve() if args.json else None

    Generator(
        json_path=json_path,
        dry_run=args.dry_run,
        database_name=args.database_name,
    )
