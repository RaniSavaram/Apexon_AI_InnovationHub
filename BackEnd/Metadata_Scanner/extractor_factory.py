# extractor_factory.py
# Ported from app/BackEnd/Metadata_Scanner/extractor_factory.py
# Updated import paths for Django app layout.

from Metadata_Scanner.extractors.sqlserver import SQLServerExtractor

# Uncomment as additional extractors are implemented:
# from backend.api.Metadata_Scanner.extractors.oracle import OracleExtractor
# from backend.api.Metadata_Scanner.extractors.mysql import MySQLExtractor
# from backend.api.Metadata_Scanner.extractors.postgres import PostgreSQLExtractor
# from backend.api.Metadata_Scanner.extractors.sqlite import SQLiteExtractor
# from backend.api.Metadata_Scanner.extractors.snowflake import SnowflakeExtractor


class ExtractorFactory:

    @staticmethod
    def get(db_type: str):

        db_type = db_type.lower()

        mapping = {
            "sqlserver": SQLServerExtractor,
            # "oracle": OracleExtractor,
            # "mysql": MySQLExtractor,
            # "postgres": PostgreSQLExtractor,
            # "sqlite": SQLiteExtractor,
            # "snowflake": SnowflakeExtractor,
        }

        if db_type not in mapping:
            raise ValueError(f"Unsupported database type: {db_type}")

        return mapping[db_type]()
