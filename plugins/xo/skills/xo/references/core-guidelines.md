---
name: core-guidelines
description: Cross-cutting operating rules for all XO stages — grounding discipline, source order, gates, storage, and per-phase invariants. Loaded at session start with save-protocol.
---

# Core Guidelines

Operating rules that apply across all stages. Load at session start. Individual stage references add their own procedure on top of these.

Routing/navigation discipline (triage, load-the-reference, gates, no-skip) lives in the SKILL.md Hard rules. This file is execution discipline *within* a stage.

## Grounding and the bounding box

The Observe and Orient stages establish the facts pertinent to the task before any commitment to action. **The bounding box** = the scope of facts pertinent to the task: Survey establishes its edges, Analyse establishes the facts inside it, Workshop tests implications inside it.

- **Ground every claim.** Cite the evidence obtained for it — a file read, a command run, a path traced. A claim stated as fact without obtained evidence is an assumption; an assumption presented as fact is a **silent failure** (a defect that reads as success).
- **Stay inside the box.** Report only facts and failure modes the artifact and request support. Do not add speculative risks they do not exhibit.
- **Set depth by the box, not by confidence.** Calibrate effort to what the downstream stage requires. Confidence and token count are not evidence of sufficient coverage.

## Source order (local-first)

Check local sources before external services: (1) active WI notes (read directly), (2) the vault — notes/tasks/diary — by known identity directly, or by terms/topic/concept via **Recall** (`references/observe-recall.md`), (3) `/memories/` via the in-context `MEMORY.md` index, (4) external. Record negative results ("checked X for Y — none found") — they prevent re-traversal.

**tgrep (optional accelerator, interactive only):** when searching the vault or a code repo interactively and a warm `tgrep` index is available, `tgrep` semantic/keyword search is a fast meaning-ranked complement (Desktop; needs Snowflake embed access). It does **not** replace the above — Recall stays the vault default, and grep/Recall remain the fallback whenever tgrep is unavailable, the index is cold (it says so), the context is unattended/hook, or you need exact-literal matching.

## Freshness and concurrency

XO is multi-session: the vault and other shared files may be edited by concurrent sessions at any time. **Having read a file is not the same as having its current state.** A read of a shared file — the WI index, another WI's task, the WI counter, shared code, repo files — is valid only for the turn it was made. Re-read immediately before editing or relying on it; never act on a read from an earlier turn or from before a subagent dispatch. Treat tallies, greps, and `git status` as point-in-time snapshots, not durable truth.

Exception: a work item's own attached files — its task file, notes file, and `Scratch:` dir — are effectively owned by the session working that WI and are not normally touched by others. You can treat them as stable within your session without re-reading every time.

**Remote objects have a discussion surface that also goes stale.** For a PR, a commented document, or a thread, the code or artifact is only half the state — the comments, reviews, and thread resolutions are the other half, and they move independently (an automated reviewer or a human may comment while you work, even with no new commit). Before any irreversible action against such a target (post, push, merge, publish), re-fetch **both** surfaces and reconcile. Treat "the commit hasn't changed" as insufficient evidence that the object is unchanged.

## Survey before create (reflexive)

Before ANY novel work (skill, doc, flow, fix, design): check whether it already exists or is already solved. Survey internally first (existing skills, workspace inventory, prior WIs/notes via Recall, the relevant repos), then externally (established patterns, MCP, Knowledge bases, etc.). Extend existing work rather than duplicate it. Reflexive — survey before creating, not after being prompted.

## Execution discipline

- **Edit with the tools, not the shell.** Use Edit/Write for normal files (notes, tasks, code); reserve shell append (`>>`) for the append-only diary. Never edit prose via `sed`/`echo`.
- **Stage edits reviewably.** Discuss prose edits before making them; change in small reviewable steps. No mass rewrites or blind find-replace.
- **Never present untested as confirmed.** A proposed solution is not a verified one — say "untested" until proven; build and run a reference implementation rather than caveating in place of testing.
- **A correction signals an upstream failure.** When the operator corrects you, check if the findings or spec were wrong — fix and re-validate those first; do not assume a freshly-found source is the answer and re-implement on it. If that correction edits already-produced work, classify the diff's materiality (TRIVIAL vs SUBSTANTIVE per `references/refs-complexity-gate.md`) as an automatic reflex; surface the verdict to the operator in one line, override-able. A SUBSTANTIVE edit re-enters the Act loop — re-critique first, then re-prove — before the operator gate; TRIVIAL edits may fast-path but are still noted. When you present at the gate, summarise what the re-critique found and what changed since the operator's feedback.
- **Never broad-search `~` or `Projects/`.** Do not recurse the home or projects tree with a slow search; if you cannot find something, ask the operator for the path.
- **Never `gh auth switch` the shared global account.** Scope GitHub auth per call (`GH_TOKEN=…`) — parallel worktrees and sessions clobber the global `gh` auth and break each other.

## Gates and phase boundaries

- **Spec and Plan are operator gates.** No execution begins without an approved spec (and, for significant work, plan) or explicit operator direction.
- **Stop and report at phase boundaries.** Produce the current stage's output and let the operator decide whether to continue. Do not run ahead into the next phase inside the current one.

## Storage

- **Scratch (transient):** use the `Scratch:` path from the WI task file (`$XOCORTEX_HOME/tmp/wi{N}/`) for subagent outputs, drafts, and intermediates. If the field or dir is missing, `ls` then `mkdir -p` once — never invent an alternative path. When dispatching subagents, the OUTPUT_PATH/WORKTREE you hand them must sit under this scratch dir — never a system-temp path (`/tmp`, `$TMPDIR`, `/var/folders`), `$HOME`, or an arbitrary location. The agent templates are contracted to confine every write to the paths they are given and to write nothing to system temp, `$HOME`, or arbitrary locations; do not undercut that by handing them an out-of-scratch path.
- **Durable:** keep-worthy artifacts go to `notes/{YYYY-MM}/`.
- **Memory namespace (`/memories/…`):** the memory-tool store — reached **only** via the `memory` tool (`memory view` / `memory create`), never the host filesystem. `/memories/…` is a tool namespace, **not** an absolute path: the host has no `/memories`, so never `ls`/`cat`/`grep` it from the shell. In prose it is always tool-attributed so it never reads as a host path; shown as a `memory` command argument it is the literal `path` you pass.
- **Reviewable artifacts.** Every stage writes something the operator can check.

## Per-phase invariants

- **Observe** — every finding records how it was reached (source trace). Do not start Orient inside Observe.
- **Orient** — Distil traces to observed facts, not inference. Workshop synthesis stays with the orchestrator and operator; sub-agents report angle findings but never the final recommendation.
- **Decide** — Review is a critical evaluation, not a writeup: a cold Critic evaluates the artifact and its work-chain against the spec; approval on passing tests alone is prohibited. Capture is the expected close-out of shipped work — offered proactively and operator-gated (declinable, not skipped by default).
- **Act** — Provision before production (never produce in the operator's main environment without permission). Build requires a spec; Document requires findings. Cleanup is the last step; do not leave workspaces, scratch, or deployed infra behind unrecorded.
