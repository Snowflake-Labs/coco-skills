---
name: migrate-spark-java-to-snowpark-connect
description: |
  Migrate Spark Java workloads to Snowflake SCOS (Snowpark Connect for Spark).
  Use when: converting Java Spark code to run on Snowflake, analyzing Java Spark compatibility,
  updating imports to Spark Connect equivalents, or migrating from standalone Spark Java.
  Generates SMA-compatible reports (Issues.csv, InputFilesInventory.csv, ArtifactDependencyInventory.csv)
  for the dvp-sma-dashboard-generator using official SMA EWI codes (SPRKCNTSCL* — JVM family).
  Triggers: migrate java spark, convert java spark, scos java migration,
  spark connect java, java compatibility, snowpark connect java.
parent_skill: snowpark-connect
allowed-tools: Read, Write, Bash, Task
---

# Migrate Spark Java to SCOS — Coordinator

Orchestrate a multi-phase migration of Spark Java workloads to Snowflake SCOS (Snowpark Connect for Spark). This coordinator delegates work to specialist sub-agents and validates each phase with the deterministic `verify_phase.py` script before advancing.

## When to Load

[snowpark-connect] Intent Detection: After user indicates migration intent for Java code (convert, migrate, update imports, rewrite for SCOS).

## Arguments

- `$ARGUMENTS` — Path to the Spark Java file or directory to migrate

### Optional Metadata (from orchestrator)

| Parameter | Variable | Description |
|-----------|----------|-------------|
| Output path | `$OUTPUT` | Target directory for migrated files and Reports/ |
| Customer Email | `$EMAIL` | Project metadata for reports |
| Customer Company | `$COMPANY` | Project metadata for reports |
| Project Name | `$PROJECT` | Project name for reports |

If not provided, use `${ARGUMENTS}_scos` as output and prompt for metadata before the first consumer.

## Prerequisites

### Skill Directory

`<SKILL_DIRECTORY>` is the **parent** `snowpark-connect/` directory containing `pyproject.toml` and `scripts/`. All tool invocations use `uv run --project <SKILL_DIRECTORY>`.

### uv Package Manager

Install `uv` if it is not already available. Show both OS variants — the skill
must work on macOS / Linux *and* Windows:

```bash
# macOS / Linux
uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
uv --version; if ($LASTEXITCODE -ne 0) { irm https://astral.sh/uv/install.ps1 | iex }
```

### Snowflake Connectivity (no Cortex LLM preflight)

No `CORTEX.COMPLETE` preflight is needed — matching the PySpark and Scala paths.
`analyze_java.py` makes **no** in-database LLM calls: it is deterministic plus RAG
*retrieval* only, and every ambiguous finding is deferred to the Phase 1.1b
adjudicator sub-agent. A Snowflake connection is still opened for the RAG backend,
so `--connection <name>` must resolve; with `--rag-backend trigger` (the default
for this skill) the offline `data/kb_rules.json` knowledgebase is used and no
Cortex Search service is required.

## The state model

A migration is a long, multi-phase run: **nothing durable lives in this
conversation**, because it gets summarized or truncated before the run ends. Nor
does it live in a checklist you keep in your head or in prose you write as you go —
those drift out of step with what actually happened on disk. State lives in one
structured, durable place: **`<CONVERSION>/migration_state.json`**.

**`phases_completed` *is* the progress — it is not a log of it.** The phase you are
on is simply the first row of the phase table whose state key is absent; the phases
that passed are the keys that are present. Read progress from the file rather than
tracking it separately, and never let a phase's real outcome and its recorded
outcome diverge — a phase that did not genuinely run must not be recorded as passed
(see *Phase Completeness*).

**After any compaction or context loss, rebuild from `migration_state.json` — never
from memory.** It carries the manifest, the paths, `coordinator_mode`,
`phases_completed`, and per-file `pending_files` / `processed_files` progress: enough
to resume exactly where the run stopped (see *Resumption*). You are also its **single
writer** — specialists return results for you to merge, so the file stays consistent
(see *Universal Gate Contract*).

## Progress UI Emit Hooks (Experimental)

These one-liners feed the live dashboard (the same server the PySpark path uses).
All are non-fatal (`|| true`) — migration never blocks on the UI.
`<SKILL_DIRECTORY>` is the parent `snowpark-connect/` directory;
`<CONVERSION>` is the resolved `Conversion-SCOS-<TIMESTAMP>` path.

```bash
BUS="python3 <SKILL_DIRECTORY>/../scripts/progress_bus.py"
RUN="<CONVERSION>"
CONNECTION_NAME="<the Snowflake connection resolved in Phase 0>"
```

> **⚠️ EXPERIMENTAL — the Progress UI is OFF by default. Never launch it automatically.**
> Start the dashboard **only** when the user has *explicitly* asked to see the
> Progress UI / live dashboard. If they have not explicitly asked, **skip the launch
> block below entirely** — no server, no browser window, no accidental launches. The
> event-emit commands are harmless no-ops when the server is not running, so continue
> to run them normally regardless.
>
> **Assessment-only flows skip this section entirely.** If this skill was loaded for
> assessment / readiness-report intent (stopping after Phase 1a), do not mention,
> offer, or launch the Progress UI — it only applies to full migrations.

**Only if the user explicitly requested the Progress UI**, launch the server once,
right after `$CONVERSION` is resolved. The launcher requires the
`--confirm-experimental` interlock (it is a no-op without it), is idempotent, and is
non-fatal (`|| true`).

```bash
UI="python3 <SKILL_DIRECTORY>/../scripts/ui_launch.py"
$UI start --run "$RUN" --skill-dir "<SKILL_DIRECTORY>/.." --port auto \
  --confirm-experimental \
  $([ "<config.ui_auto_open_browser>" = "yes" ] || echo "--no-browser") \
  2>/dev/null || true
```

Emit at each key boundary:

| When | Command |
|---|---|
| Phase 0 done / CONVERSION resolved | `$BUS run-init --run "$RUN" --path snowpark-connect --data "{\"project_name\":\"$PROJECT\",\"conversion_type\":\"snowpark-connect (java)\",\"total_files\":$TOTAL_FILES,\"connection_name\":\"$CONNECTION_NAME\"}" \|\| true` |
| Each phase starts | `$BUS phase-start --run "$RUN" --phase "<phase-name>" \|\| true` |
| Each phase ends | `$BUS phase-end   --run "$RUN" --phase "<phase-name>" \|\| true` |
| Fixer worker chunk assigned (Phase 2) | `$BUS agent-status --run "$RUN" --worker "fixer-$N" --status started  --phase migration --message "chunk $N/$TOTAL assigned" \|\| true` |
| Fixer worker starts a file | `$BUS file-progress --run "$RUN" --file "$REL_PATH" --status converted --worker "fixer-$N" --phase migration \|\| true` |
| Phase 2b compile passes for a file | `$BUS file-progress --run "$RUN" --file "$REL_PATH" --status verified --phase verification \|\| true` |
| `revert_failing_java_files.py` reverts | `$BUS file-progress --run "$RUN" --file "$REL_PATH" --status reverted --phase verification \|\| true` |
| Assessment report ready (Phase 1a) | `$BUS report-ready --run "$RUN" --file "$REPORTS_DIR/MigrationReadinessReport.html" --phase report-assessment \|\| true` |
| Each Reports/*.csv ready | `$BUS report-ready  --run "$RUN" --file "$REPORTS_DIR/Issues.csv" --phase reports \|\| true` |
| Migration summary done | `$BUS summary --run "$RUN" --data "{\"total_files\":$TOTAL_FILES,\"converted\":$CONVERTED,\"verified\":$VERIFIED}" \|\| true` |

Java phase-name mapping (use these exact names so the dashboard's stage tracker
lights up correctly): Phase 0.5 → `preprocessing`, Phase 0.6 → `sql-rewrite`,
Phase 1 → `analysis`, Phase 1a → `report-assessment`, Phase 2 → `migration`,
Phase 2b → `verification`, Phase 3 → `imports-headers`, Phase 4 → `reports`.

Emit `run-init` **once** when `$CONVERSION` is first resolved, immediately after
launching the server, using the manifest length as `total_files` and the Phase-0
connection as `connection_name`. The dashboard's **"Ask" chat** is answered by
`SNOWFLAKE.CORTEX.COMPLETE` over that same connection (read from the `run-init`
event), so including `connection_name` keeps chat on the Cortex-enabled connection
instead of falling back to `default`.

> **Report linking is automatic.** The server scans `Reports/` and links
> `MigrationReadinessReport.html`, `AssessmentIR.json`, and the dashboard CSVs as
> soon as they exist on disk, so they surface even if a `report-ready` hook is
> missed. The `report-ready` emits above are a redundant signal, not the only path.

## Workflow

You are a coordinator. You **NEVER** hand-write code fixes yourself — the judgment-heavy Phase 2 fixer and the Phase 1.1b adjudicators are delegated to specialist sub-agents via the `task()` tool. The deterministic phases (Phase 0.6 SQL rewrite, Phase 1 analysis, Phase 1a assessment render, Phase 3 imports/session/build/headers via `update_imports_java.py`, Phase 4 reports, Phase 4b feedback) and **all** phase verification run as scripts you invoke directly — no sub-agent, no tokens. (`verify_phase.py` replaced the former LLM critic agents; `update_imports_java.py` replaced the former import-updater specialist; Phases 1, 1a, and 4 call their generators directly because those generators are deterministic.) State is tracked in `migration_state.json`.

### Phase Playbooks

Every phase's step-by-step procedure lives in its own file under `phases/`.
**Read the playbook for the phase you are about to run, run it, then come back
here for the next one.** Do not preload them all — that is the whole point of
the split.

| # | Phase | Playbook | State key (`phases_completed`) | Must run |
|---|-------|----------|-------------------------------|----------|
| 0 | Collect info, create conversion folder | `phases/phase-0-setup.md` | — (writes state skeleton) | yes |
| 0.5 | Deterministic AST pre-processing (JavaParser) | `phases/phase-0.5-preprocess.md` | `0_5c_javaparser` **✓** | **MUST RUN** |
| 0.6 | Standalone SQL rewrite | `phases/phase-0.5-preprocess.md` | `0_6_sql_rewrite` | conditional |
| 1 | Analysis | `phases/phase-1-analysis.md` | `1_analysis` **✓** | yes |
| 1.1 | Adjudication | `phases/phase-1-analysis.md` | `1_5_adjudication` | default |
| 1a | Render assessment report | `phases/phase-1.2-assessment.md` | `1a_assessment_report` **✓** | yes |
| 2 | Apply fixes (parallel fixer pool) | `phases/phase-2-fixes.md` | `2_fixes` **✓** | yes |
| 2a | Coverage verification + deterministic fallback | `phases/phase-2-gates.md` | `2a_fallback` **✓** | **MUST RUN** |
| 2b | Compilation verification | `phases/phase-2-gates.md` | `2b_compilation` **✓** | **MUST RUN** |
| 2c | Evidence-based verification | `phases/phase-2-gates.md` | `2c_verification` **✓** | **MUST RUN** |
| 3 | Imports, session, build, headers | `phases/phase-3-imports.md` | `3_imports` **✓** | yes |
| 4 | Generate reports | `phases/phase-4-reports.md` | `4_reports` **✓** | yes |
| 4a | Post-run state validation | `phases/phase-4-reports.md` | `4a_validation` | **MUST RUN** |
| 4b | Migration feedback file | `phases/phase-4-reports.md` | — | non-fatal |
| 5 | Offer validation | `phases/phase-5-6-followups.md` | — | optional |
| 6 | Offer notebook conversion | `phases/phase-5-6-followups.md` | — | standalone only |

**✓ = one of the 9 keys `validate_migration_state.py --strict --language java` requires.**
Note the naming trap: **Phase 1a writes `1a_assessment_report`** (not `1_a_...`).
The state key — not the phase number — is the contract with the validator.

### Phase Completeness

Because playbooks load one at a time, **the phase table above is your only map** —
work down it in order and do not rely on remembering what comes next.

Before you report the migration complete, run the completeness check (Phase 4a).
It is the single authority on whether every phase was navigated:

```bash
python3 <SKILL_DIRECTORY>/scripts/validate_migration_state.py \
  --strict --language java \
  --state <CONVERSION>/migration_state.json
```

Exit `0` = all 9 required keys present. Exit `1` = at least one phase never ran.
**Never report success without a green run of this command** — it is the canonical
success criterion, and skipping it means nothing checked the pipeline end to end.

Two limits of this check you must compensate for by being honest, because it
cannot catch you:

- **A skip with a reason passes.** `{"status": "skipped", "skip_reason": "<any text>"}`
  is accepted as satisfied; only a *missing* key or a skip with *no* reason fails.
  So only ever record `skipped` when the phase genuinely could not run, and put the
  real cause in `skip_reason` — never to move past a phase that merely looked hard.
- **This validator is itself an optional phase** (`4a_validation`), so nothing forces
  you to run it. That is exactly why it is listed **MUST RUN** above.

Per-phase `verify_phase.py` checks are the complement: they inspect the code on disk,
so they catch a phase that *ran but did nothing* — state-key presence proves navigation;
the verifiers prove work.

### Universal Gate Contract

Every deterministic gate (`scripts/verify_phase.py` and `scripts/scos_gates.py`) uses
the **same behavioral protocol**. Read the verdict from **stdout** — never rely on a
non-portable `$?` capture.

**`verify_phase.py` exit codes (phases 1, 2, 3, 4):**

| Exit | Verdict | Action |
|------|---------|--------|
| `0` | `PASS` / `PASS_WITH_GAPS` | Advance. `PASS_WITH_GAPS` carries advisory `WARN` findings — record them, do not block. |
| `1` | `FAIL` | **Gap-scoped re-dispatch**: pass the listed failing checks as targeted feedback and fix *only* those, leaving already-resolved code untouched. Re-run the verifier. Never re-run a whole file statelessly; that regresses correct work. |

**`scos_gates.py` exit codes (Phase 1a assessment):**

| Exit | Verdict | Action |
|------|---------|--------|
| `0` | `PASS` / `PASS_WITH_GAPS` | Advance. |
| `2` | `FAIL` | Gap-scoped re-dispatch (same as above). |
| `3` | IO / usage error | **STOP and escalate.** A missing state file or bad path will not be fixed by re-running the specialist. |

**On every FAIL, read the gate output carefully before acting.** Some failure codes
are **not fixable by a specialist at all**: `phase2_not_orchestrated` means *you*
skipped a coordinator step; `manifest_file_missing` is a coverage problem; a
pre-existing compile failure in the customer's source must not trigger a fixer
re-dispatch. Check what the gate is reporting before dispatching anything.

**Empty stdout means the gate did not run — it is NOT a FAIL.** A malformed command
line exits non-zero with empty stdout because `argparse` exits 2 and does not honour
the `3` reserved above. If stdout is empty or the verdict is missing, **fix your own
invocation and re-run the gate. Do NOT re-dispatch a specialist.**

Default retry budget is **2** attempts on failure, then escalate to the user.
Phase 1a allows **3**.

On every advance, record the outcome under `migration_state.json :: phases_completed`
with `status`, the verifier/gate name, and `attempts`; on an unrunnable phase record
`{"status": "skipped", "skip_reason": "<one-line reason>"}`. **You (the coordinator) are
the single writer of `migration_state.json`** — specialists return results for you to
merge.

After each phase, git checkpoint inside `<CONVERSION>`; the playbooks give the exact
commit messages and tags (`phase-0-source`, `phase-1-complete`, …), which the gates
depend on for baselines.

### Specialists and References

| File | Role |
|------|------|
| `agents/analyzer.md` | Phase 1 analysis procedure (reference) |
| `agents/adjudicator.md` | Phase 1.1b adjudication |
| `agents/reporter.md` | Phase 1a + Phase 4 report rendering (reference) |
| `agents/fixer.md` | Phase 2 code fixing (incl. targeted re-fix mode) |
| `references/fix-rules.md` | Java fix rules |
| `<SKILL_DIRECTORY>/references/sql/sql-fix-rules.md` | SQL fix rules |
| `<SKILL_DIRECTORY>/references/java/rdd-conversion.md` | Holistic RDD chain conversion |

### Resumption

If context is lost mid-migration, read `migration_state.json` to determine the last completed phase and resume from the next one. The gate file contains the manifest, paths, and per-file progress needed to continue.

## Stopping Points

- Phase 0: After collecting project info — confirm settings before starting
- Phase 2: If `verify_phase.py --phase 2` fails after 2 retries — escalate to user with specific errors
- Phase 5: After migration completes — ask user about validation

## Success Criteria

- `scripts/validate_migration_state.py --strict --language java` exits 0 (canonical check — see Phase 4a)
- `migration_state.json` shows all phases completed and verified
- `Reports/Issues.csv` exists with data rows
- `Reports/InputFilesInventory.csv` code-row count matches the manifest
- `Reports/MigrationReadinessReport.html` and `Reports/AssessmentIR.json` exist
- All `.java` files pass the Phase 2b compile gate — `javac -proc:none` (when JDK available), else tokenizer fallback; mode recorded in `phases_completed.2b_compilation.compile_mode`
- Phase 2c evidence-based verification passes with `disagreements = 0`
- Every `.java` file has a migration header block comment
- Build files actively transformed (Java 11+, Spark 3.5+, `com.snowflake:snowpark-connect-java-client` added)
- File count matches between original and migrated directories

## Output

```
<output_root>/
  Conversion-SCOS-<timestamp>/                       ← <CONVERSION>
    Output/                                          ← <MIGRATED> — converted files
    Reports/
      Issues.csv                                     ← EWI issues (SPRKCNTSCL*)
      InputFilesInventory.csv                        ← Source file inventory
      ArtifactDependencyInventory.csv                ← Import dependencies
      MigrationReadinessReport.html                  ← Stakeholder-facing readiness report
      AssessmentIR.json                              ← Structured IR (stable contract)
    Logs/                                            ← Migration log
    migration_state.json                             ← Phase gate tracking
    analysis.json                                    ← Compatibility analysis
```
