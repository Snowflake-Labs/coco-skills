# flake8: noqa: T201

"""
Base utilities for Snowflake Cortex Search RAG services.

SCOS Migrator - Base RAG Module
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from snowflake.core import Root
from snowflake.core.cortex.search_service import CortexSearchServiceCollection

from snowflake.snowpark import Session


@dataclass
class BaseRAGConfig:
    """Base configuration for Cortex RAG services."""

    database: str = "SCOS_MIGRATION"
    warehouse: str | None = None
    schema: str = "PUBLIC"
    table: str = "FAILURES"
    search_service: str = "FAILURES_SERVICE"
    target_lag: str = "60 seconds"
    stage: str = "FAILURES_STAGE"
    embedding_model: str = "snowflake-arctic-embed-l-v2.0"


class BaseCortexRAG(ABC):
    """Base class with common utilities for Cortex Search RAG services."""

    def __init__(self, session: Session, config: BaseRAGConfig) -> None:
        self.session = session
        self.config = config
        self._search_service: CortexSearchServiceCollection | None = (
            None  # Lazy initialized
        )

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[Any]:
        """Search for similar patterns. Implemented by subclasses."""
        pass

    @abstractmethod
    def _get_empty_prediction(self) -> dict[str, Any]:
        """Return empty prediction dict with proper keys for this RAG type."""
        pass

    @abstractmethod
    def _build_prediction(self, top_result: Any, results: list[Any]) -> dict[str, Any]:
        """Build prediction dict from search results."""
        pass

    def predict_failure(self, query: str, limit: int = 3) -> dict[str, Any]:
        """
        Predict if a given code/SQL snippet will fail based on similar patterns.

        Args:
            query: The code or SQL to analyze.
            limit: Maximum number of similar patterns to return.

        Returns:
            Dict with prediction results including failure_likelihood and similar_patterns.
        """
        results = self.search(query, limit=limit)
        if not results:
            return self._get_empty_prediction()
        return self._build_prediction(results[0], results)

    @property
    def search_service(self) -> CortexSearchServiceCollection:
        """
        Get the Cortex Search Service reference (cached after first access).

        Returns:
            CortexSearchService object for semantic search.
        """
        if self._search_service is None:
            cfg = self.config
            self._search_service = (
                Root(self.session)
                .databases[cfg.database]
                .schemas[cfg.schema]
                .cortex_search_services[cfg.search_service]
            )
        return self._search_service

    def create_table(self, columns_sql: str) -> None:
        """Create the database and table if they don't exist."""
        cfg = self.config
        # TODO: Figure out why this only works with accoutadmin
        # self.session.sql("use role accountadmin").collect()
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

    def create_search_service(
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

        # Upload to stage
        self.session.sql(f"USE DATABASE {cfg.database}").collect()
        self.session.sql(f"CREATE STAGE IF NOT EXISTS {cfg.stage}").collect()
        self.session.file.put(
            str(csv_path.absolute()),
            f"@{cfg.stage}",
            auto_compress=False,
            overwrite=True,
        )
        stage_path = f"@{cfg.stage}/{csv_path.name}"

        # Copy from stage
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
