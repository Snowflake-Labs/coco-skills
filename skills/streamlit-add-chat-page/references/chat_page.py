"""Cortex Analyst chat page for a Streamlit-in-Snowflake app — reusable template.

Drop into pages/chat.py of a SiS app and customize the CONFIG block. Requires
helpers `get_session()` and `is_sis()` (typical in a SiS app's utils/db.py); if
you don't have them, minimal inline versions are shown at the bottom.

Key patterns baked in (see the skill's SKILL.md for the rationale):
  * Cortex Analyst REST call via _snowflake in SiS, connector-token fallback locally.
  * Multi-turn: accumulate the full message history and resend it every turn.
  * Compute-then-render: build the whole assistant turn, then render once, so the
    live turn and the redraw-on-rerun path use identical code (no drift).
  * Run the returned SQL directly — it is already fully qualified; do NOT pass it
    through any table-name qualifier or it will double-qualify.
  * Answer narrative via AI_COMPLETE (a fast "flash" model) + deterministic fallback.
  * Example-question buttons via a pending_q + st.rerun() handoff.
  * Empty-result handling that lists valid values so a miss becomes a next step.
"""
import json
import re
import uuid
import streamlit as st
import pandas as pd
from utils.db import get_session, is_sis   # adapt to your app

# ── CONFIG ────────────────────────────────────────────────────────────────────
SEMANTIC_VIEW = "<DB>.<SCHEMA>.<SEMANTIC_VIEW>"
SUMMARY_MODEL = "gemini-3.5-flash"          # fast/cheap model for the narrative
BASE_VIEW     = "<DB>.<SCHEMA>.<BASE_VIEW>"  # for the empty-result value list
FEEDBACK_TABLE = "<DB>.<SCHEMA>.CHAT_FEEDBACK"  # set to None to disable feedback logging
MAX_ROWS      = 1000                         # cap result rows rendered to the browser
VALUE_DIMS    = {"MODEL": "MODEL", "CATEGORY": "CATEGORY"}  # label -> column
EXAMPLE_QUESTIONS = [
    "<example question 1>",
    "<example question 2>",
]
_ANALYST_PATH = "/api/v2/cortex/analyst/message"

st.title(":material/forum: Chat")
st.caption("Ask about the data in plain English — powered by **Cortex Analyst**.")


# ── Cortex Analyst call ────────────────────────────────────────────────────────
def _call_analyst(messages: list) -> dict:
    body = {"messages": messages, "semantic_view": SEMANTIC_VIEW}
    if is_sis():
        import _snowflake
        resp = _snowflake.send_snow_api_request(
            "POST", _ANALYST_PATH, {}, {}, body, None, 60000
        )
        if resp.get("status") not in (200, "200"):
            raise RuntimeError(f"Analyst API status {resp.get('status')}: {resp.get('content')}")
        return json.loads(resp["content"])
    # Local-dev fallback via the connector's session token.
    import requests
    conn = get_session().connection
    r = requests.post(
        f"https://{conn.host}{_ANALYST_PATH}",
        json=body,
        headers={"Authorization": f'Snowflake Token="{conn.rest.token}"',
                 "Content-Type": "application/json"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


# Not listed: DESC (ORDER BY ... DESC is common in Analyst SQL) and SHOW/DESCRIBE,
# which the SELECT/WITH-only check already rejects as statements.
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|merge|truncate|create|alter|drop|undrop|rename|grant|"
    r"revoke|call|execute|copy|put|get|remove|list|use|set|unset|begin|commit|"
    r"rollback|explain)\b",
    re.IGNORECASE,
)


def _strip_literals_and_comments(sql: str, keep_literals: bool = False) -> str:
    """Drop comments and (unless keep_literals) blank out string literals, quoted
    identifiers and $$-blocks, so the keyword/statement checks below only see real
    SQL tokens. One left-to-right scan, so a quote or $$ inside one construct can't
    hide SQL inside another."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c, two = sql[i], sql[i:i + 2]
        if two in ("--", "//"):                        # line comment
            j = sql.find("\n", i)
            out.append(" "); i = n if j < 0 else j
        elif two == "/*":                              # block comment
            j = sql.find("*/", i + 2)
            out.append(" "); i = n if j < 0 else j + 2
        elif two == "$$":                              # $$ ... $$ block
            j = sql.find("$$", i + 2)
            end = n if j < 0 else j + 2
            out.append(sql[i:end] if keep_literals else "''"); i = end
        elif c in "'\"":                               # 'string' or "identifier"
            j = i + 1
            while j < n and not (sql[j] == c and sql[j + 1:j + 2] != c):
                j += 2 if (sql[j] == c or (c == "'" and sql[j] == "\\")) else 1
            end = min(j + 1, n)
            out.append(sql[i:end] if keep_literals else c + c); i = end
        else:
            out.append(c); i += 1
    return "".join(out)


def _validate_read_only(sql: str) -> str:
    """Allow exactly one read-only SELECT/WITH statement; raise otherwise.

    Analyst output is LLM-generated, so treat it as untrusted. This is defense in
    depth — the real boundary is owning the app with a least-privilege role (SELECT
    on the base view/tables, USAGE on the semantic view, INSERT on the feedback
    table) while a separate admin role owns the objects (see SKILL.md).
    """
    # Drop comments first (literals kept), then trailing semicolons, so a trailing
    # `-- note` or `; -- done` can't break the LIMIT wrapper or trip the `;` check.
    clean = _strip_literals_and_comments(sql, keep_literals=True).strip().rstrip(";").strip()
    tokens = _strip_literals_and_comments(clean)
    if not re.match(r"(?is)^\s*(with|select)\b", tokens):
        raise ValueError("Refusing to run non-SELECT SQL.")
    if ";" in tokens:
        raise ValueError("Refusing to run multiple SQL statements.")
    bad = _FORBIDDEN_SQL.search(tokens)
    if bad:
        raise ValueError(f"Refusing to run SQL containing '{bad.group(1).upper()}'.")
    return clean


def _run_sql(sql: str) -> pd.DataFrame:
    """Execute Analyst SQL defensively: single read-only statement, row cap enforced
    in the warehouse (not after pulling everything into the browser)."""
    clean = _validate_read_only(sql)   # already fully qualified — do not re-qualify
    capped = f"SELECT * FROM (\n{clean}\n) LIMIT {int(MAX_ROWS) + 1}"
    df = get_session().sql(capped).to_pandas()
    if len(df) > MAX_ROWS:
        df.attrs["truncated_from"] = True
        df = df.head(MAX_ROWS)
    return df


def _log_feedback(turn: dict, rating: str | None) -> None:
    """Append the turn + rating to the feedback table (mine later for verified queries)."""
    if not FEEDBACK_TABLE:
        return
    try:
        get_session().sql(
            f"INSERT INTO {FEEDBACK_TABLE} "
            "(USERNAME, QUESTION, GENERATED_SQL, REQUEST_ID, STATUS, RATING) "
            "SELECT CURRENT_USER(), ?, ?, ?, ?, ?",
            params=[turn.get("question"), turn.get("sql"),
                    turn.get("request_id"), turn.get("status"), rating],
        ).collect()
    except Exception:
        pass


def _render_chart(df: pd.DataFrame) -> None:
    if df.shape[1] != 2:
        return
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if len(num) != 1 or not (2 <= len(df) <= 60):
        return
    label = [c for c in df.columns if c not in num][0]
    try:
        st.bar_chart(df.set_index(label)[num[0]])
    except Exception:
        pass


def _render_results(df: pd.DataFrame) -> None:
    disp = df.copy()
    for c in disp.select_dtypes("float").columns:   # round floats for readability
        disp[c] = disp[c].round(2)
    if len(disp) == 1 and disp.shape[1] == 1:
        st.metric(disp.columns[0], disp.iloc[0, 0])
    else:
        st.dataframe(disp, use_container_width=True, hide_index=True)
        if df.attrs.get("truncated_from"):
            st.caption(f"Showing first {len(df):,} rows (result truncated).")
        _render_chart(df)


def _available_values() -> str:
    """List valid values for the filter dimensions (empty-result safety net)."""
    try:
        sel = " UNION ALL ".join(
            f"SELECT '{lbl}' AS KIND, {col} AS VAL FROM "
            f"(SELECT DISTINCT {col} FROM {BASE_VIEW} WHERE {col} IS NOT NULL)"
            for lbl, col in VALUE_DIMS.items()
        )
        df = get_session().sql(sel + " ORDER BY 1,2").to_pandas()
        parts = [f"**{lbl}:** " + ", ".join(df.loc[df.KIND == lbl, "VAL"].tolist())
                 for lbl in VALUE_DIMS if (df.KIND == lbl).any()]
        return ("\n\n" + "\n\n".join(parts)) if parts else ""
    except Exception:
        return ""


def _summarize(question: str, df: pd.DataFrame, prior: str = "") -> str | None:
    if df is None:
        return None
    if df.empty:
        return ("The query ran but matched no rows — usually a value name didn't match. "
                "Check the exact value below and try again." + _available_values())
    ctx = f"Conversation so far (use only to resolve follow-ups):\n{prior}\n\n" if prior else ""
    prompt = ("Answer the CURRENT question in 1-3 concise sentences using the result data; "
              "state key numbers. No preamble, no code, don't restate the question.\n\n"
              f"{ctx}Current question: {question}\n\nResult (CSV):\n{df.head(30).to_csv(index=False)}")
    try:
        row = get_session().sql("SELECT AI_COMPLETE(?, ?) AS S",
                                params=[SUMMARY_MODEL, prompt]).to_pandas()
        text = (row.iloc[0]["S"] or "").strip()
        for ch in ('"', "\u201c", "\u201d"):
            text = text.replace(ch, "")
        if text.strip():
            return text.strip()
    except Exception:
        pass
    if len(df) == 1:
        return "Result: " + ", ".join(f"{c} = {df.iloc[0][c]}" for c in df.columns)
    return f"{len(df)} rows returned across columns: {', '.join(map(str, df.columns))}."


# ── State ─────────────────────────────────────────────────────────────────────
st.session_state.setdefault("analyst_msgs", [])   # Analyst wire history
st.session_state.setdefault("chat_display", [])    # rendered turns for redraw

c1, c2 = st.columns([4, 1])
c1.caption(f"Semantic view: `{SEMANTIC_VIEW}`")
if c2.button("Clear chat", use_container_width=True):
    st.session_state.analyst_msgs = []
    st.session_state.chat_display = []
    st.rerun()


def _render_turn(turn: dict) -> None:
    with st.chat_message(turn["role"]):
        if turn.get("text"):
            st.markdown(turn["text"])
        for w in turn.get("warnings", []) or []:
            st.warning(w)
        if turn.get("sql"):
            with st.expander("Generated SQL"):
                st.code(turn["sql"], language="sql")
        if turn.get("error"):
            st.error(turn["error"])
        if turn.get("df") is not None:
            with st.expander("Results", expanded=False):
                _render_results(turn["df"])
            if turn.get("summary"):
                with st.expander("Answer", expanded=True):
                    st.markdown(turn["summary"])
        for s in turn.get("suggestions", []) or []:
            st.markdown(f"- {s}")
        # Feedback (assistant turns that produced an answer)
        if turn["role"] == "assistant" and turn.get("df") is not None and FEEDBACK_TABLE:
            tid = turn.get("tid", "x")
            fb1, fb2, _ = st.columns([1, 1, 8])
            if fb1.button(":material/thumb_up:", key=f"up_{tid}", help="Good answer"):
                _log_feedback(turn, "up"); st.toast("Thanks for the feedback!")
            if fb2.button(":material/thumb_down:", key=f"down_{tid}", help="Wrong / unhelpful"):
                _log_feedback(turn, "down"); st.toast("Logged — thanks, we'll use it to improve.")


for _t in st.session_state.chat_display:
    _render_turn(_t)


def _history_text(max_turns: int = 10) -> str:
    lines = []
    for t in st.session_state.chat_display[-(max_turns * 2):]:
        if t.get("role") == "user" and t.get("text"):
            lines.append(f"Q: {t['text']}")
        elif t.get("role") == "assistant" and t.get("summary"):
            lines.append(f"A: {t['summary']}")
    return "\n".join(lines)


def _handle(question: str) -> None:
    prior = _history_text()                       # capture BEFORE appending this turn
    user_turn = {"role": "user", "text": question}
    st.session_state.chat_display.append(user_turn)
    _render_turn(user_turn)
    st.session_state.analyst_msgs.append(
        {"role": "user", "content": [{"type": "text", "text": question}]})

    turn = {"role": "assistant", "text": "", "sql": None, "df": None, "error": None,
            "suggestions": [], "summary": None, "warnings": [], "question": question,
            "request_id": None, "status": None, "tid": uuid.uuid4().hex[:8]}
    with st.spinner("Asking Cortex Analyst…"):
        try:
            resp = _call_analyst(st.session_state.analyst_msgs)
        except Exception as e:
            turn["error"] = f"Cortex Analyst request failed: {e}"
            turn["status"] = "error"
            _log_feedback(turn, None)
            _render_turn(turn); st.session_state.chat_display.append(turn); return

    turn["request_id"] = resp.get("request_id")
    turn["warnings"] = [w.get("message", str(w)) for w in resp.get("warnings", []) or []]
    content = resp.get("message", {}).get("content", [])
    for part in content:
        if part.get("type") == "text":
            turn["text"] += part.get("text", "")
        elif part.get("type") == "sql":
            turn["sql"] = part.get("statement")
        elif part.get("type") == "suggestions":
            turn["suggestions"] = part.get("suggestions", [])
    if content:                                   # keep multi-turn context
        st.session_state.analyst_msgs.append({"role": "analyst", "content": content})

    if turn["sql"]:
        with st.spinner("Running query…"):
            try:
                turn["df"] = _run_sql(turn["sql"])
            except Exception as e:
                turn["error"] = f"Could not run the generated SQL: {e}"
        if turn["df"] is not None:
            with st.spinner("Summarizing…"):
                turn["summary"] = _summarize(question, turn["df"], prior)

    # Status: answered | empty | error | clarify (no SQL, just suggestions/text)
    turn["status"] = ("error" if turn["error"] else
                      "empty" if (turn["df"] is not None and turn["df"].empty) else
                      "answered" if turn["df"] is not None else "clarify")
    # Graceful out-of-scope message when Analyst returns nothing actionable.
    if turn["status"] == "clarify" and not turn["text"] and not turn["suggestions"]:
        turn["text"] = ("I couldn't answer that with this data. Try rephrasing, or ask "
                        "about a different metric or dimension this model covers.")
    _log_feedback(turn, None)                     # log the turn (rating filled in on click)
    _render_turn(turn)
    st.session_state.chat_display.append(turn)


_pending = st.session_state.get("pending_q")
if not st.session_state.chat_display and not _pending:
    st.markdown("###### :material/lightbulb: Example questions — click to ask")
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLE_QUESTIONS):
        if cols[i % 2].button(ex, key=f"ex_q_{i}", use_container_width=True):
            st.session_state.pending_q = ex
            st.rerun()

_typed = st.chat_input("Ask about the data…")
_q = _typed or st.session_state.pop("pending_q", None)
if _q:
    _handle(_q)


# ── Minimal helpers if your app lacks utils/db.py ──────────────────────────────
# _IS_SIS = False
# try:
#     from snowflake.snowpark.context import get_active_session
#     get_active_session(); _IS_SIS = True
# except Exception:
#     pass
# def is_sis(): return _IS_SIS
# @st.cache_resource
# def get_session():
#     from snowflake.snowpark.context import get_active_session
#     try: return get_active_session()
#     except Exception:
#         from snowflake.snowpark import Session
#         return Session.builder.config("connection_name", "<conn>").create()
