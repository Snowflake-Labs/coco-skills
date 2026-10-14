# flake8: noqa: T201

"""
SCOS Compatibility RAG interface using Snowflake Cortex Search Service.

Embeddings computed on "code" column to find similar failing patterns
for both SQL and DataFrame code in a single search.

Schema:
    - test_name: Source test name for tracking (optional, for KB maintenance)
    - code: Problematic SQL or DataFrame code (searchable)
    - root_cause: Why it fails on SCOS
    - additional_notes: Workarounds, JIRA links, fix status, etc.

Usage:
    Given a PySpark code snippet or Spark SQL, find similar patterns that have failed
    and return root cause analysis and additional notes.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing_extensions import Self

from snowflake.core import Root
from snowflake.core.cortex.search_service import CortexSearchServiceCollection
from snowflake.snowpark import Session

logger = logging.getLogger(__name__)

from .base import BaseRAG, SCOSSearchResult

DATA_DIR = Path(__file__).parent.parent / "data"


@dataclass
class SCOSRAGConfig:
    """Configuration for the SCOS Cortex Search RAG service."""

    database: str = ""  # Auto-discovered at runtime; set explicitly only for init()
    warehouse: str | None = None
    schema: str = "PUBLIC"
    table: str = "SCOS_COMPAT_ISSUES"
    search_service: str = "SCOS_COMPAT_ISSUES_SERVICE"
    target_lag: str = "60 seconds"
    stage: str = "SCOS_COMPAT_ISSUES_STAGE"
    embedding_model: str = "snowflake-arctic-embed-l-v2.0"


class SCOSCortexRAG(BaseRAG):
    """
    SCOS Compatibility RAG using Snowflake Cortex Search.

    Finds similar failing SQL and DataFrame patterns for migration analysis.
    """

    def __init__(self, session: Session, config: SCOSRAGConfig | None = None) -> None:
        super().__init__()  # SNOW-3347479: Initialize BaseRAG cache
        self.session = session
        self.config = config or SCOSRAGConfig()
        self._search_service: CortexSearchServiceCollection | None = None

    @classmethod
    def discover(
        cls,
        session: Session,
        service_name: str = "SCOS_COMPAT_ISSUES_SERVICE",
    ) -> Self:
        """Auto-discover the Cortex Search service across all databases visible to the current role.

        Runs ``SHOW CORTEX SEARCH SERVICES`` and returns a configured instance
        pointing at whichever database/schema hosts *service_name*.

        Raises:
            LookupError: If no service with the given name is visible.
        """
        rows = session.sql("SHOW CORTEX SEARCH SERVICES").collect()
        for row in rows:
            if row["name"] == service_name and "SCOS" in row["database_name"].upper():
                db = row["database_name"]
                schema = row["schema_name"]
                logger.info("Discovered Cortex Search service %s in %s.%s", service_name, db, schema)
                return cls(
                    session,
                    config=SCOSRAGConfig(
                        database=db,
                        schema=schema,
                        search_service=service_name,
                        table="SCOS_COMPAT_ISSUES",
                    ),
                )
        raise LookupError(
            f"Cortex Search service '{service_name}' not found in any database "
            f"visible to role {session.sql('SELECT CURRENT_ROLE()').collect()[0][0]}. "
            "Run with --rag-backend remote (default) or initialize the RAG with --init."
        )

    @property
    def search_service(self) -> CortexSearchServiceCollection:
        """Get the Cortex Search Service reference (cached after first access)."""
        if self._search_service is None:
            cfg = self.config
            self._search_service = (
                Root(self.session)
                .databases[cfg.database]
                .schemas[cfg.schema]
                .cortex_search_services[cfg.search_service]
            )
        return self._search_service

    def init(self) -> Self:
        """Initialize the database, table, and Cortex Search Service."""
        self._create_table(
            """
            test_name VARCHAR,
            code VARCHAR,
            root_cause VARCHAR,
            additional_notes VARCHAR
            """
        )
        self._create_search_service(
            search_column="code",
            attributes=["test_name", "root_cause", "additional_notes"],
            select_columns=["test_name", "code", "root_cause", "additional_notes"],
        )
        return self

    def upload_csv(self, csv_path: str | Path) -> int:
        """
        Upload failures from a CSV file.

        Expected CSV format:
            test_name,code,root_cause,additional_notes

        Args:
            csv_path: Path to CSV file (relative to data/ directory or absolute)

        Returns:
            Number of rows loaded.
        """
        if not Path(csv_path).is_absolute():
            csv_path = DATA_DIR / csv_path

        return self._append_csv(
            csv_path,
            columns="test_name, code, root_cause, additional_notes",
            select_expr="NULLIF($1, ''), $2, NULLIF($3, ''), NULLIF($4, '')",
        )

    def search(self, query: str, limit: int = 5) -> list[SCOSSearchResult]:
        """
        Semantic search for similar failure patterns.

        Args:
            query: The PySpark code or SQL to search for similar patterns.
            limit: Maximum number of results to return.

        Returns:
            List of SCOSSearchResult with similar failing patterns.
        """
        response = self.search_service.search(
            query=query,
            columns=["test_name", "code", "root_cause", "additional_notes"],
            limit=limit,
        )
        return [SCOSSearchResult.from_response(r) for r in response.results]

    # --- Snowflake infrastructure helpers ---

    def _create_table(self, columns_sql: str) -> None:
        """Create the database and table if they don't exist."""
        cfg = self.config
        # TODO: Figure out why this only works with accountadmin
        self.session.sql(f"CREATE DATABASE IF NOT EXISTS {cfg.database}").collect()
        self.session.sql(f"USE DATABASE {cfg.database}").collect()
        self.session.sql(
            f"""
            CREATE TABLE IF NOT EXISTS {cfg.table} (
                created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                {columns_sql}
            )
        """
        ).collect()

    def _create_search_service(
        self,
        search_column: str,
        attributes: list[str],
        select_columns: list[str],
    ) -> None:
        """Create the Cortex Search Service if it doesn't exist."""
        cfg = self.config
        if not cfg.warehouse:
            raise ValueError(
                "warehouse is required for creating the Cortex Search Service. "
                "Please provide --warehouse <name> when initializing the RAG."
            )
        attrs = ", ".join(attributes)
        select_cols = ", ".join(select_columns)
        self.session.sql(
            f"""
            CREATE CORTEX SEARCH SERVICE IF NOT EXISTS {cfg.search_service}
            ON {search_column}
            ATTRIBUTES {attrs}
            WAREHOUSE = {cfg.warehouse}
            TARGET_LAG = '{cfg.target_lag}'
            EMBEDDING_MODEL = '{cfg.embedding_model}'
            AS (
                SELECT {select_cols}
                FROM {cfg.table}
            )
        """
        ).collect()

    def _append_csv(self, csv_path: str | Path, columns: str, select_expr: str) -> int:
        """
        Append data from a CSV file via stage.

        Args:
            csv_path: Path to the CSV file.
            columns: Comma-separated column names to insert into.
            select_expr: SELECT expression mapping CSV columns ($1, $2, ...) to table columns.

        Returns:
            Number of rows loaded.
        """
        cfg = self.config
        csv_path = Path(csv_path)

        self.session.sql(f"USE DATABASE {cfg.database}").collect()
        self.session.sql(f"CREATE STAGE IF NOT EXISTS {cfg.stage}").collect()
        self.session.file.put(
            str(csv_path.absolute()),
            f"@{cfg.stage}",
            auto_compress=False,
            overwrite=True,
        )
        stage_path = f"@{cfg.stage}/{csv_path.name}"

        result = self.session.sql(
            f"""
            COPY INTO {cfg.table} ({columns})
            FROM (
                SELECT {select_expr}
                FROM {stage_path}
            )
            FILE_FORMAT = (
                TYPE = CSV
                FIELD_OPTIONALLY_ENCLOSED_BY = '"'
                ESCAPE_UNENCLOSED_FIELD = NONE
                SKIP_HEADER = 1
            )
            ON_ERROR = CONTINUE
        """
        ).collect()
        return result[0]["rows_loaded"] if result else 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Initialize SCOS RAG knowledge base and upload CSV data"
    )
    parser.add_argument(
        "--connection",
        type=str,
        default="default",
        help="Snowflake connection name (default: default)",
    )
    parser.add_argument(
        "--warehouse",
        type=str,
        required=True,
        help="Snowflake warehouse name (required for creating the Cortex Search Service)",
    )
    args = parser.parse_args()

    # Treat "default" as "use the connector's configured default connection"
    # (honors SNOWFLAKE_DEFAULT_CONNECTION_NAME / config.toml default_connection_name)
    # rather than forcing an entry literally named "default".
    _builder = Session.builder
    if args.connection and args.connection != "default":
        _builder = _builder.config("connection_name", args.connection)
    session = _builder.create()

    rag = SCOSCortexRAG(
        session,
        config=SCOSRAGConfig(
            warehouse=args.warehouse,
            table="SCOS_COMPAT_ISSUES",
            search_service="SCOS_COMPAT_ISSUES_SERVICE",
        ),
    ).init()

    rag_files = [
        "df_test_rca_normalized.csv",
        "sql_test_rca_normalized.csv",
        "expectation_tests_xfail_rca_normalized.csv",
        "jira_rca_normalized.csv",
        # SNOW-3347463: Scala and Databricks compatibility patterns
        "scala_test_rca_normalized.csv",
        "dbx_compat_rca_normalized.csv",
        # SNOW-3319145: ML, UDTF/UDAF, and Delta Lake patterns
        "ml_compat_rca_normalized.csv",
        "udtf_udaf_compat_rca_normalized.csv",
        "delta_lake_compat_rca_normalized.csv",
    ]

    for file in rag_files:
        rag.upload_csv(file)

    # Test query - can be SQL or DataFrame code
    test_code = """
df.select(col("date"), expr("add_months(to_date(date), 1)"))
    """

    print("\n" + "=" * 60)
    print("QUERY:", test_code.strip())
    print("=" * 60)

    prediction = rag.predict_failure(test_code)

    print(f"\nFailure Likelihood: {prediction['failure_likelihood']:.1f}%")

    if prediction["matching_code"]:
        print(f"\nMatching Code: {prediction['matching_code'][:100]}...")
        print(f"Root Cause: {prediction['root_cause']}")
        print(f"Additional Notes: {prediction['additional_notes']}")
        print(f"Test Name: {prediction['test_name']}")

    print("\n--- Similar Patterns ---")
    for idx, result in enumerate(prediction["similar_patterns"]):
        print(f"\n[{idx + 1}] Similarity: {result.score:.1%}")
        code_preview = (
            result.code[:80] + "..." if len(result.code) > 80 else result.code
        )
        print(f"    Code: {code_preview}")
        print(f"    Root Cause: {result.root_cause}")
