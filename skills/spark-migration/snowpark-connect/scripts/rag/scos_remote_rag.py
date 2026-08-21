"""
SCOS Compatibility RAG interface using a remote search endpoint.

Endpoint contract (v1 — unauthenticated):
    POST  <base_url>/api/v1/scos/compatibility/search
    Body: {"query": "<code snippet>", "limit": <int>}
    Response: {"results": [{code, score, root_cause, additional_notes, test_name}, ...]}

Endpoint contract (v2 — authenticated, SNOW-3319329):
    POST  <base_url>/api/v1/auth/token
    Body: {"sessionId": "...", "user": "...", "account": "..."}
    Response: {"accessToken": "jwt...", "expiresAt": "...", "tokenType": "Bearer"}

    NOTE: The server binds the client IP from the TCP request
    (HttpContext.Connection.RemoteIpAddress) — the client MUST NOT send clientIp.

    POST  <base_url>/api/v2/scos/compatibility/search  (Authorization: Bearer <jwt>)
    Body: {"query": "...", "limit": N}
    Response: {"results": [...], "requestId": "..."}
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from .base import BaseRAG, SCOSSearchResult

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api-sit-assessment.azurewebsites.net"
DEFAULT_SEARCH_PATH = "/api/v1/scos/compatibility/search"

# SNOW-3319329: Auth endpoint default (single-endpoint flow — no challenge/verify ceremony)
DEFAULT_AUTH_TOKEN_PATH = "/api/v1/auth/token"
DEFAULT_SEARCH_PATH_V2 = "/api/v2/scos/compatibility/search"


@dataclass
class SCOSRemoteRAGConfig:
    """Configuration for the remote SCOS compatibility search endpoint."""

    base_url: str = DEFAULT_BASE_URL
    search_path: str = DEFAULT_SEARCH_PATH
    # SNOW-3319329: Raised from 30s. The dev-stage/prod WebAPI is an Azure App
    # Service that cold-starts after idling — the first request after a pause
    # routinely takes 30-60s while .NET warms up. 30s was too aggressive and
    # caused every first-batch request to time out and retry unnecessarily.
    timeout_seconds: int = 90
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})

    # SNOW-3319329: Auth configuration (single-endpoint token flow)
    auth_token_path: str = DEFAULT_AUTH_TOKEN_PATH
    search_path_v2: str = DEFAULT_SEARCH_PATH_V2
    use_auth: bool = True
    snowflake_session_id: str | None = None
    snowflake_user: str | None = None
    snowflake_account: str | None = None

    # SNOW-3319329: When True, issue a single /auth/token + tiny search on a
    # single worker during __init__ to pre-warm the Azure App Service before
    # the analyzer fans out to N parallel workers. Avoids the cold-start burst
    # where all N workers simultaneously wait >30s for the first response.
    warmup_on_init: bool = True

    # SNOW-3319329: Client-side query length safeguard.
    # The WebAPI's CortexSearchRequest DTO historically enforced
    # StringLength(5000) on the Query field (see migrations-sma-webapi PR #41).
    # migrations-sma-webapi PR #44 raises the cap to 100000; this default keeps
    # us under that with a small safety margin. Set to None to disable.
    max_query_chars: int | None = 99000

    @property
    def search_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.search_path}"

    # SNOW-3319329: Computed URL properties for auth endpoint
    @property
    def auth_token_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.auth_token_path}"

    @property
    def search_v2_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.search_path_v2}"


class SCOSRemoteRAG(BaseRAG):
    """
    SCOS Compatibility RAG backed by a remote HTTP search API.

    Production backend — supports both unauthenticated v1 and authenticated v2
    endpoints. When use_auth=True (default), authenticates via single /auth/token
    endpoint before calling the v2 secure endpoint. When use_auth=False, calls
    v1 directly. Errors propagate; the caller decides whether to fall back to
    another RAG backend (SNOW-3319329).
    """

    def __init__(self, config: SCOSRemoteRAGConfig | None = None) -> None:
        super().__init__()  # SNOW-3347479: Initialize BaseRAG cache
        self.config = config or SCOSRemoteRAGConfig()
        # SNOW-3319329: Token state for single-endpoint auth flow
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

        # SNOW-3319329: Pre-warm the Azure App Service on init so the first
        # parallel request from the analyzer doesn't race a cold start.
        if self.config.warmup_on_init and self.config.use_auth:
            try:
                self._warmup()
            except Exception as exc:
                logger.warning(
                    "SCOS remote RAG warmup failed (continuing anyway): %s", exc
                )

    # SNOW-3319329: Serial warmup — one auth + one tiny search to spin up the
    # Azure App Service before the analyzer fans out to N parallel workers.
    def _warmup(self) -> None:
        logger.info("SCOS remote RAG: warming up endpoint %s ...", self.config.base_url)
        self._ensure_authenticated()
        # One cheap search using a known-safe short payload. We don't care
        # about the response contents — just that the instance is warm.
        try:
            self._search_v2("df.collect()", limit=1)
            logger.info("SCOS remote RAG: warmup complete")
        except Exception as exc:
            # Propagate so the caller can log and continue (auth may have
            # succeeded even if this single search failed).
            raise

    # SNOW-3319329: Defensive client-side truncation to match the server-side
    # length constraint on CortexSearchRequest.Query. Large PySpark blocks
    # (data literals, multi-Row fixtures) otherwise trigger HTTP 400 from
    # DTO validation before the controller runs.
    def _truncate_query(self, query: str) -> str:
        cap = self.config.max_query_chars
        if cap is None or len(query) <= cap:
            return query
        logger.warning(
            "SCOS remote RAG: query length %d exceeds max_query_chars=%d; truncating",
            len(query),
            cap,
        )
        return query[:cap]

    # SNOW-3319329: Single-endpoint authentication — POST session details, get JWT back.
    def _authenticate(self) -> None:
        """
        Authenticate via single /auth/token endpoint to obtain a JWT access token.

        Posts Snowflake session details (sessionId, user, account); server validates
        and returns a signed JWT with IP binding.
        """
        cfg = self.config

        if not all([cfg.snowflake_session_id, cfg.snowflake_user, cfg.snowflake_account]):
            raise ValueError(
                "Authentication requires snowflake_session_id, snowflake_user, "
                "and snowflake_account to be set in SCOSRemoteRAGConfig"
            )

        token_payload = {
            "sessionId": cfg.snowflake_session_id,
            "user": cfg.snowflake_user,
            "account": cfg.snowflake_account,
        }

        # SNOW-3319329: Single POST — no challenge/nonce/QUERY_TAG ceremony
        token_resp = requests.post(
            cfg.auth_token_url,
            json=token_payload,
            headers={"Content-Type": "application/json"},
            timeout=cfg.timeout_seconds,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        self._access_token = token_data["accessToken"]

        # Parse expiry — server returns ISO 8601. When the server omits
        # expiresAt or the value is unparseable, fall back to a conservative
        # 30-minute expiry so the cache check in _ensure_authenticated
        # treats the token as usable and does not re-auth on every call.
        from datetime import timedelta
        expires_str = token_data.get("expiresAt", "")
        if expires_str:
            try:
                self._token_expires_at = datetime.fromisoformat(expires_str)
            except ValueError:
                self._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        else:
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

        logger.info("SCOS remote RAG: authenticated successfully (token expires: %s)", expires_str)

    def _ensure_authenticated(self) -> None:
        """Check if token exists and is not expired; re-authenticate if needed."""
        if self._access_token and self._token_expires_at:
            # Add 60-second buffer before expiry
            now = datetime.now(timezone.utc)
            expires = self._token_expires_at
            # Ensure both are tz-aware for comparison
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            from datetime import timedelta

            if now + timedelta(seconds=60) < expires:
                return  # Token still valid

        self._authenticate()

    def _search_v1(self, query: str, limit: int) -> list[SCOSSearchResult]:
        """Search via the unauthenticated v1 endpoint (original behavior)."""
        query = self._truncate_query(query)
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                resp = requests.post(
                    self.config.search_url,
                    json={"query": query, "limit": limit},
                    headers=self.config.headers,
                    timeout=self.config.timeout_seconds,
                )
                resp.raise_for_status()
                body = resp.json()
                results_raw = body if isinstance(body, list) else body.get("results", [])
                return [SCOSSearchResult.from_response(r) for r in results_raw]
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.config.max_retries - 1:
                    delay = self.config.backoff_base_seconds * (2 ** attempt)
                    logger.warning(
                        "SCOS remote search v1 attempt %d/%d failed: %s — retrying in %.1fs",
                        attempt + 1,
                        self.config.max_retries,
                        exc,
                        delay,
                    )
                    time.sleep(delay)

        raise last_exc  # type: ignore[misc]

    # SNOW-3319329: Authenticated v2 search
    def _search_v2(self, query: str, limit: int) -> list[SCOSSearchResult]:
        """Search via the authenticated v2 endpoint."""
        query = self._truncate_query(query)
        self._ensure_authenticated()

        def _auth_headers() -> dict[str, str]:
            return {
                **self.config.headers,
                "Authorization": f"Bearer {self._access_token}",
            }

        reauth_attempted = False
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                resp = requests.post(
                    self.config.search_v2_url,
                    json={"query": query, "limit": limit},
                    headers=_auth_headers(),
                    timeout=self.config.timeout_seconds,
                )
                # SNOW-3319329: On 401/403 from a stale token, clear state,
                # re-authenticate once, and retry the same attempt without
                # counting it against max_retries.
                if resp.status_code in (401, 403) and not reauth_attempted:
                    logger.info(
                        "SCOS remote search v2: received %d, clearing token and re-authenticating",
                        resp.status_code,
                    )
                    self._access_token = None
                    self._token_expires_at = None
                    reauth_attempted = True
                    self._authenticate()
                    continue
                resp.raise_for_status()
                body = resp.json()
                results_raw = body if isinstance(body, list) else body.get("results", [])
                return [SCOSSearchResult.from_response(r) for r in results_raw]
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.config.max_retries - 1:
                    delay = self.config.backoff_base_seconds * (2 ** attempt)
                    logger.warning(
                        "SCOS remote search v2 attempt %d/%d failed: %s — retrying in %.1fs",
                        attempt + 1,
                        self.config.max_retries,
                        exc,
                        delay,
                    )
                    time.sleep(delay)

        raise last_exc  # type: ignore[misc]

    def search(self, query: str, limit: int = 5) -> list[SCOSSearchResult]:
        """
        Semantic search for similar failure patterns via the remote endpoint.

        When use_auth is True (default), calls the authenticated v2 endpoint.
        When use_auth is False, explicitly calls the unauthenticated v1 endpoint.

        SNOW-3319329: v2 failures are NOT silently downgraded to v1. Callers
        (e.g., the analyzer orchestrator) decide whether to switch to a
        different RAG backend when the remote WebAPI is unreachable.

        Args:
            query: PySpark code or SQL to search for similar patterns.
            limit: Maximum number of results to return.

        Returns:
            List of SCOSSearchResult with matching patterns.

        Raises:
            requests.RequestException: On network error or non-2xx after retries.
            ValueError: If use_auth is True but Snowflake session fields are missing.
        """
        if self.config.use_auth:
            return self._search_v2(query, limit)
        return self._search_v1(query, limit)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Test the remote SCOS compatibility search endpoint"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=DEFAULT_BASE_URL,
        help=f"Base URL for the search API (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--query",
        type=str,
        default='df.select(col("date"), expr("add_months(to_date(date), 1)"))',
        help="Code snippet to search for",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of results (default: 3)",
    )
    args = parser.parse_args()

    rag = SCOSRemoteRAG(config=SCOSRemoteRAGConfig(base_url=args.base_url))
    prediction = rag.predict_failure(args.query, limit=args.limit)

    print("\n" + "=" * 60)
    print("QUERY:", args.query)
    print("=" * 60)

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
