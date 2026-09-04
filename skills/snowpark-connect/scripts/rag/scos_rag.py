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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from snowflake.snowpark import Session

from .base import BaseCortexRAG, BaseRAGConfig

DATA_DIR = Path(__file__).parent.parent / "data"


@dataclass
class SCOSRAGConfig(BaseRAGConfig):
    """Configuration for the SCOS compatibility RAG service."""

    table: str = "SCOS_COMPAT_ISSUES"
    search_service: str = "SCOS_COMPAT_ISSUES_SERVICE"
    stage: str = "SCOS_COMPAT_ISSUES_STAGE"


@dataclass
class SCOSSearchResult:
    """A search result from the SCOS RAG service."""

    code: str
    score: float
    root_cause: str | None = None
    additional_notes: str | None = None
    test_name: str | None = None

    @property
    def will_likely_fail(self) -> bool:
        """Returns True if this pattern indicates a failure."""
        return self.root_cause is not None

    @classmethod
    def from_response(cls, data: dict) -> Self:
        cosine_similarity = data.get("@scores", {}).get("cosine_similarity", 0.0)

        return cls(
            code=data.get("code", ""),
            score=cosine_similarity,
            root_cause=data.get("root_cause") or None,
            additional_notes=data.get("additional_notes") or None,
            test_name=data.get("test_name") or None,
        )


class SCOSCortexRAG(BaseCortexRAG):
    """
    SCOS Compatibility RAG using Snowflake Cortex Search.

    Finds similar failing SQL and DataFrame patterns for migration analysis.
    """

    def __init__(self, session: Session, config: SCOSRAGConfig | None = None) -> None:
        super().__init__(session, config or SCOSRAGConfig())

    def init(self) -> Self:
        """Initialize the database, table, and Cortex Search Service."""
        self.create_table(
            """
            test_name VARCHAR,
            code VARCHAR,
            root_cause VARCHAR,
            additional_notes VARCHAR
            """
        )
        self.create_search_service(
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

        # CSV columns: test_name, code, root_cause, additional_notes
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

    def _get_empty_prediction(self) -> dict[str, Any]:
        """Return empty prediction dict for failures."""
        return {
            "failure_likelihood": 0.0,
            "matching_code": None,
            "root_cause": None,
            "additional_notes": None,
            "test_name": None,
            "similar_patterns": [],
        }

    def _build_prediction(
        self, top_result: SCOSSearchResult, results: list[SCOSSearchResult]
    ) -> dict[str, Any]:
        """Build prediction dict from search results."""
        failure_likelihood = (
            top_result.score * 100 if top_result.will_likely_fail else 0.0
        )
        return {
            "failure_likelihood": failure_likelihood,
            "matching_code": top_result.code,
            "root_cause": top_result.root_cause,
            "additional_notes": top_result.additional_notes,
            "test_name": top_result.test_name,
            "similar_patterns": results,
        }


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

    session = Session.builder.config("connection_name", args.connection).create()

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
