---
name: refs-survey-methods
description: "Discovery traversal patterns, local-first source checking, trust signals, and source tracing. Supporting reference loaded by survey.md and analyse.md."
---

# Survey Methods

Two discovery modes, each with different traversal strategies. The mode depends on operator intent and the shape of the question.

## Discovery Modes

### Survey Mode (Breadth-First)

**Trigger**: open-ended questions — "what do we know about X?", "what's the landscape for Y?", "who's working on Z?"

**Goal**: map the territory. Cast wide across source categories, collect signals, identify clusters of relevance before going deep on any one thread.

**Traversal**: scan across source categories (local → services → external), collecting pointers. Do NOT follow any single thread deep until the breadth pass is complete. Present the map to the operator, who decides where to go deep.

**When to use**: new topics, cross-cutting investigations, landscape mapping, "what exists" questions.

### Investigate Mode (Depth-First from Target)

**Trigger**: specific target — "investigate this repo", "what can we learn about service X?", "trace the history of this feature"

**Goal**: build out from a known starting point. Follow connections: who contributed → what teams → what related repos → what docs exist → what PRs are open → what decisions were made.

**Traversal**: start at the target, identify its connections, follow each connection one level, then decide which threads warrant further depth. Each level of depth should be a conscious choice, not automatic.

**When to use**: given a specific repo, service, team, PR, or system to understand in depth.

### Mode Selection

| Operator Language | Mode | Reason |
|-------------------|------|--------|
| "what do we know about…", "survey", "landscape" | Survey | Open question, need breadth |
| "investigate repo X", "look into service Y" | Investigate | Specific target, need depth |
| "find out who's working on Z" | Survey → Investigate | Start broad (who?), then follow specific threads |
| "what's changed recently in area A" | Investigate | Temporal focus on known area |

When unclear, default to survey — it's safer to go broad first than to tunnel into the wrong thread.

---

## Source Tracing

Every finding must record how it was reached. This serves two purposes: (1) the operator can verify the chain, (2) future sessions don't re-traverse the same path.

### Trace Format

In the notes file, record the discovery chain:

```markdown
### Source Trace
- Started: {initial query or target}
- Found: {finding} via {source system} → {specific query or path}
  - Led to: {next finding} via {connection type}
    - Led to: {deeper finding} via {connection type}
```

When a source yields nothing, record that too: `{source}: searched for "{query}" — no relevant results`. This prevents re-searching.

---

## Local Sources (Check All Before External)

**MANDATORY**: Before querying ANY external service, check ALL local sources. Do not short-circuit — gather from all, then assess which is most trustworthy.

### Check Order

| Order | Source | How to Check | Staleness Signal |
|-------|--------|--------------|------------------|
| 1 | Active WI notes | Read `notes/{YYYY-MM}/{project}-wi{N}-*.md` directly | Most recent by definition |
| 2 | The vault (notes + tasks + diary) | **Recall** (`references/observe-recall.md`) — not a bare grep | Recall returns dates; check StatusNote/recency on hits |
| 3 | `/memories/` directory | Glance the in-context `MEMORY.md` index; `memory view` a specific file if relevant | No metadata — may be stale or abandoned |
| 4 | External sources | Codebases, docs, APIs | After local is exhausted |

### Notes as Knowledge Base

Search the accumulated workspace with **Recall** (`references/observe-recall.md`) rather than a bare keyword grep — Recall expands the query into candidate terms first, so it bridges the vocabulary gap between how you phrase the search and how the note was written:

```
Recall: query = "[topic]"  → ranked full-path hits across notes/tasks/diary
```

Use a direct grep only for an exact known literal — a specific filename, error string, or identifier. For any topic or concept search, use Recall: a bare grep misses the vocabulary gap.

No results means "not yet documented" — NOT "nothing exists." Recall covers the authoritative vault (notes/tasks/diary); `/memories` is checked separately via the in-context memory index (provisional scratch — keep it labelled apart).

After checking local sources, consult any registered knowledge sources that match the topic and suggest them to the operator before searching them. Suggest, do not auto-search: *"You've previously found [source] useful for [domain] — want me to include it in this survey?"* Registered sources live in the same durable places as other XO references; use them as prompts for breadth, not as a silent routing override.

### Why Check All

- **Notes are current but narrow**: only recent WI context
- **Memories have no metadata**: could be from abandoned session, superseded, or still relevant
- **Contradictions are signals**: if two sources disagree, surface it to the operator

### Assessing Conflicting Sources

When multiple local sources have information:

1. **Check recency** — look at the date of the notes file or diary entry
2. **Check memory file age** — file modification date is only a proxy for freshness
3. **Note contradictions** — surface them to operator rather than silently choosing

---

## External Services

For specifics on each service (endpoints, table paths, tool lists), search prior WI notes and diary for any service investigations already done. This reference covers only the discovery process.

### Source Categories

| Category | What it Contains |
|----------|------------------|
| **Systems** | Repositories, platforms, container entities |
| **Services** | Access patterns, APIs, query tools |
| **Refs** | Point references, conventions, patterns |
| **External** | Public docs, upstream projects |

### Before Using a Service

Search accumulated notes for any prior investigation of this service with **Recall** (`references/observe-recall.md`):
```
Recall: query = "[service-name]"  → ranked hits across notes/tasks/diary
```
If found, use cached access methods and conventions.
If not, investigate directly and consider writing a durable note via Capture (`references/decide-capture.md`).

### Codebase Investigation

When the target is a specific repository:

1. Search notes/tasks for an existing article or prior investigation of that repo
2. If found, use cached conventions (AGENTS.md location, test commands, etc.)
3. If not, investigate directly; consider writing a durable note via Capture (`references/decide-capture.md`)

Standard locations to check in any repo:
- `AGENTS.md` / `CLAUDE.md` — agent-specific guidance
- `README.md` — project overview
- `.github/instructions/` — coding standards
- `CONTRIBUTING.md` — contribution guidelines
- `package.json` / `Cargo.toml` / etc. — dependencies, scripts

**For substantive work (new features, understanding prior decisions), surface the offer to check attendant services before expanding scope:**
> "I'm about to survey [repo]. For feature-level work it's worth checking attendant services — PR history, issue tracker, wiki, Jira — to surface prior decisions, non-goals, and abandoned approaches. Do you want me to include those, or just the codebase?"

Skip the offer for bug fixes and targeted lookups where breadth of prior art is irrelevant. When the operator says yes, specifically look for: WONTFIX decisions, abandoned PRs and their reversal reasons, and any tests or docs that explicitly assert "NOT SUPPORTED" — these are the evidence for non-goals in the spec.

**If Survey surfaces reusable standards or knowledge sources (test commands, build conventions, CI setup, AGENTS.md location, PR workflow, doc styles, a useful KB, Glean collection, Confluence space, or MCP), these are candidates for Capture** regardless of the depth of investigation. Offer once: *"I found conventions or reference sources worth keeping — want me to record them so future sessions don't have to re-discover?"* Persist global sources as a `reference`-type memory entry; persist repo- or customer-specific sources in that repo's `AGENTS.md` domain note. Keep it operator-gated: propose the capture, do not auto-persist. See Capture (`references/decide-capture.md`). Notes written this way can be refreshed deliberately when the repo changes.

---

## Traversal Patterns

| Pattern | When | Example |
|---------|------|---------|
| **Breadth scan** | Survey mode — map the territory | Local sources → services → external, collecting pointers |
| **Depth chase** | Investigate mode — follow a thread | Repo → contributors → their other repos → related docs |
| **Hypothesis test** | After first pass reveals a pattern | "I think team X owns this" → verify via multiple sources |
| **Contrast** | Comparing approaches | How does repo A solve this vs repo B? |
| **Temporal** | Understanding change over time | Git log → PR timeline → ticket history |

### Deciding Breadth vs Depth

After each discovery pass, present findings and ask the operator:
- **"Go wider"** → next source category in the breadth scan
- **"Go deeper on X"** → switch to investigate mode on that thread
- **"Sufficient"** → proceed to Orient

The operator controls traversal. Do not automatically chase every thread — present the map and let the operator choose where to invest attention.

---

## Source Reliability

Discovery surfaces what exists — but not all sources are equally trustworthy. Present trust signals alongside findings.

### Trust Signals

| Signal | Where to Check | Implication |
|--------|---------------|-------------|
| **Last updated** | Git log, page metadata, captured-note date | >1 year = flag as potentially stale |
| **Activity level** | Commit frequency, PR activity, edit history | Active = higher trust; dormant = skepticism |
| **Source type** | What kind of artifact | Code > tests > internal docs > wiki > old Confluence |
| **Note recency** | Date stamp on a captured note or diary entry | Recent = more likely current |
| **Contradictions** | Multiple sources say different things | Flag for operator resolution |

### Source Type Hierarchy

| Tier | Source Type | Trust Level | Rationale |
|------|-------------|-------------|-----------|
| 1 | Active code, passing tests | Highest | Compiles, runs, verified by CI |
| 2 | Recent PRs, code review discussions | High | Current thinking, actively debated |
| 3 | Recent captured notes (this WI or recent sessions) | Medium-high | Written from verified findings |
| 4 | AGENTS.md, README in active repos | Medium-high | Maintained alongside code |
| 5 | Recent Confluence (<6 months), Jira tickets | Medium | May lag behind code changes |
| 6 | Older captured notes (prior WIs, undated) | Medium-low | Better than nothing, verify currency |
| 7 | Old Confluence (>1 year), archived wikis | Low | Likely stale, may contradict current state |
| 8 | Memories (no metadata) | Low | Unknown staleness, may be abandoned |
| 9 | Slack threads, email snippets | Lowest | Ephemeral, context-dependent |

### Annotating Findings

When presenting findings, include trust signals:

```markdown
### Key Findings
- {finding}
  - Source: {where} | Updated: {when} | Trust: {signal}
  - ⚠️ Stale: last updated 2+ years ago
  - ⚠️ Contradicts: {other source says X}
  - ✓ Active: 15 commits this month
  - ✓ Confirmed: captured note dated 2026-03-01
```

---

## What Discovery Does NOT Do

Discovery surfaces what exists and flags trust signals. It does NOT:
- Block progress on unvalidated findings (that's the operator's call)
- Guarantee correctness (it finds *interesting*, not *verified*)

When the operator needs to capture a durable learning, route to **Capture** (`references/decide-capture.md`).
