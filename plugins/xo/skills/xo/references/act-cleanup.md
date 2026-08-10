---
name: act-cleanup
description: Cleanup stage — teardown the workspace and run hygiene processes. Removes worktrees, clears scratch files, checks infrastructure, runs Curate (the supervised-dreaming memory pass — prune + promote), commits the durable vault. Timing-based hygiene reminders (repo commit/push, Curate) are surfaced by the SessionStart hook; Cleanup runs the processes when the operator accepts a nudge.
---

# Cleanup

Tear down the workspace and run hygiene processes. Cleanup is always the last step of any work that modified files or deployed infrastructure. It also carries the hygiene *processes* (Curate — the supervised-dreaming memory pass — and durable commit/push) that the SessionStart hook's time-based nudges point to, plus reflexive sub-modes that surface good-practice reminders at the right moment.

## When to use this stage

- After Ship (PR merged or closed)
- After any work that created a worktree, deployed infrastructure, or left temporary files
- At session end, to trigger the session-lifecycle hygiene checks

---

## Mode: scratch directory clear (universal — always applies)

Subagent handoff artifacts and other transient files for this work live under the canonical per-WI scratch dir `$XOCORTEX_HOME/tmp/wi{N}/` (the `Scratch:` path in the WI task file, allocated at WI creation). At the end of the work, inventory what exists and propose a disposition to the operator before taking any action.

### Process

1. **Inventory** — list what's in the scratch directory:
   ```bash
   ls "$XOCORTEX_HOME/tmp/wi{N}/"
   ```

2. **Propose disposition** — present the inventory to the operator with a recommended action for each artifact:

   > "Scratch artifacts for WI-{N}:
   >
   > | Artifact | Recommendation |
   > |----------|---------------|
   > | `build-verifier.md` | Discard (transient gate output) |
   > | `workshop-approach-01.md` | Promote to durable note (contains design reasoning) |
   > | `doc-guide-draft.md` | Discard (final version already shipped) |
   > | `prove-verdict.md` | Promote (contains validation evidence) |
   >
   > Shall I proceed with these, or would you like to adjust?"

3. **Act on approval** — only after operator confirms:
   - Promote designated artifacts: `mv "$XOCORTEX_HOME/tmp/wi{N}/<artifact>.md" "$XOCORTEX_HOME/notes/$(date +%Y-%m)/{project}-wi{N}-<slug>.md"`
   - Clear the rest: `rm -rf "$XOCORTEX_HOME/tmp/wi{N}/"`

---

## Mode: worktree removal (repository work only)

Remove the worktree created in Provision:

```bash
git -C <repo> worktree remove <worktree-path>
git -C <repo> branch -d <branch-name>   # only after PR is merged
```

If the worktree has uncommitted changes:
```bash
git -C <repo> worktree remove --force <worktree-path>
```

Confirm the worktree is gone:
```bash
git -C <repo> worktree list
```

---

## Mode: infrastructure teardown checklist

When the work deployed infrastructure (services, containers, databases, pipelines):

Present a checklist to the operator:

> "The following infrastructure may still be running from this work:
> - [item 1]: [what it is, where it runs]
> - [item 2]: ...
>
> For each item: tear down now / keep for now / defer?
> (Anything deferred is recorded in the task notes.)"

Record the decisions in the WI notes. Deferred items get a reminder at the next session start (via the SessionStart hygiene hook).

---

## Mode: Curate (supervised dreaming)

Trigger: the session-start nudge ("your `/memories` were last curated Nd ago"), or operator request. Run only on operator opt-in. Treat memory as scratch, not a sanctioned record (Memory Trust). Operator-gate every removal and every promotion. (Curate is XO's supervised-dreaming pass.)

### Step 1 — Enumerate the real store first

Do NOT treat `/memories/MEMORY.md` as the inventory — it is a curated subset that under-reports. List the ACTUAL store before deciding anything:

- `memory view /memories`, then `memory view` each subdirectory it lists, recursing fully — the directory view is one level deep, so subdirectory contents are hidden until you descend.
- If you have filesystem access to the memory directory, `find` it directly.

Reconcile against `MEMORY.md` at the END (Step 6), never as the starting inventory.

Exclude from Curate (operational state and durable reference data, not curatable memory): `xo/`, `context-state/`, `projects/` plumbing, `checkpoints/`, `github/`; and **reference data** — any file consumed as machine data rather than curatable knowledge (config, inventory or credential maps, schemas, infra JSON). When unsure whether a file is reference-data or a stale note, ESCALATE — do not guess. Leave excluded files untouched.

### Step 2 — Classify each entry

Classify per **rule/note**, not per file — one file may hold several rules with different targets (split it in Step 6).

- **Operating rule** — an instruction, preference, or rule for how to work (e.g. `feedback*`, `MANDATORY_INSTRUCTIONS`). → PROMOTE path (Step 4).
- **WI working note** — WI-scoped state/investigation/progress (e.g. `wi{N}*`). Violates "updates in notes, not memory." → PRUNE path (Step 3), joined by WI number.
- **Transient session state** — point-in-time handoffs/session logs (e.g. `session-*`, `handoff-*`, `*_session_*`). Superseded by definition. → PRUNE path (Step 3), by pattern — no counterpart lookup.
- **Reference data** — excluded in Step 1.
- Fits none of these → ESCALATE.

### Step 3 — Prune path: reconcile, never blind-delete

Decide per item — never assume "memory is stale, delete it" nor "the vault is current, ignore memory." `mtime` is a HINT only; content is the authority — so "memory is newer" is a Tier-1 judgement, never a Tier-0 one. Match counterparts by **work-item NUMBER**, not filename string — extract the number from each file's content header (`# WI-N`) or filename (handle `wiN`, `wi-N`, and embedded forms), then join on the number. Vault notes/tasks use varied slugs and month folders, so a filename-string match misses real counterparts and a *failed* filename match does NOT prove orphan. Build the WI-number sets ONCE (memory; vault `tasks/archive/`, `tasks/current/` + each task's `Status`, `notes/`) and membership-test — never scan the vault once per memory file (O(files×vault), crawls).

- **Tier 0 — cheap signals (auto-triage the bulk; pattern + existence only, no content read):**
  - *Transient session state* (`session-*`, `handoff-*`, `*_session_*`) → **prune by pattern** (superseded point-in-time logs; bulk-approve group).
  - *WI working note + WI settled (archived in `tasks/archive/`, OR its `tasks/current/` task is `Status: Done`) + a vault counterpart exists* → **superseded → prune** (bulk-approve group).
  - *WI working note + WI still active (open task in `tasks/current/`)* → escalate to Tier 1 (memory may hold live state).
  - *No counterpart and no recognised transient pattern (orphan)* → **escalate** with a recommendation (promote-to-note if it looks durable, else discard). NEVER auto-prune or auto-promote an only-copy.
- **Tier 1 — content reconciliation (escalated only):** hash both first (e.g. `shasum`) — byte-identical → exact duplicate → prune the copy with no read. Not identical → read both and compare (a fast proxy: grep the memory file's distinctive tokens — commit hashes, error codes, unique phrases — in the counterpart). Memory content is a subset → prune. Unique or newer content → merge the unique bits into the durable note, THEN prune the copy. For *active* WIs especially, a same-topic vault note is NOT proof of subset.
- **Tier 2 — operator adjudication:** orphans and unresolved conflicts → carry to the disposition table (Step 5) with a recommendation.

### Step 4 — Promote path: classify each rule, then place it

For each operating rule, determine its relationship to the authoritative durable surface, then place it. Default placement offered to the operator = this project's `AGENTS.md`.

- **Universal** (applies to any user, any project) → the plug-in. Sub-target: routing rule → `SKILL.md` Hard rules; execution rule → `core-guidelines.md`; stage-specific → that stage reference; always-on → SessionStart hook directive. Plug-in promotion is a reviewed edit to the xo repo, not an in-place memory write.
- **Environment/project-specific** (this operator's accounts, paths, infra) → the project's `AGENTS.md` (durable, git-backed). The default suggestion.
- **Ephemeral or uncertain** → leave in local memory.
- **Already represented** in the plug-in or `AGENTS.md` → prune the redundant memory copy (dedup).
- **Another skill's or plug-in's domain** (e.g. a NiFi rule belongs to the `openflow` skill, not XO) → ESCALATE: out of XO's promote scope; recommend the target skill and let the operator route.

A file holding several rules may split across targets — classify each rule, carry each disposition separately, and split the file in Step 6.

Same reconciliation discipline: if a rule is divergent from its durable counterpart, reconcile before pruning the copy. Provenance/currency lens: did the *user* attest the rule, or did the *agent* infer it? Is it still current? Bias — prune unverifiable agent-inferred claims; keep user-given instructions unless superseded.

### Step 5 — Present the disposition table, then gate

Present ONE table to the operator: each entry, its classification, the recommended action (prune / promote→target / reconcile / keep), and the signal or reason. The operator bulk-approves the clear-cut rows and adjudicates the flagged ones. Apply only on approval. Never delete or promote silently.

### Step 6 — Rebuild the index; normalise

After applying: where a multi-rule file split across targets, extract each rule to its target and remove it from the source (delete the file only if nothing remains). Rebuild `/memories/MEMORY.md` so it matches what remains (the index should reflect the store). Normalise naming conventions (one consistent prefix, e.g. `feedback-`).

### Step 7 — Stamp the watermark

Set `memoryPrune.lastPruned` to today's date in `/memories/xo/hygiene-state.json` (via the memory tool) so the nudge resets — the watermark marks "store reviewed", not "N pruned", so stamp it after any real review pass. Decline handling is in the Session-lifecycle reflex below (record `lastDeclined` if the operator declines the nudge).

---

## Mode: durable commit/push

The session-start hygiene nudge ("your xocortex vault was last committed Nd ago / has N unpushed commits") points here. When wrapping up, offer to commit and push the vault so notes/tasks/diary are durable:

```bash
git -C "$XOCORTEX_HOME" add -A && git -C "$XOCORTEX_HOME" commit -m "<summary>" && git -C "$XOCORTEX_HOME" push
```

Only with operator approval, and only the xocortex vault — never push project code as a side effect of Cleanup. Committing resets the staleness the hook tracks (it reads git directly; no watermark needed).

---

## Reflexive sub-modes

These surface as gentle reminders — never blocking, never automated without operator confirmation.

### 1. Workflow-event: intermediate artifact pruning

Trigger: when the operator confirms the outcome of the work is good (approval of a brief, merge of a PR, or explicit "we're done").

Surface once:
> "We're done with this work. Want to clean up intermediate artifacts?
> [list `$XOCORTEX_HOME/tmp/wi{N}/` contents that aren't the final deliverable]"

---

### 2. State-threshold: infrastructure deployed

Trigger: when the task notes or task status indicates deployed infrastructure.

Surface once per session:
> "Heads up — [infrastructure item] is still deployed from this work. Tear it down, or keep it running?"

---

### 3. Session-lifecycle: hygiene reminders surface at session start

The durable-repo commit/push reminder and the Curate reminder are surfaced by the **SessionStart hook** (`hooks/sessionstart.js`, `buildHygieneNudges`). The hook decides *whether to present* a nudge from two inputs: the live condition (vault last committed >7d ago or unpushed commits >0; `/memories` last curated >30d ago) and a **decline watermark it never writes itself**.

Recording the operator's response is the agent's job, not the hook's — the hook cannot know whether the operator actually saw or answered a nudge (a watermark written on generation silently consumed nudges on compaction-triggered starts, which do not surface them). So when a hygiene nudge appears:

- **On decline / "not now":** record it so the nudge does not re-nag. Set the matching `lastDeclined` to today (ISO date) in `/memories/xo/hygiene-state.json` via the memory tool — `repoCommit.lastDeclined` for the commit/push nudge, `memoryPrune.lastDeclined` for the Curate nudge. The hook then suppresses that nudge for 7 days (`DECLINE_DAYS`).
- **On accept:** run the matching process above (**Mode: durable commit/push** or **Mode: Curate**). No watermark write is needed — acting resets the condition: a commit clears staleness/unpushed; a completed Curate pass stamps `memoryPrune.lastPruned`.
- **If neither (ignored):** the nudge re-presents at most once per session start until answered — intentional, so it reminds rather than silently dropping.

---

## Output contract

- Worktree removed (confirmed via `git worktree list`)
- Scratch files cleared or recorded as kept
- Infrastructure decisions recorded in WI notes
- Task status updated to Done if work is complete; archive via Update Task File (`references/save-protocol.md`)

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Capture (capture durable learnings — and promote any artefacts worth keeping — *before* teardown) and Ship. Haven't Captured? If there's nothing durable, say so and proceed.
- **Leads to:** terminal — nothing follows Cleanup.
