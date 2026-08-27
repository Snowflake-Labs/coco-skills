---
name: observe-survey
description: Survey stage — breadth-first discovery to locate where the subject lives. Dispatches xo-cold-fast. Produces a location map / inventory file. Use before Analyse when the target location is not yet known.
---

# Survey

Breadth-first discovery. The goal is to **map the territory** — locate where the subject lives, what sources contain relevant information, and what the inventory looks like — without going deep on any thread. Depth comes in Analyse.

## When to use this stage

- The target location is unknown ("where do we have anything about X?")
- You need to map a domain before investigating it
- Building an inventory for the operator to review before deciding what to dig into

---

## Input contract

- Task intent or question from the operator
- Optional: prior WI notes to check first (load them if they exist)

**If the target includes a code repository and the work is substantive** (new feature, understanding prior decisions), surface the attendant-services offer before dispatching — see `references/refs-survey-methods.md` Codebase Investigation section. Skip this for bug fixes and quick targeted lookups.

---

## Mode: breadth scan (default)

Scan across sources breadth-first. Collect pointers. Produce a map.

### Source order (local-first, then broad)

Survey ranges widely to find *where* a topic lives and *what* is already known. Check what we already have before re-discovering, then cast across external discovery surfaces.

| Order | Source | How to check |
|---|---|---|
| 1 | Active WI notes | Read `notes/{YYYY-MM}/{project}-wi{N}-*.md` directly if a WI is active |
| 2 | The vault (notes + tasks + diary) | **Recall** (`references/observe-recall.md`) — not a bare grep. Recall expands the query into candidate terms, then greps; it bridges the vocabulary gap a literal grep misses. |
| 3 | `/memories/` directory | Glance the in-context `MEMORY.md` index; `memory view` a specific file if a pointer looks relevant. Do not send Recall here — its scope is the vault only. |
| 4 | External discovery surfaces | Glean, wiki/Confluence, Google Docs, the web, code repositories, and any connected MCP sources — to locate where the topic lives and what's documented |

**Check local first** (1–3) so you don't re-discover what we already know — but Survey's job is breadth: once local is checked, range across whatever external surfaces and MCP tools are available. No results means "not yet documented" — not "nothing exists."

Before you range outward, consult any registered knowledge sources relevant to the topic and suggest them to the operator rather than auto-searching them. Use the survey context to frame the offer plainly: *"You've previously found [source] useful for [domain] — want me to include that in this survey?"*

### Reach for available skills

When the topic touches unfamiliar territory — especially Snowflake technologies (Cortex, semantic views, Snowpark, Iceberg, Streamlit, etc.) — a relevant **skill** very likely already exists. Treat *"is there a skill for this?"* as a first-class part of the survey: check the available skills and prefer using a matching one over improvising your own approach. The strong default is to use the capability that exists rather than hand-roll.

### Dispatch (xo-cold-fast)

For broad scans across multiple sources or a large codebase, dispatch one or more `xo-cold-fast` agents in parallel — one per distinct source area:

```
runSubagent(subagent_type: "xocortex:xo-cold-fast", prompt: "
  PERSONA: Surveyor — scan for all references to [topic]
  INPUTS: [source area path or system]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/survey-[source-area].md
  
  Scan [source area] for any content related to [topic]. 
  Record what you find, where it lives, and any pointers to related material.
  Record negative results: what you checked and found nothing relevant.
")
```

Run multiple dispatches in parallel when covering genuinely distinct areas. Each writes its findings to `$XOCORTEX_HOME/tmp/wi{N}/survey-[area].md`.

### Inline survey (single source, quick)

For a single focused source, survey inline without dispatching a sub-agent:
1. Cast wide with a broad grep/glob
2. Collect pointers (paths, section names, document titles)
3. Do NOT read deeply — just locate

---

## Mode: targeted sweep

When you know the general area but need a complete inventory of everything in it:

1. Identify the root path or source system
2. Dispatch `xo-cold-fast` with PERSONA: "Surveyor — complete inventory of [area]"
3. Agent enumerates all relevant items with brief descriptions

---

## Output contract

**Survey establishes the edges of the bounding box** (the scope of pertinent facts). Report where the subject lives and what is in scope. Do not verify internals — that is Analyse. A gap in the map is an unbounded region: report it as a gap, do not omit it.

Write a **location map / inventory** to `notes/{YYYY-MM}/{project}-wi{N}-survey.md`:

```markdown
## Survey — [topic] ([date])

### Sources checked
- [source 1]: [what was checked] — [result: found X / no relevant results]
- [source 2]: ...

### Location map
- [item]: [where it lives] — [brief note]
- ...

### Gaps
- [what wasn't checked and why]
- [suggested next: Analyse on [specific target]?]
```

Present the map to the operator and ask: proceed to Analyse on [target], or Survey more?

**If the survey returned nothing or far less than expected:** treat this as a conflict to surface, not a conclusion to report. State what you searched, how you searched it, and why the result might be wrong (wrong location? wrong terms? wrong source system?). Suggest an alternative: *"I expected to find [X] but found nothing. Possible reasons: [A, B]. Shall I try a different search strategy?"* It is not acceptable to report a null result and stop — the user cannot act on a finding they don't have.

---

## Next step

Load `references/observe-analyse.md` to go deep on a specific finding from this survey.

Load `references/refs-survey-methods.md` for detailed traversal patterns and trust signals.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Triage (a research or investigation intent) — Survey is often where Observe starts.
- **Leads to — primary:** Analyse (go deep on a specific finding).
- **Or:** Distil (if breadth already answered the question and you just need to write it up); or stop, if the question is now answered.
