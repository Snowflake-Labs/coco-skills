"""
SCOS Migration Agent - RAG Module

Provides RAG services for finding similar failing PySpark code and SQL patterns.

Backends available:
  - SCOSCortexRAG:  Snowflake Cortex Search (fuzzy embedding)
  - SCOSRemoteRAG:  Remote HTTP endpoint (fuzzy embedding)
  - SCOSTriggerRAG: offline, exact-match trigger KB (no network, no embeddings)

All conform to the BaseRAG interface.
"""

from .base import BaseRAG, SCOSSearchResult
from .scos_rag import SCOSCortexRAG, SCOSRAGConfig
from .scos_remote_rag import SCOSRemoteRAG, SCOSRemoteRAGConfig
from .trigger_kb import SCOSTriggerRAG, TriggerKB

__all__ = [
    "BaseRAG",
    "SCOSSearchResult",
    "SCOSCortexRAG",
    "SCOSRAGConfig",
    "SCOSRemoteRAG",
    "SCOSRemoteRAGConfig",
    "SCOSTriggerRAG",
    "TriggerKB",
]
