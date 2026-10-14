---
name: observe-recall
description: "Recall — the memory-vault retrieval primitive. Dispatches xo-cold-fast to expand a query into keywords, grep notes/tasks/diary, and return ranked full-path hits with excerpts. Canonical define-once pattern; invoked by Triage (fuzzy-recall resolution) and Survey (local-sources rung)."
---

# Recall

**Recall is how XO searches its own memory vault.** It pairs with recording (the save protocol *writes* the vault; Recall *reads* it). The mechanism is zero-dependency at its floor: the LLM does the semantic work (expanding a vague query into the concrete terms likely to appear in the notes), and `grep` does the cheap literal matching — no native deps, no index to maintain, and it works everywhere, including unattended hook contexts. When a warm `tgrep` index is available (interactive Desktop with Cortex embed access), the expanded terms are also run through `tgrep` keyword/hybrid search, which meaning-ranks the hits and often lifts the strongest note above what literal matching finds. `grep` stays the guaranteed fallback: whenever tgrep is unavailable, its index is cold (it will say so), or you need exact-literal matching, Recall still works with no infrastructure at all.

## When to use

- **Triage:** the operator makes a fuzzy recall reference — "I vaguely remember we looked at X", "didn't we hit this before?", "that thing about Y". Resolve it to a concrete WI/note *before* routing.
- **Survey:** the local-sources discovery rung — find what the vault already knows about a topic.
- **Anytime reflex:** you wonder "have we seen this before?" mid-task.

Not for: deep investigation of a known target (that's Analyse), or external/web discovery (that's Survey's external rung).

---

## The dispatch

Dispatch `xo-cold-fast` (auto-fast model — keeps Recall cheap) with the Recall persona. **Recall returns results in-context, NOT to a file** — it is a fast interactive lookup, so override the agent's default OUTPUT_PATH behaviour explicitly:

```
runSubagent(subagent_type: "xocortex:xo-cold-fast", prompt: "
  PERSONA: Recall — search the operator's memory vault
  INPUTS: query = '[the operator's query, verbatim or lightly cleaned]'
          scope = $XOCORTEX_HOME/notes, $XOCORTEX_HOME/tasks, $XOCORTEX_HOME/diary

  Step 1 — EXPAND the query into 4-10 keyword/phrase candidates: synonyms,
  likely technical terms, class/product/customer names, and rephrasings that
  would appear in the actual notes. (e.g. 'that idle-session housekeeping thing'
  -> SessionIdleService, idle, timeout, hook, CronScheduler, housekeeping.)
  Avoid ultra-generic terms that match everything (build, data, the).

  Step 2 — SEARCH the scope (markdown only) for each candidate. If a warm tgrep
  index is available, run the expanded terms through tgrep (keyword or hybrid
  mode) and use its ranking; if tgrep is unavailable or reports a cold index,
  GREP for each candidate instead. Either way, rank files by how many DISTINCT
  candidates they contain (tie-break: total occurrences). Prefer prose notes over
  machine-generated data dumps — discount artifact files (e.g. *-artifacts/*.json)
  that rank on keyword density rather than relevance.

  Step 3 — RETURN IN-CONTEXT (do not write a file). Top 5-8 hits, each:
    - full path RELATIVE to \$XOCORTEX_HOME (e.g. notes/2026-06/wi314-...md) —
      NEVER a bare basename (basenames collide: notes/ and tasks/ both hold
      wi308-session-end-closure-hooks-investigation.md)
    - a 1-2 line excerpt around the strongest match
    - a one-line 'why relevant'
  If nothing scores, say so plainly — do not pad with weak hits.

  Single pass. Do not follow threads or read files deeply — that is Analyse.
")
```

This is one bounded dispatch: expand -> grep -> rank -> return. Fast by design.

---

## Pairing with the memory index (segregated trust)

The vault (notes/tasks/diary) is **authoritative** — it has passed the operator. `/memories` is **provisional agent scratch** — it may be stale or never sanctioned. Never blend them.

Recall (the subagent) covers the **vault only**. For `/memories`, you already have the curated `MEMORY.md` index in your context at session start — glance it directly (and `memory view` a specific file if a pointer looks relevant). Do **not** send the subagent into `/memories`: it would lose the curated-index advantage and muddy the trust boundary.

Present the two sources as **clearly-labelled separate groups**:

```markdown
**From the vault (checked — authoritative):**
- notes/2026-06/wi314-kafka-binary-headers.md — little-endian Int64 header decode
- ...

**Also in agent scratch (/memories — provisional, verify before relying):**
- [memory pointer], if any looked relevant
```

If there is nothing relevant in memory, omit the second group rather than padding it.

---

## Bounding

- **Scope is fixed:** notes/tasks/diary under `$XOCORTEX_HOME`. No skills, no knowledge-base, no external, no `/memories` (handled by the index glance above).
- **One pass, fast model.** No iterative deepening, no thread-following.
- **Full relative paths always** — the single most important output rule (basename collisions are real in this workspace).
- The orchestrator decides what to do with hits — Recall surfaces *associations ranked by overlap*, not verified answers.
