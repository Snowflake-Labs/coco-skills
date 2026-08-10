---
name: decide-capture
description: Capture stage — keep durable learnings, operating rules, and environment facts for next time. Learnings → workspace notes; operating rules → memory (for Curate to place); environment facts (CLI profiles, paths) → the open project's AGENTS.md. Optional promote-out to a shared system; reflexive nudges on workflow events.
---

# Capture

Decide what to keep. Capture is a triggered reminder to consolidate useful context into a form the next session can find and use — without ceremony and without requiring a curated knowledge base.

The workspace notes are the knowledge base. Capture writes to them explicitly, with enough structure that a future grep or read will surface what was learned.

## When to use this stage

- After Review or Workshop, when a clear learning emerged that should survive beyond this session
- After an investigation that uncovered useful facts about a system, repository, or process
- When you had to ask for or rediscover an environment fact (CLI profile, repo/log path, service name) you will need again
- When the operator says "capture this" or "I want to remember that"
- When a reflexive sub-mode surfaces the reminder (see below)

---

## Mode: capture a learning (default)

Decide whether the learning warrants a durable note.

| Finding type | Worth capturing? | Why |
|---|---|---|
| Investigated a system in depth; will access again | Yes | Saves re-discovery |
| Discovered repository standards (build, test, CI, conventions) during Survey or reconnaissance | Yes | Future sessions use cached conventions; add a refresh note so it can be updated when the repo changes |
| Found a useful access pattern with nuance | Yes | Routing guidance |
| Validated or invalidated a prior assumption | Yes | Update the record |
| Trivial or one-off lookup | No | Not worth maintaining |
| Too uncertain to commit | No | Note it in the active WI notes instead |

---

## Writing the durable note

Write to `notes/{YYYY-MM}/_capture-[topic].md` (distinct from WI-specific notes):

```markdown
# [Topic] — [brief title]

**Captured:** [date]
**Context:** [brief: what prompted this capture]
**Confidence:** high / medium / low

## What is true

[The key findings, stated as facts with evidence.]

## How to access / use

[If this is about a system or tool: how to reach it, what commands work, gotchas.]

## Expires / review

[When this might go stale: "review if X changes", "likely valid for ~6 months"]

## Source

[Link to the WI notes or diary entry where the full investigation lives.]
```

---

## Mode: capture an operating rule

Distinct from a learning (a fact about a system). An **operating rule** is an instruction, preference, or constraint on *how to work* — usually surfaced when the operator corrects you or states a standing preference ("never do X", "always do Y first").

When one emerges, propose recording it — do **not** place it durably yourself (placement is Curate's job, in Cleanup):

1. **Propose the default:** record it to memory as `/memories/feedback-<slug>.md` (a `type: feedback` entry, with a one-line **Why** and **How to apply**) so it survives this session and can be placed by the next Curate pass.
2. **Offer a redirect gate:** "I'll record this rule to memory so it persists — or do you want it somewhere specific (this project's `AGENTS.md`)?" The operator accepts the default or redirects.
3. **Record per the operator's choice.** If recorded to memory, add it to `/memories/MEMORY.md` so it is indexed.

Capture detects and records the candidate; it does not classify local / project / plug-in or place it. That is **Curate** (in Cleanup). Recording to memory is the safe default; Curate places it later.

---

## Mode: capture an environment fact

An **environment fact** is a stable, project- or machine-specific operational detail the agent needs to act correctly and repeatedly has to ask for or rediscover: a required CLI profile or flag (e.g. which AWS profile to use, or always invoking Python via `uv`), a repo/checkout/log/asset path, a connection or service name. Distinct from a *learning* (how something works) and a *rule* (how to behave).

Record it in the **open project's `AGENTS.md`** under an `## Environment` section. `AGENTS.md` is auto-loaded, so the fact stays in context every session with no lookup step. Usually a few lines.

1. Offer when you had to ask for it or found it by trial: "Record this in the project `AGENTS.md` so I have it next session? — e.g. which AWS profile to use, or 'always run Python via uv'."
2. On yes: append a terse keyed line under `## Environment` (create the section if absent). Factual and short.
3. The operator corrects it on demand ("that path moved — update it").

Treat entries as provisional: confirm a path or profile still resolves before relying on it, and update the line if it has drifted.

Example `## Environment` block:
```
## Environment
- aws: use the project's designated profile for all `awscli` calls
- python: always run via `uv`
- repo: primary checkout at `/path/to/checkout`; logs at `/path/to/logs`
```

---

## Mode: promote out

When the captured learning is useful beyond the private workspace — for a shared team wiki, a docs site, a Confluence page, a Slack summary:

1. Write the durable note as above
2. Ask the operator:
   > "This looks worth sharing more broadly. Want to promote it to [suggested destination - repo, wiki, docs]? I can prepare it for posting."
3. If yes: prepare a version for the target audience (strip internal references per shared-content policy) and route to the appropriate channel

**Promote is always optional and operator-driven.** Never auto-promote.

---

## Mode: promote artefacts to durable storage

Beyond the distilled learning, decide whether any **artefacts** produced during the work — findings briefs, design explorations, review briefs sitting in `$XOCORTEX_HOME/tmp/wi{N}/` — deserve to survive. Scratch is discarded at Cleanup, so anything worth keeping must be promoted here:

```bash
mv "$XOCORTEX_HOME/tmp/wi{N}/<artifact>.md" "$XOCORTEX_HOME/notes/$(date +%Y-%m)/wi{N}-<slug>.md"
```

Capture is where you decide artefact durability; Cleanup executes the teardown of whatever wasn't promoted. Promote selectively — most scratch is transient; keep only the artefacts a future session would genuinely want to re-read.

---

## Reflexive sub-modes

These reminders surface automatically — they are opt-in nudges, not blockers.

### Workflow-event: post-Review reminder

After any Review stage completes, surface once:
> "Worth capturing any learnings from that review? (e.g. new patterns, access methods, common mistakes found)"

The operator responds or ignores. No follow-up.

### Workflow-event: asked for an environment fact

When you have to ask the operator for an environment fact you will likely need again (a CLI profile/flag, a repo/log path, a service or connection name), offer to record it in the open project's `AGENTS.md` — see *Mode: capture an environment fact*. Surface once, at the point of asking.

---

## Output contract

Write the durable note to `notes/{YYYY-MM}/_capture-[topic].md`.

If the operator declines to capture ("nothing new"), record the explicit skip decision in the active WI notes:
```markdown
### Capture
Skipped — [reason: e.g. "learnings already in WI notes", "too shallow to capture separately", "one-off lookup"]
```

This makes the skip a conscious decision, not a forgotten step.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Review, Document, Ship, or any substantial work that produced a durable learning or artefacts worth keeping.
- **Leads to — primary:** Cleanup (terminal teardown — anything not promoted to durable storage is discarded there).
- **Or:** Promote out (share the learning beyond the workspace — see *Mode: promote out*).
