# flake8: noqa: T201

"""
Base interface and shared types for SCOS compatibility RAG services.

Defines the ``BaseRAG`` ABC that both the Cortex Search (dev) and
Remote API (production) implementations conform to.
"""

from abc import ABC, abstractmethod
import hashlib  # SNOW-3347479: SHA-256 cache keys
import logging  # SNOW-3347479: cache stats logging
from dataclasses import dataclass
from typing import Any

from typing_extensions import Self

logger = logging.getLogger(__name__)


@dataclass
class SCOSSearchResult:
    """A search result from the SCOS RAG service."""

    code: str
    score: float
    root_cause: str | None = None
    additional_notes: str | None = None
    test_name: str | None = None
    # Fired token (e.g. "ceil") from exact-match backends; empty otherwise.
    matched_token: str = ""
    # True when the match is statically decidable: the firing condition is
    # structurally certain (an unconditionally-unsupported API, a signature
    # kwarg violation, or an attribute gateway like ``.rdd``), so a real call
    # is a guaranteed divergence regardless of curated severity. Fuzzy/cosine
    # backends always leave this False — only the exact-match trigger KB sets
    # it. Used by the analyzer to bypass the LLM adjudication step. Decidability
    # is orthogonal to severity: a LOW-severity unsupported API is still a
    # certain true positive.
    decidable: bool = False
    # Deterministic EWI code + status class from the rule catalog.
    ewi_code: str = ""
    status_class: str = ""

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


class BaseRAG(ABC):
    """
    Abstract base for SCOS compatibility RAG backends.

    Subclasses only need to implement ``search``; prediction logic is shared.

    SNOW-3347479: Includes in-memory query result caching (SHA-256 keyed)
    so identical queries across files are not re-executed.
    """

    def __init__(self) -> None:
        # SNOW-3347479: In-memory cache for search results
        self._cache: dict[str, list[SCOSSearchResult]] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[SCOSSearchResult]:
        """Search for similar failing patterns. Implemented by subclasses."""

    # SNOW-3347479: Cached wrapper around search()
    def search_cached(self, query: str, limit: int = 5) -> list[SCOSSearchResult]:
        """
        Search with in-memory caching. Returns cached results for repeated queries.

        Cache key is SHA-256 of ``query:limit`` to ensure uniqueness.
        """
        cache_key = hashlib.sha256(f"{query}:{limit}".encode()).hexdigest()
        if cache_key in self._cache:
            self._cache_hits += 1
            return self._cache[cache_key]
        self._cache_misses += 1
        results = self.search(query, limit=limit)
        self._cache[cache_key] = results
        return results

    def predict_failure(self, query: str, limit: int = 3) -> dict[str, Any]:
        """
        Predict if a given code/SQL snippet will fail based on similar patterns.

        Args:
            query: The code or SQL to analyze.
            limit: Maximum number of similar patterns to return.

        Returns:
            Dict with prediction results including failure_likelihood
            and similar_patterns.
        """
        # SNOW-3347479: Use cached search instead of direct search
        results = self.search_cached(query, limit=limit)
        if not results:
            return self._get_empty_prediction()
        return self._build_prediction(results[0], results)

    # SNOW-3347479: Cache statistics for logging
    def log_cache_stats(self) -> None:
        """Log cache hit/miss statistics."""
        total = self._cache_hits + self._cache_misses
        rate = (self._cache_hits / total * 100) if total > 0 else 0
        logger.info(
            "RAG cache: %d hits, %d misses (%.1f%% hit rate), %d unique queries",
            self._cache_hits,
            self._cache_misses,
            rate,
            len(self._cache),
        )

    @property
    def cache_stats(self) -> dict[str, int | float]:
        """Return cache statistics as a dict."""
        total = self._cache_hits + self._cache_misses
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": (self._cache_hits / total * 100) if total > 0 else 0,
            "unique_queries": len(self._cache),
        }

    @staticmethod
    def _get_empty_prediction() -> dict[str, Any]:
        return {
            "failure_likelihood": 0.0,
            "matching_code": None,
            "root_cause": None,
            "additional_notes": None,
            "test_name": None,
            "similar_patterns": [],
        }

    @staticmethod
    def _build_prediction(
        top_result: SCOSSearchResult,
        results: list[SCOSSearchResult],
    ) -> dict[str, Any]:
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
