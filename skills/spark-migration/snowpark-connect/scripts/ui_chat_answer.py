#!/usr/bin/env python3
"""ui_chat_answer.py — Cortex COMPLETE backend for the progress-UI chat window.

Two modes
---------
1. **One-shot** (default) — reads one JSON object from stdin, answers, exits.
2. **Serve** (``--serve``) — long-lived worker: reads one JSON object per stdin
   line, answers on one stdout line, and **reuses a single Snowflake session**.
   This is what the Progress UI server uses so browser SSO / SAML only fires
   once for the whole chat conversation.

Request JSON (either mode)::

    {
      "context":    "<compact live-run status text>",
      "messages":   [{"role": "user"|"assistant", "content": "..."}, ...],
      "connection": "default",          # optional
      "model":      "claude-opus-4-6"   # optional
    }

Prints one JSON line to stdout: ``{"answer": "..."}`` or ``{"error": "..."}``.
Always exits 0 in one-shot mode. Serve mode exits only on stdin EOF / fatal.

Set ``SCOS_UI_CHAT_MOCK=1`` to skip Snowflake and echo a canned answer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading

# scos_session lives in this same directory; Python puts the script's dir on
# sys.path[0], so this resolves under `uv run` exactly as check_cortex_llm_access.py does.
try:
    from scos_session import DEFAULT_LLM_MODEL, open_session
except Exception:  # pragma: no cover - only when run outside the uv project
    DEFAULT_LLM_MODEL = "claude-opus-4-6"
    open_session = None  # type: ignore[assignment]

MAX_TURNS = 20          # cap history sent to the model
MAX_CHARS_PER_MSG = 4000
MAX_OUTPUT_TOKENS = 800

SYSTEM_PREAMBLE = (
    "You are an assistant embedded in a live Spark-to-Snowflake (Snowpark Connect / "
    "SCOS) migration dashboard. Help the user understand (a) the CURRENT RUN's "
    "progress from the live status below and (b) the SCOS migration and validation "
    "process in general.\n"
    "Rules:\n"
    "- Answer concisely and factually. Prefer specifics from the live status "
    "(phase, counts, failures, reports) over generalities.\n"
    "- If the live status does not contain the answer, say so plainly rather than "
    "guessing about this particular run.\n"
    "- You are READ-ONLY. You observe and explain; you never change files, re-run "
    "phases, or claim to have performed any action.\n"
    "- Do not invent file names, counts, or errors that are not in the status.\n"
    "- Format for a compact chat bubble: short paragraphs, **bold** labels, and "
    "bullet lists. Avoid huge walls of text and avoid emoji."
)


def _open_session_safe(connection_name: str):
    """Open a Snowpark session without calling ``sys.exit`` on failure.

    ``scos_session.open_session`` exits the process on error, which would kill
    the long-lived ``--serve`` worker. Mirror its connection-name semantics
    here and raise instead.
    """
    from snowflake.snowpark import Session

    use_default = not connection_name or connection_name == "default"
    builder = Session.builder
    if not use_default:
        builder = builder.config("connection_name", connection_name)
    return builder.create()


def _canned(context: str, question: str) -> str:
    """Demo-mode: return context-aware canned answers without hitting Snowflake."""
    q = question.lower()

    if any(k in q for k in ("rdd", "manual", "quarantine")):
        return (
            "**2 files were quarantined** due to RDD operations (.map() and .flatMap()) "
            "that have no automatic Snowpark Connect equivalent.\n\n"
            "These files are `job_03.py` and another file flagged with `SPRKCNTPY1500`. "
            "They are excluded from the deployment package and need a manual rewrite — "
            "typically replacing the RDD chain with a DataFrame equivalent using "
            "`spark.createDataFrame()` and column expressions."
        )
    if any(k in q for k in ("validation", "phase a", "phase b", "entrypoint")):
        return (
            "**Validation summary:** 6 of 6 entrypoints passed both phases.\n\n"
            "- **Phase A** (local Spark baseline) — all 6 passed, output captured.\n"
            "- **Phase B** (Snowpark Connect on Snowflake) — all 6 matched the baseline "
            "with **0 divergences**.\n\n"
            "The ship recommendation is **SHIP**. The 22 auto-converted files are "
            "production-ready."
        )
    if any(k in q for k in ("score", "readiness", "85")):
        return (
            "The **readiness score is 85 / 100** (High Confidence).\n\n"
            "The main deductions are the 2 RDD files that need manual work (-10 pts) "
            "and 5 behavioral-difference warnings in areas like NULL ordering and "
            "string comparison case-sensitivity (-5 pts). "
            "Everything else converted cleanly."
        )
    if any(k in q for k in ("issue", "ewi", "error", "warning", "problem")):
        return (
            "**18 EWI issues** were flagged across the 24 files:\n\n"
            "- **5 Behavioral Differences** (NULL ordering, integer division, string case) — advisory, low risk\n"
            "- **4 RDD** operations — 2 files quarantined for manual rewrite\n"
            "- **3 Unsupported Imports** (pandas_udf, delta.tables) — auto-removed\n"
            "- **3 Unsupported APIs** (checkpoint→cache, setLogLevel no-op) — auto-rewritten\n"
            "- **1 UDF** — performance advisory only, no action needed\n\n"
            "Only the 2 High-severity RDD issues block deployment."
        )
    if any(k in q for k in ("how long", "time", "duration", "complete", "done", "finish")):
        return (
            "The migration run completed in **approximately 1 minute**.\n\n"
            "Breakdown: preprocess (3s) → analysis (8s) → assessment report (4s) → "
            "LLM migration fixes (20s, 4 parallel workers) → compiler gate (3s) → "
            "import updater (3s) → reports (3s) → validation Phase A + B (12s) → harvest (2s)."
        )
    if any(k in q for k in ("file", "convert", "how many")):
        return (
            "**24 files** were processed in total:\n\n"
            "- **19 converted automatically** — clean rewrites, no warnings\n"
            "- **3 converted with warnings** — advisory EWIs, no action required\n"
            "- **2 reverted (RDD)** — quarantined for manual rewrite\n\n"
            "The 22 non-RDD files are in `Conversion-SCOS-etl-pipeline/Output/` "
            "and ready to deploy."
        )
    if any(k in q for k in ("next step", "deploy", "what now", "ship")):
        return (
            "**Recommended next steps:**\n\n"
            "1. **Deploy the 22 converted files** from the Output/ directory — "
            "validation confirmed they match baseline output exactly.\n"
            "2. **Manually rewrite** `job_03.py` and the other RDD file — "
            "replace `.map()` / `.flatMap()` with DataFrame column expressions.\n"
            "3. Run the validator again on the manually-rewritten files to confirm "
            "they pass Phase B before deploying them.\n"
            "4. Review the 5 behavioral-difference advisories in production "
            "(NULL ordering, string case) and add test coverage if needed."
        )
    # default
    return (
        f"**etl-pipeline** migration is **complete** — overall progress 100%.\n\n"
        "**Summary:** 22 / 24 files auto-converted and validated. "
        "2 files quarantined (RDD — need manual rewrite). "
        "6 / 6 validation entrypoints passed Phase A and Phase B with 0 divergences. "
        "Readiness score: **85** · Ship recommendation: **SHIP**.\n\n"
        f"You asked: *{question}*\n"
        "Feel free to ask about specific issues, file counts, validation results, or next steps."
    )


def _build_prompt(context: str, messages: list) -> tuple[str, str]:
    """Return (full_prompt, latest_user_question)."""
    turns = [m for m in messages if isinstance(m, dict) and m.get("content")][-MAX_TURNS:]
    latest_user = ""
    for m in reversed(turns):
        if m.get("role") == "user":
            latest_user = str(m.get("content", ""))[:MAX_CHARS_PER_MSG]
            break

    convo_lines = []
    for m in turns:
        role = "User" if m.get("role") == "user" else "Assistant"
        convo_lines.append(f"{role}: {str(m.get('content', ''))[:MAX_CHARS_PER_MSG]}")

    prompt = (
        f"{SYSTEM_PREAMBLE}\n\n"
        "===== LIVE MIGRATION STATUS =====\n"
        f"{context or '(status not available yet)'}\n"
        "===== END STATUS =====\n\n"
        "Conversation so far:\n"
        + "\n".join(convo_lines)
        + "\nAssistant:"
    )
    return prompt, latest_user


def _emit(obj: dict) -> None:
    """Write one JSON response line and flush so the parent can read it promptly."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class _SessionPool:
    """Keep one Snowpark session alive across chat turns (SSO auth once)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = None
        self._connection: str | None = None
        self._complete = None
        self._CompleteOptions = None

    def answer(self, payload: dict) -> dict:
        context = str(payload.get("context") or "")
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            messages = []
        connection = payload.get("connection") or "default"
        model = payload.get("model") or DEFAULT_LLM_MODEL

        prompt, latest_user = _build_prompt(context, messages)
        if not latest_user:
            return {"error": "no question provided"}

        if os.environ.get("SCOS_UI_CHAT_MOCK") == "1":
            return {"answer": _canned(context, latest_user)}

        if open_session is None:
            return {"error": "snowflake session helpers unavailable "
                            "(run inside the snowpark-connect uv project)"}

        with self._lock:
            try:
                if self._complete is None:
                    from snowflake.cortex import CompleteOptions, complete
                    self._complete = complete
                    self._CompleteOptions = CompleteOptions

                # Reuse session when the connection name matches; reopen otherwise.
                if self._session is None or self._connection != connection:
                    if self._session is not None:
                        try:
                            self._session.close()
                        except Exception:
                            pass
                        self._session = None
                    try:
                        self._session = _open_session_safe(connection)
                        self._connection = connection
                    except Exception as exc:
                        self._session = None
                        self._connection = None
                        return {"error": f"could not open a Snowflake session for chat: {exc}"}

                response = self._complete(
                    model,
                    prompt,
                    options=self._CompleteOptions(
                        temperature=0.2, max_tokens=MAX_OUTPUT_TOKENS
                    ),
                    session=self._session,
                )
                answer = str(response).strip()
                if not answer:
                    return {"error": "the model returned an empty response"}
                return {"answer": answer}
            except Exception as exc:
                # Drop a dead session so the next turn can re-auth cleanly.
                msg = str(exc).lower()
                if any(k in msg for k in ("session", "auth", "token", "expired", "closed")):
                    try:
                        if self._session is not None:
                            self._session.close()
                    except Exception:
                        pass
                    self._session = None
                    self._connection = None
                return {"error": f"chat failed: {exc}"}

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None
                self._connection = None


def _serve() -> int:
    """Long-lived line protocol: one JSON request in → one JSON response out."""
    pool = _SessionPool()
    # Signal readiness so the parent knows the worker is up.
    _emit({"ready": True})
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception as exc:
                _emit({"error": f"bad request: {exc}"})
                continue
            if isinstance(payload, dict) and payload.get("op") == "shutdown":
                _emit({"ok": True})
                break
            _emit(pool.answer(payload if isinstance(payload, dict) else {}))
    finally:
        pool.close()
    return 0


def _oneshot() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        _emit({"error": f"bad request: {exc}"})
        return 0

    if not isinstance(payload, dict):
        payload = {}
    pool = _SessionPool()
    try:
        _emit(pool.answer(payload))
    finally:
        pool.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="ui_chat_answer")
    p.add_argument(
        "--serve",
        action="store_true",
        help="Long-lived worker: reuse one Snowflake session across chat turns",
    )
    args = p.parse_args()
    return _serve() if args.serve else _oneshot()


if __name__ == "__main__":
    sys.exit(main())
