# XO

*/ˈɛksoʊ/ — from exocortex. Smarter rocks.*

XO is an operator workflow system (an "AI harness") for [Cortex Code](https://www.snowflake.com/en/product/features/cortex-code/) — Desktop and CLI. It helps an AI agent operate as a reliable partner across multi-session, multi-step knowledge work — documentation, problem-solving, analysis, coding, testing, and research — with structure and human supervision.

It is delivered as a **plugin** containing hooks, a skill, and specialised subagents:

- **Automatic, durable memory** — session-recording hooks keep a local markdown trail (diary, notes, tasks, checkpoints) so context survives summarisation and compaction, and work resumes cleanly across sessions. Every significant session gets a local "Work Item" number.
- **Composable stages** — a builder's tool belt mapped to an OODA loop (**Observe → Orient → Decide → Act**), so the agent reaches for the right step at the right moment: never forcing heavyweight process onto a small job, never skipping discipline on a large one.
- **Specialised subagents** — proposer / critic / verifier roles for scoped, reviewable work.

The stages are artifact-type-neutral — the same discipline applies whether the output is code, a document, a Snowflake object, a configuration, an app, or a demo. Most work uses only a slice (a typo fix goes straight to build-and-verify; documentation runs analyse → distil → document → prove → ship).

## What XO encodes

XO's value is what it makes an agent do reliably. Two parts: the **phases** it composes per task, and the **correction practices** that sit around the whole piece of work. None are code-specific — they apply to whatever gets shipped (a PR, a document, a config, an email), and all are plain files you can read, tune, or override.

### Phases

| Phase | Role |
|---|---|
| Triage | Classify the request and route it to the right phases before acting |
| Survey | Breadth-first discovery: locate where the subject lives |
| Analyse | Depth-first investigation: establish verified facts |
| Recall | Search the memory vault of past notes, tasks, and decisions |
| Distil | Compile raw evidence into a findings brief |
| Workshop | Explore several angles in parallel; bring back candidates for you to choose |
| Spec | Write the bounding contract: goal, requirements, out-of-scope, done |
| Plan | Turn the spec into an implementation plan |
| Provision | Set up the workspace: branch, scratch, local/cloud infra |
| Build | Produce the work, with parallel critics and a verifier, holding scope |
| Document | Write the guide when the artifact is prose rather than code |
| Prove | Verify: tests, interactive checks, state validation, or citation audit |
| Ship | Deliver: a PR, a publish, a deploy, or a hand-off |
| Capture | Keep the durable learnings from the work |
| Cleanup | Prune and consolidate so the memory stays a help, not a hoard |

A task uses only the phases it needs, in sequences XO composes and reuses.

### Correction practices

Each addresses a specific failure mode of an unsupervised agent:

- **Notes as you go** — records decisions into plain files as it works, so a long job survives the context limit and resumes later instead of starting over.
- **Named phases** — tracks the next action and where it is in the sequence, so when a task grows mid-flight it can split off the blocker and still resume in place.
- **One place for the work** — capture anything, however rough, where the work happens; nothing leaves your hands until you Ship it, and then the checks apply at the boundary.
- **Organisation across task-switching** — structured notes, a daily diary, and a low-overhead task queue hold what was decided, what was touched, and what's still owed, so you move between demands without carrying the organisation in your head.
- **Curated memory, not a hoard** — captured findings and rules are periodically promoted into the vault, and pruned (stale ones dropped), with a nudge when curation is overdue, so memory compounds instead of rotting or being re-learned.
- **Durable, portable memory** — everything is plain text in a git-backed vault you own; the harness nudges you to commit and push once it has gone unsaved for a while.
- **Delegate closed questions** — sends a subagent to fetch just the relevant past work, so the main session stays on your goal instead of reloading everything.
- **Isolation before parallelism** — each concurrent session gets its own workspace and makes session-local, never global, changes, so parallel runs don't collide.
- **Chain of evidence** — every claim traces to a file read or command run; what couldn't be verified is listed as a gap, not smoothed over.
- **Cold finder, warm writer** — one agent gathers facts and invents nothing; a separate one writes using only those facts, so the writing can't quietly fill a gap.
- **Null-result reasoning** — an empty search prompts a second hypothesis before concluding a thing isn't there.
- **Triage first** — reads what you actually asked, and how large it is, before acting.
- **Survey before create** — checks whether a thing already exists (locally first) before building, and records what it didn't find.
- **Spec and operator gates** — you approve a short scoped contract before anything is built; it stops to report at decision points rather than running ahead.
- **Workshop the options** — for real trade-offs, explores several angles in parallel and brings them back for you to choose.
- **Cold critic** — the agent that produced the work never signs it off; a fresh one checks it against the goal, and can't pass on "it ran without errors" alone.
- **Review the whole chain** — checks the reasoning, not just the result, so a wrong upstream assumption doesn't slip through tidy output.
- **Complexity gate** — matches effort to the change: mechanical fixes apply fast; judgment calls re-enter the full critique before shipping.

## Prerequisites

`node`, `git`, `gh`, and `jq` on your PATH.

## Install & set up

Install the plugin through Cortex Code, then run the bundled setup once. See **[SETUP.md](SETUP.md)** for the happy-path install (zero-config or git-backed) — it sets `XOCORTEX_HOME`, puts `node` on the hooks' PATH, and optionally creates a git-backed memory vault.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
