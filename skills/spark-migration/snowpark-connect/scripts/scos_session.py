"""
Shared connectivity helpers for SCOS analyzer scripts.

Centralizes the Snowflake session bootstrap and RAG backend selection so that
``analyze_pyspark.py`` and ``analyze_scala.py`` (and any future analyzer) stay
in sync on CLI flags and backend fallback behavior.
"""

from __future__ import annotations

import argparse
import logging
import sys

from rag import (
    BaseRAG,
    SCOSCortexRAG,
    SCOSRemoteRAG,
    SCOSRemoteRAGConfig,
    SCOSTriggerRAG,
)
from snowflake.snowpark import Session

logger = logging.getLogger(__name__)

try:
    from snowflake.cortex import CompleteOptions, complete as cortex_complete
except ModuleNotFoundError:  # pragma: no cover - depends on host env packaging
    CompleteOptions = None  # type: ignore[assignment]
    cortex_complete = None  # type: ignore[assignment]

# Keep this in sync with the analyzers' DEFAULT_LLM_MODEL values.
# We intentionally probe the same model path used during Phase 1.
DEFAULT_LLM_MODEL = "claude-opus-4-6"
_CORTEX_PROBE_PROMPT = (
    'Reply with exactly "OK". Do not include markdown or extra text.'
)
# Non-retryable markers are derived from real migration failures observed in logs
# (403/Forbidden, missing CORTEX.COMPLETE function, and account/role privilege
# errors such as "USE AI FUNCTIONS"). These indicate deterministic auth/config
# problems where retry backoff only wastes time.
_NON_RETRYABLE_LLM_MARKERS = (
    "403",
    "forbidden",
    "unknown user-defined function snowflake.cortex.complete",
    "unknown function snowflake.cortex.complete",
    "does not exist or not authorized",
    "insufficient privileges",
    "use ai functions",
    "not authorized",
)


def add_connectivity_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--connection`` and ``--rag-backend`` flags to *parser*."""
    parser.add_argument(
        "--connection",
        type=str,
        default="default",
        help="Snowflake connection name (default: default)",
    )
    parser.add_argument(
        "--rag-backend",
        choices=["remote", "cortex", "trigger"],
        default="trigger",
        help=(
            "RAG backend: 'trigger' (offline exact-match KB, DEFAULT — no network, "
            "no embeddings; risk comes from curated severity), 'remote' (WebAPI; "
            "falls back to Cortex Search if unreachable), or 'cortex' (Snowflake "
            "Cortex Search directly — no fallback)"
        ),
    )


def open_session(connection_name: str | None = None) -> Session:
    """Open a Snowpark session and exit on failure.

    When *connection_name* is ``None``, empty, or the ``"default"`` sentinel, we
    do **not** pin ``connection_name`` and instead let the Snowflake connector
    resolve the configured default connection. Resolution order (increasing
    precedence): a connection literally named ``default`` in ``connections.toml``,
    the ``default_connection_name`` key in ``config.toml``, then the
    ``SNOWFLAKE_DEFAULT_CONNECTION_NAME`` environment variable. Pinning the literal
    string ``"default"`` would bypass the latter two, so we treat it as "use the
    configured default" rather than "require an entry literally named default".

    Only an explicit, non-default name is pinned via ``connection_name``.
    """
    use_default = not connection_name or connection_name == "default"
    label = "<configured default>" if use_default else connection_name
    logger.info("\nConnecting to Snowflake (connection: %s)...", label)
    try:
        builder = Session.builder
        if not use_default:
            builder = builder.config("connection_name", connection_name)
        return builder.create()
    except Exception as exc:
        logger.error("Error connecting to Snowflake: %s", exc)
        logger.info(
            "\nMake sure you have a valid connection configured via "
            "connections.toml / config.toml (default_connection_name) or the "
            "SNOWFLAKE_DEFAULT_CONNECTION_NAME environment variable."
        )
        sys.exit(1)


def get_session_identity(session: Session) -> tuple[str, str, str]:
    """Return ``(account, user, role)`` for diagnostics."""
    try:
        row = session.sql(
            "SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE()"
        ).collect()[0]
    except Exception as exc:
        raise RuntimeError(
            f"Could not fetch session identity (account/user/role): {exc}"
        ) from exc
    return str(row[0]), str(row[1]), str(row[2])


def verify_cortex_complete_access(
    session: Session,
    *,
    model: str = DEFAULT_LLM_MODEL,
) -> str:
    """Run a minimal ``SNOWFLAKE.CORTEX.COMPLETE`` probe.

    Raises:
        RuntimeError: If the probe fails, returns empty output, or cannot be parsed.
    """
    if cortex_complete is None or CompleteOptions is None:
        raise RuntimeError(
            "CORTEX.COMPLETE probe unavailable: snowflake.cortex module is not installed"
        )
    try:
        response = cortex_complete(
            model,
            _CORTEX_PROBE_PROMPT,
            options=CompleteOptions(temperature=0.0),
            session=session,
        )
    except Exception as exc:
        raise RuntimeError(f"CORTEX.COMPLETE probe failed: {exc}") from exc

    text = str(response).strip()
    if not text:
        raise RuntimeError("CORTEX.COMPLETE probe returned empty output")
    return text


def is_non_retryable_llm_error(exc: Exception) -> bool:
    """Return True for auth/permission/function errors that should fail fast."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _NON_RETRYABLE_LLM_MARKERS)


def _fetch_snowflake_identifiers(
    session: Session,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(sessionId, user, account)`` for WebAPI auth, or ``(None, None, None)`` on failure."""
    try:
        row = session.sql(
            "SELECT CURRENT_SESSION(), CURRENT_USER(), CURRENT_ACCOUNT()"
        ).collect()[0]
        return str(row[0]), str(row[1]), str(row[2])
    except Exception as exc:  # pragma: no cover — surfaces as a WebAPI failure downstream
        logger.warning("Could not fetch Snowflake session identifiers: %s", exc)
        return None, None, None


def _build_remote_rag(session: Session) -> SCOSRemoteRAG:
    """Build a :class:`SCOSRemoteRAG` and probe connectivity with a single auth call."""
    sess_id, sf_user, sf_account = _fetch_snowflake_identifiers(session)
    if not all([sess_id, sf_user, sf_account]):
        raise RuntimeError(
            "Could not resolve Snowflake session identifiers required for WebAPI auth"
        )
    cfg = SCOSRemoteRAGConfig(
        snowflake_session_id=sess_id,
        snowflake_user=sf_user,
        snowflake_account=sf_account,
    )
    rag = SCOSRemoteRAG(config=cfg)
    # Connectivity + auth probe: hits /auth/token once.
    rag._ensure_authenticated()
    return rag


def build_rag(session: Session, backend: str, *, language: str = "python") -> BaseRAG:
    """Build a RAG backend.

    - ``backend == "trigger"`` (default): offline exact-match trigger KB. No
      session/network needed; risk is curated severity, not cosine similarity.
    - ``backend == "cortex"``: explicit user choice. Uses
      :meth:`SCOSCortexRAG.discover` against the given *session* with no
      fallback to remote.
    - ``backend == "remote"``: try the WebAPI first; on any failure fall back to
      Cortex Search via :meth:`SCOSCortexRAG.discover`. If both backends are
      unavailable, log an error and :func:`sys.exit(1)`.

    ``language`` is forwarded to :class:`SCOSTriggerRAG` so Python-only rules
    (e.g. ``python_attribute`` kinds) are skipped when analysing Java/Scala code.
    """
    if backend == "trigger":
        # Offline exact-match KB. No session/network needed; risk is the
        # curated rule severity rather than cosine similarity.
        scos_rag: BaseRAG = SCOSTriggerRAG(language=language)
        logger.info(
            "Using offline trigger KB backend (explicit --rag-backend trigger): "
            "%d rules loaded",
            len(scos_rag.kb.rules),
        )
        return scos_rag

    if backend == "cortex":
        # Explicit user choice — no fallback to remote.
        scos_rag = SCOSCortexRAG.discover(session)
        logger.info("Using Cortex Search RAG backend (explicit --rag-backend cortex)")
        return scos_rag

    # Default: try remote WebAPI first, fall back to Cortex Search on failure.
    try:
        scos_rag = _build_remote_rag(session)
        logger.info("Using remote WebAPI RAG backend")
        return scos_rag
    except Exception as exc:
        logger.warning(
            "Remote WebAPI RAG unavailable (%s) — falling back to Cortex Search",
            exc,
        )
        try:
            scos_rag = SCOSCortexRAG.discover(session)
            logger.info("Using Cortex Search RAG backend (fallback)")
            return scos_rag
        except Exception as cex:
            logger.error(
                "Neither remote WebAPI nor Cortex Search are available: %s",
                cex,
            )
            sys.exit(1)
