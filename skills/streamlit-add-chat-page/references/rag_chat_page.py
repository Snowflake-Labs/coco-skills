"""RAG chat page for a Streamlit-in-Snowflake app — retrieve chunked docs with
Cortex Search, then synthesize a grounded, cited answer. Reusable template.

Use this variant when the app is powered by UNSTRUCTURED text (documents,
policies, transcripts) rather than structured tables. For structured data use
chat_page.py (Cortex Analyst) instead.

Requires helpers `get_session()` and `is_sis()` (see chat_page.py notes).

Patterns baked in:
  * Retrieve top-K chunks from a Cortex Search service (Python API; SEARCH_PREVIEW
    SQL fallback so it works with no extra deps).
  * Grounded synthesis: answer ONLY from retrieved context; cite sources by [n];
    say "I don't know" when the context doesn't contain the answer.
  * Sources expander with title/link per chunk; multi-turn; feedback logging.
"""
import json
import streamlit as st
from utils.db import get_session, is_sis   # adapt to your app

# ── CONFIG ────────────────────────────────────────────────────────────────────
DB, SCHEMA, SERVICE = "<DB>", "<SCHEMA>", "<SEARCH_SERVICE>"
SEARCH_SERVICE = f"{DB}.{SCHEMA}.{SERVICE}"
TEXT_COL   = "CHUNK_TEXT"
TITLE_COL  = "TITLE"
URL_COL    = "URL"
RETURN_COLS = [TEXT_COL, TITLE_COL, URL_COL]
TOP_K       = 5
SYNTH_MODEL = "claude-sonnet-4-6"        # answer synthesis (use a capable model)
FEEDBACK_TABLE = f"{DB}.{SCHEMA}.CHAT_FEEDBACK"  # or None to disable
EXAMPLE_QUESTIONS = ["<example question 1>", "<example question 2>"]

st.title(":material/forum: Chat")
st.caption("Ask about the documents in plain English — answers are grounded in and cited from your content.")


# ── Retrieval ─────────────────────────────────────────────────────────────────
def _search(query: str) -> list[dict]:
    """Return top-K relevant chunks from the Cortex Search service."""
    # Primary: Python API (snowflake.core Root).
    try:
        from snowflake.core import Root
        svc = (Root(get_session())
               .databases[DB].schemas[SCHEMA].cortex_search_services[SERVICE])
        resp = svc.search(query=query, columns=RETURN_COLS, limit=TOP_K)
        return list(resp.results)
    except Exception:
        pass
    # Fallback: SQL SEARCH_PREVIEW (no extra deps).
    payload = json.dumps({"query": query, "columns": RETURN_COLS, "limit": TOP_K})
    row = get_session().sql(
        "SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW(?, ?)) AS R",
        params=[SEARCH_SERVICE, payload],
    ).to_pandas()
    return json.loads(row.iloc[0]["R"]).get("results", [])


def _synthesize(question: str, chunks: list[dict], prior: str = "") -> str:
    """Grounded answer over retrieved chunks, citing sources by [n]."""
    context = "\n\n".join(
        f"[{i+1}] {c.get(TITLE_COL,'source')}: {c.get(TEXT_COL,'')}"
        for i, c in enumerate(chunks)
    )
    ctx = f"Conversation so far (for follow-up context):\n{prior}\n\n" if prior else ""
    prompt = (
        "Answer the question using ONLY the numbered context below. Cite the sources "
        "you use inline as [n]. If the answer is not in the context, say you don't have "
        "that information — do not invent facts. Be concise.\n\n"
        f"{ctx}Question: {question}\n\nContext:\n{context}"
    )
    try:
        row = get_session().sql("SELECT AI_COMPLETE(?, ?) AS A",
                                params=[SYNTH_MODEL, prompt]).to_pandas()
        return (row.iloc[0]["A"] or "").strip()
    except Exception as e:
        return f"(Could not synthesize an answer: {e})"


def _log_feedback(turn: dict, rating: str | None) -> None:
    if not FEEDBACK_TABLE:
        return
    try:
        get_session().sql(
            f"INSERT INTO {FEEDBACK_TABLE} (USERNAME, QUESTION, GENERATED_SQL, "
            "REQUEST_ID, STATUS, RATING) SELECT CURRENT_USER(), ?, NULL, NULL, ?, ?",
            params=[turn.get("question"), turn.get("status"), rating],
        ).collect()
    except Exception:
        pass


# ── State ─────────────────────────────────────────────────────────────────────
st.session_state.setdefault("chat_display", [])
st.session_state.setdefault("history", [])   # [(q, a), ...] for follow-up context

c1, c2 = st.columns([4, 1])
c1.caption(f"Search service: `{SEARCH_SERVICE}`")
if c2.button("Clear chat", use_container_width=True):
    st.session_state.chat_display = []
    st.session_state.history = []
    st.rerun()


def _render_turn(turn: dict) -> None:
    with st.chat_message(turn["role"]):
        if turn.get("text"):
            st.markdown(turn["text"])
        if turn.get("answer"):
            with st.expander("Answer", expanded=True):
                st.markdown(turn["answer"])
        if turn.get("chunks"):
            with st.expander(f"Sources ({len(turn['chunks'])})", expanded=False):
                for i, c in enumerate(turn["chunks"]):
                    title = c.get(TITLE_COL, "source")
                    url = c.get(URL_COL)
                    head = f"**[{i+1}] [{title}]({url})**" if url else f"**[{i+1}] {title}**"
                    st.markdown(head)
                    st.caption((c.get(TEXT_COL, "") or "")[:500])
        if turn["role"] == "assistant" and turn.get("answer") and FEEDBACK_TABLE:
            tid = turn.get("tid", "x")
            f1, f2, _ = st.columns([1, 1, 8])
            if f1.button(":material/thumb_up:", key=f"up_{tid}"):
                _log_feedback(turn, "up"); st.toast("Thanks!")
            if f2.button(":material/thumb_down:", key=f"down_{tid}"):
                _log_feedback(turn, "down"); st.toast("Logged — thanks.")


for _t in st.session_state.chat_display:
    _render_turn(_t)


def _handle(question: str) -> None:
    import uuid
    prior = "\n".join(f"Q: {q}\nA: {a}" for q, a in st.session_state.history[-10:])
    user_turn = {"role": "user", "text": question}
    st.session_state.chat_display.append(user_turn)
    _render_turn(user_turn)

    turn = {"role": "assistant", "question": question, "answer": None,
            "chunks": [], "status": None, "tid": uuid.uuid4().hex[:8]}
    with st.spinner("Searching documents…"):
        try:
            turn["chunks"] = _search(question)
        except Exception as e:
            turn["answer"] = f"Search failed: {e}"; turn["status"] = "error"
    if turn["status"] != "error":
        if not turn["chunks"]:
            turn["answer"] = ("No relevant passages were found for that question. "
                              "Try rephrasing or using different keywords.")
            turn["status"] = "empty"
        else:
            with st.spinner("Answering…"):
                turn["answer"] = _synthesize(question, turn["chunks"], prior)
            turn["status"] = "answered"

    _log_feedback(turn, None)
    _render_turn(turn)
    st.session_state.chat_display.append(turn)
    if turn["status"] == "answered":
        st.session_state.history.append((question, turn["answer"]))


_pending = st.session_state.get("pending_q")
if not st.session_state.chat_display and not _pending:
    st.markdown("###### :material/lightbulb: Example questions — click to ask")
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLE_QUESTIONS):
        if cols[i % 2].button(ex, key=f"ex_q_{i}", use_container_width=True):
            st.session_state.pending_q = ex
            st.rerun()

_typed = st.chat_input("Ask about the documents…")
_q = _typed or st.session_state.pop("pending_q", None)
if _q:
    _handle(_q)
