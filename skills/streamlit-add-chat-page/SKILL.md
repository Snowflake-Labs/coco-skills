---
name: streamlit-add-chat-page
title: Add Chat Page to Streamlit
summary: "Build a chat page for a Streamlit-in-Snowflake app: structured Q&A (Cortex Analyst) or document RAG (Cortex Search), with a robust chat UI."
description: |
  Build a natural-language chat page for a Streamlit-in-Snowflake (SiS) app, in
  either of two flavors depending on the data:
    * STRUCTURED data (tables, metrics, dashboards) -> Cortex Analyst over a
      semantic view (named metrics, synonyms, Cortex Search literal resolution,
      verified queries) that generates and runs SQL.
    * UNSTRUCTURED text (documents, policies, transcripts, chunked text) -> Cortex
      Search RAG: retrieve relevant chunks and synthesize a grounded, cited answer.
  Both share a robust chat UI: multi-turn history, results/answer/sources expanders,
  guardrails, empty-result handling, and feedback logging.

  Use when: adding a chat or conversational-analytics page to a SiS app, a "chat with
  your data" or "chat with your documents" UI, a Cortex Analyst text-to-SQL page, or a
  RAG document-Q&A page.

  Triggers: cortex analyst chat, cortex search chat, chat page, chat with your data,
  chat with your documents, RAG chat, document Q&A, streamlit chat snowflake,
  conversational analytics, text-to-sql app, retrieval augmented generation, add chat
  to streamlit.

  Do NOT use for: a single fixed dashboard chart (just write the SQL); or an agent that
  must orchestrate MANY tools across domains (use a Cortex Agent).
prompt: Add a Cortex Analyst chat page to my Streamlit-in-Snowflake app.
language: en
status: Published
author: Chanin Nantasenamat, Lead Developer Advocate, OSS
type: snowflake
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
---

# Chat Page for Streamlit in Snowflake

Build a "chat with your data" page for a SiS app. Two flavors, by data type:
- **Structured** (tables, metrics) → **Path A: Cortex Analyst** turns the question into
  SQL against a **semantic view**, runs it, and shows results + a short answer.
- **Unstructured** (documents, chunked text) → **Path B: Cortex Search RAG** retrieves the
  most relevant chunks and synthesizes a **grounded, cited** answer.

Both share the same chat-UI patterns (multi-turn, expanders, feedback, guardrails).

Templates: `references/semantic_layer.sql` + `references/chat_page.py` + `references/eval_harness.py`
(Path A); `references/cortex_search_rag.sql` + `references/rag_chat_page.py` (Path B).

## Choose your path
| Your data | Path | Engine |
|---|---|---|
| Rows/metrics in tables you can aggregate | **A** | Cortex Analyst + semantic view (NL→SQL) |
| Free text: docs, policies, transcripts, wiki | **B** | Cortex Search over chunks + LLM synthesis (RAG) |
| Both, in one chat | — | A **Cortex Agent** with both an Analyst tool and a Search tool (beyond this skill) |

## When NOT to use
- A single fixed dashboard chart → just write the SQL; a chat page is overkill.
- One chat that must orchestrate many tools across domains → use a Cortex Agent.

## Path A — Structured data (Cortex Analyst)

### Step A1 — Model the semantic layer
1. **Flatten to ONE view.** Do all joins and pre-derive any parsed/computed
   columns (regex splits, flags, per-row composites) in a single SQL view. This
   keeps the semantic view **single-table**: no relationships to model, and Analyst
   is far more reliable. It also doubles as a pre-aggregated rollup that speeds up
   the rest of the app. **Mind the grain:** you own the join, so a one-to-many join
   will duplicate rows and inflate `SUM`/`AVG`/`COUNT` — keep the join at the intended
   grain (or pre-aggregate the many side) and count entities with `COUNT(DISTINCT)`.
2. **Create the semantic view** over that one view (`CREATE SEMANTIC VIEW`):
   - **Dimensions** = categorical/filter columns; **metrics** = aggregates.
   - **Encode derived/composite metrics as NAMED metrics** (e.g. a ratio-of-averages
     score) so the LLM uses your exact formula and can never mis-derive it.
   - Add `WITH SYNONYMS` + `COMMENT` to every table/dimension/metric — this is
     what Analyst reads to map natural language to columns.
3. **Seed accuracy controls** (biggest levers, cheap):
   - **Verified queries** (`AI_VERIFIED_QUERIES`): a handful of question→SQL pairs,
     ideally reusing your existing dashboard queries. This anchors generation on
     known-good patterns and is the #1 accuracy lever.
   - **`AI_SQL_GENERATION`**: embed domain rules (use named metrics, case-insensitive
     filters, sensible defaults) so the model doesn't reinvent them.
4. **⚠️ STOP** — show the DDL and get approval before deploying.

### Step A2 — Fix literal resolution with Cortex Search (the big accuracy win)
Snowflake string `=` is **case-sensitive**. Analyst will emit
`WHERE category = 'cortex search'` for a stored value of `Cortex Search` → **zero
rows, no answer**. Fix it structurally:
- **High-cardinality string dimensions (>10 distinct values):** attach a **Cortex
  Search service** (`WITH CORTEX SEARCH SERVICE <fqn> USING <col>`). Analyst then
  fuzzy-resolves the user's phrase to the canonical stored value before writing SQL.
  This also fixes wording/typo/abbreviation mismatches, not just case.
- **Low-cardinality (1-10):** provide sample values instead (no service needed).
- Do this for every user-facing filter column (categories, model/product names, …).
- **⚠️ STOP** — show the DDL and get approval before running any `CREATE OR REPLACE`
  (search services, semantic view): it overwrites an existing object of the same name.

### Step A3 — Build the chat page (`pages/chat.py`)
Adapt `references/chat_page.py`. It implements:
- **Analyst REST call:** in SiS use `_snowflake.send_snow_api_request("POST",
  "/api/v2/cortex/analyst/message", {}, {}, body, None, 60000)` with
  `body = {"messages": [...], "semantic_view": "<fqn>"}`; connector-token fallback for local dev.
- **Multi-turn:** accumulate `messages` (user + analyst turns) and resend the full
  history each call so follow-ups ("what about baseline?") keep context.
- **Run the returned SQL directly** — it is already fully qualified; do NOT pass it
  through any bare-name qualifier (double-qualification → "object does not exist").
- **Answer narrative:** summarize the result via `AI_COMPLETE(<flash-model>, ...)`
  (fast/cheap) with a deterministic fallback; feed the last ~10 turns for context.
- **UX:** Generated SQL / Results / Answer expanders; example-question buttons via a
  `pending_q` + `st.rerun()` handoff; empty-result path lists valid values.
- **Guardrails:** treat Analyst SQL as untrusted LLM output. Run **one read-only
  SELECT/WITH statement only** (reject `;`-chained statements and DML/DDL/`CALL`
  keywords after stripping strings/comments), wrap it in a `LIMIT` so the row cap
  (`MAX_ROWS`) is enforced in the warehouse. The role, not the regex, is the real
  security boundary: create the objects with a separate admin/owner role, and own the
  app with a least-privilege role that has only `SELECT` on the base view/tables,
  `USAGE` on the semantic view and search service, `INSERT` on the feedback table, and
  `USAGE` on the database/schema/warehouse. Render
  Analyst **warnings** and **clarification/suggestions** (no SQL) as guidance, not errors.
- **Feedback loop:** 👍/👎 buttons + a `CHAT_FEEDBACK` table logging question, generated
  SQL, request_id, and status — mine it later for new verified queries and accuracy tracking.

### Step A4 — Deploy, verify, guard against regressions
- **⚠️ STOP** — show the deploy command and target app name and get approval before deploying.
- Deploy the app (SiS runs **owner's rights**, so the app's least-privilege owner role
  needs the grants listed under Guardrails in Step A3, plus Cortex access).
- Verify the model BEFORE trusting the UI:
  `cortex analyst query "<real question>" --view <fqn>` and confirm the generated
  SQL uses the **canonical literal** (e.g. `'Cortex Search'`, not `'cortex search'`).
- Keep a **gold-question regression harness** (`references/eval_harness.py`) and rerun it
  after any semantic-view change to catch questions that silently stop working.
- **⚠️ STOP** — confirm end-to-end before finishing.

## Path B — Unstructured docs (Cortex Search RAG)
Use `references/cortex_search_rag.sql` + `references/rag_chat_page.py`.

### Step B1 — Chunk the documents & create the search service
1. **Get a chunks table** (one row per chunk + source metadata for citations). If you
   only have raw docs: extract text (`AI_PARSE_DOCUMENT` for PDFs/images) then split with
   `SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER`. **Chunk size** ~300-1800 chars with
   ~10-15% overlap: smaller = more precise retrieval, larger = more context per chunk.
2. **Create a Cortex Search service** `ON` the chunk-text column, with `ATTRIBUTES` for
   the metadata you'll cite (title, url, doc_id) and a `TARGET_LAG` to keep it fresh.
   **⚠️ STOP** — show the DDL and get approval before running the `CREATE OR REPLACE`.
   Create it with the admin/owner role, not the app's role (see Guardrails in Step A3).
3. **⚠️ STOP** — verify retrieval quality with `SEARCH_PREVIEW` (are the top chunks
   actually relevant?) before wiring the app.

### Step B2 — Build the RAG chat page (`pages/chat.py`)
Adapt `references/rag_chat_page.py`. It implements:
- **Retrieve:** query the service (Python `Root().…cortex_search_services[...].search()`;
  `SEARCH_PREVIEW` SQL fallback) for the top-K chunks.
- **Synthesize (grounded):** feed ONLY the retrieved chunks to `AI_COMPLETE` with
  instructions to answer **only from context, cite sources by [n], and say "I don't
  know" when the answer isn't present** — this is what prevents hallucination.
- **Cite:** a **Sources** expander lists each chunk with its title/link.
- Shared UI: multi-turn context, example buttons, feedback logging, and a
  **no-relevant-results** message when retrieval is empty.

### Step B3 — Deploy & verify
- **⚠️ STOP** — show the deploy command and target app name and get approval before deploying.
- Deploy the app (owner's rights: the owner role needs `USAGE` on the search service + Cortex).
- Ask a question whose answer you know and confirm the answer is correct **and** the cited
  chunks actually support it (grounding check).
- **⚠️ STOP** — confirm end-to-end before finishing.

## Robustness checklist
**Shared (both paths):** SELECT-only/grounded execution • empty-result handling • multi-turn context • feedback logging • deploy under owner's rights with Cortex + object access.

**Path A (Analyst):**
- [ ] Named metrics for every computed value (no formulas left to the LLM).
- [ ] View grain verified — no one-to-many fan-out; entity counts use `COUNT(DISTINCT)`.
- [ ] NULLs COALESCE'd on filter/group dimensions; ratio metrics guard div-by-zero (`NULLIF`).
- [ ] Synonyms + comments on all dimensions/metrics.
- [ ] Cortex Search on high-cardinality string dims; sample values on low-cardinality.
- [ ] A few verified queries seeded (reuse existing dashboard SQL).
- [ ] `AI_SQL_GENERATION` domain rules set.
- [ ] SELECT-only execution + row cap + query safety.
- [ ] Warnings + clarification (no-SQL) responses handled gracefully.
- [ ] Empty-result path lists valid values.
- [ ] Gold-question eval harness passes before/after changes.

**Path B (RAG):**
- [ ] Chunk size + overlap tuned; source metadata kept for citations.
- [ ] Synthesis prompt is **strictly grounded** (answer only from context; "I don't know" otherwise).
- [ ] Answers cite sources; a Sources expander shows the retrieved chunks/links.
- [ ] No-relevant-results path returns a clear message (not a hallucinated answer).
- [ ] `TARGET_LAG` set so the index stays fresh as documents change.

## Edge cases: general vs niche
**Universal (handled by the templates):**
- Grain/fan-out double-counting; NULLs; divide-by-zero; case/literal mismatch; out-of-scope
  questions; large results; untrusted summary-prompt input (Path A). Empty retrieval, ungrounded
  answers, and missing citations (Path B).

**Data-shape-dependent (handle if applicable):**
- Time-series/trends → add a date dimension + line chart (Path A chart heuristic only does bars).
- Over-fuzzy Cortex Search matches → tighten descriptions / cardinality.
- Ambiguous synonyms (two metrics both "score") → disambiguate.
- Non-English questions → answer in the question's language.
- RAG retrieval quality: low recall → smaller chunks / higher K / hybrid filters; conflicting
  chunks → ask the model to note disagreement; long chunks → summarize before synthesis.

**Niche (don't re-teach — model with the semantic-view `patterns` skill):**
- Period-over-period (YoY/MoM/SPLY), rolling/YTD windows, fiscal calendars.
- Non-additive snapshot facts (balances, headcount — must not sum over time).
- SCD2 / as-of temporal joins, multi-fact / role-playing dimensions, unit conversions, geospatial.


## Gotchas (hard-won)
- **`AI_COMPLETE`, not `SNOWFLAKE.CORTEX.COMPLETE`** — the latter is deprecated / gated.
- **Per-item `COMMENT` needs `=`**: `COMMENT = '...'` (bare `COMMENT '...'` is a syntax error).
- **Dimension clause order is fixed:** `expr → WITH SYNONYMS → COMMENT → WITH CORTEX
  SEARCH SERVICE` (the search-service clause must come **last**).
- **Fully-qualify the search-service name** (`DB.SCHEMA.NAME`) in the semantic view.
- **Search services have a `TARGET_LAG`** (e.g. 24h): new values become *fuzzy*-matchable
  after refresh; exact matches always work immediately.
- **Compute-then-render** the assistant turn once (don't render inline during the
  API calls) so the live path and the redraw-on-rerun path stay identical.
- **`RESULT_SCAN` / session state** don't survive across pooled connections — verify
  DDL by re-querying the object, not by trusting a prior `SHOW`/`LAST_QUERY_ID`.

## Stopping points
- ✋ A1: approve the base-view + semantic-view DDL before running it.
- ✋ A2: approve any `CREATE OR REPLACE` (search services, semantic view) before running it.
- ✋ A3: none — code changes only (page file is reviewed before deploy).
- ✋ A4: approve the deploy, then confirm the NL round-trip works before calling it done.
- ✋ B1: approve the search-service `CREATE OR REPLACE`, then verify retrieval with `SEARCH_PREVIEW`.
- ✋ B2: none — code changes only.
- ✋ B3: approve the deploy, then confirm a grounded, cited answer end-to-end.

## Output
A deployed SiS chat page backed by: a flattened base view, a single-table semantic
view with named metrics + synonyms, and Cortex Search services on string dimensions.
