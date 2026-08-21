---
name: migrate-pyspark-to-snowpark-connect
description: |
  Migrate PySpark and Databricks workloads to Snowflake SCOS (Snowpark Connect for Spark).
  Use when: converting Spark code to run on Snowflake, analyzing PySpark compatibility,
  updating imports to Spark Connect equivalents, or migrating from Databricks.
  Generates SCOS-compatible reports (Issues.csv, InputFilesInventory.csv, ArtifactDependencyInventory.csv)
  for the dvp-scos-dashboard-generator using official SCOS EWI codes (SPRKCNTPY*).
  Triggers: migrate pyspark, convert spark, scos migration,
  spark connect, pyspark compatibility, snowpark connect.
parent_skill: snowpark-connect
allowed-tools: Read, Write, Bash, Task
---

# Migrate PySpark to SCOS — Coordinator

Orchestrate a multi-phase migration of PySpark workloads to Snowflake SCOS (Snowpark Connect for Spark). This coordinator delegates **code-fixing (Phase 2)** to a parallel pool of specialist sub-agents; runs the mechanical phases as scripts directly; and runs analysis + report-rendering (Phases 1, 1.2, 4) **inline for single-file workloads or as sub-agents for multi-file workloads** (sized by `coordinator_mode`). Every phase is validated with deterministic quality gates (`scripts/scos_gates.py`) before advancing.

## When to Load

[snowpark-connect] Intent Detection: After user indicates migration intent (convert, migrate, update imports, rewrite for SCOS).

## Arguments

- `$ARGUMENTS` — Path to the PySpark file or directory to migrate

### Optional Metadata (from orchestrator)

| Parameter | Variable | Description |
|-----------|----------|-------------|
| Output path | `$OUTPUT` | Target directory for migrated files and Reports/ |
| Customer Email | `$EMAIL` | Project metadata for reports |
| Customer Company | `$COMPANY` | Project metadata for reports |
| Project Name | `$PROJECT` | Project name for reports |
If not provided, use `${ARGUMENTS}_scos` as output and prompt for metadata in Phase 4.

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

## Workflow

You are a coordinator. You **NEVER** hand-edit code fixes yourself, and the migrated reports are always produced by their generator scripts (never hand-written). **Code fixing (Phase 2) is always delegated to specialist sub-agents** via the `task()` tool — it fans out across files in a parallel worker pool and is by far the largest consumer of context, so isolating each fixer in its own sub-agent keeps the coordinator's window lean and lets waves run concurrently.

The **analysis (Phase 1)** and **report-rendering (Phases 1.2 and 4)** phases are run **one of two ways depending on workload size**, controlled by `coordinator_mode` (set in Phase 0: `false` when `manifest` holds a single file, `true` for multi-file workloads):

- **`coordinator_mode == false` (single-file / small) → run them inline yourself, no sub-agent.** Each is a deterministic script wrapped in at most light bounded judgment (a blind-spot supplementary scan for Phase 1, four advisory narrative sentences for Phase 1.2, nothing for Phase 4), and on a one-file workload the inlined reads are tiny — a sub-agent would buy only cold-start latency and context churn. For the Phase 1 supplementary scan, prefer `grep`/`Bash` over `Read` so the source stays out of your window.
- **`coordinator_mode == true` (multi-file) → spawn a `task()` sub-agent for each**, exactly as for the fixer. Here the supplementary scan reads many source files and `analysis.json` / `AssessmentIR.json` grow with the workload; doing that inline would accumulate in the coordinator's single session and push toward `context_budget_tokens`. The throwaway sub-agent absorbs those reads and returns only a compact summary, preserving context isolation.

In both cases `agents/analyzer.md` and `agents/reporter.md` are the canonical step-by-step procedures — read and follow them (inline, or as the sub-agent's prompt context); the procedure is identical, only *who* runs it changes. The mechanical phases (pre-processing, SQL rewrite, import/header updates, coverage, verification, validation) are always deterministic scripts you invoke directly. Every phase is validated with a deterministic quality gate (`scripts/scos_gates.py` or a phase-specific validator); the gates are the real enforcement and run identically regardless of who performed the phase.

## Progress UI Emit Hooks (Experimental)

These one-liners feed the live dashboard. All are non-fatal (`|| true`).
`<SKILL_DIRECTORY>` is the parent `snowpark-connect/` directory.
`<CONVERSION>` is the resolved `Conversion-SCOS-<TIMESTAMP>` path.

```bash
BUS="python3 <SKILL_DIRECTORY>/../scripts/progress_bus.py"
RUN="<CONVERSION>"
CONNECTION_NAME="<the Snowflake connection resolved in Phase 0>"
```

> **⚠️ EXPERIMENTAL — the Progress UI is OFF by default. Never launch it automatically.**
> Start the dashboard **only** when the user has *explicitly* asked to see the
> Progress UI / live dashboard (e.g. "show the progress UI", "open the migration
> dashboard"). If they have not explicitly asked, **skip the launch block below
> entirely** — do not start the server and do not open a browser window. There
> must be no accidental launches. The event-emit commands elsewhere in this
> section are harmless no-ops when the server is not running, so continue to run
> them normally regardless.

**Only if the user explicitly requested the Progress UI**, launch the server once,
right after `$CONVERSION` is resolved (this is the *only* place it is ever started;
the top-level router and the SMA / `snowpark-api` path never launch it). `--run "$RUN"`
must be the resolved `<CONVERSION>` dir so the server tails the same event log these
hooks write to. The launcher requires the `--confirm-experimental` interlock (it is a
no-op without it), is idempotent (reuses a live server), and is non-fatal (`|| true`) —
migration never blocks on it.

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
| Phase 0 done / CONVERSION resolved | `$BUS run-init --run "$RUN" --path snowpark-connect --data "{\"project_name\":\"$PROJECT\",\"conversion_type\":\"snowpark-connect\",\"total_files\":$TOTAL_FILES,\"connection_name\":\"$CONNECTION_NAME\"}" \|\| true` |
| Each phase starts | `$BUS phase-start --run "$RUN" --phase "<phase-name>" \|\| true` |
| Each phase ends | `$BUS phase-end   --run "$RUN" --phase "<phase-name>" \|\| true` |
| Fixer worker chunk assigned (Phase 2) | `$BUS agent-status --run "$RUN" --worker "fixer-$N" --status started  --phase migration --message "chunk $N/$TOTAL assigned" \|\| true` |
| Fixer worker starts a file | `$BUS file-progress --run "$RUN" --file "$REL_PATH" --status converted --worker "fixer-$N" --phase migration \|\| true` |
| verify_migration.py pass | `$BUS file-progress --run "$RUN" --file "$REL_PATH" --status verified --phase verification \|\| true` |
| revert_failing_files.py reverts | `$BUS file-progress --run "$RUN" --file "$REL_PATH" --status reverted --phase verification \|\| true` |
| Assessment report ready (Phase 1.2) | `$BUS report-ready --run "$RUN" --file "$REPORTS_DIR/MigrationReadinessReport.html" --phase report-assessment \|\| true` |
| Each Reports/*.csv ready | `$BUS report-ready  --run "$RUN" --file "$REPORTS_DIR/Issues.csv" --phase reports \|\| true` |
| Migration summary done | `$BUS summary --run "$RUN" --data "{\"total_files\":$TOTAL_FILES,\"converted\":$CONVERTED,\"verified\":$VERIFIED}" \|\| true` |

Phase names to use: `assessment`, `preprocessing`, `sql-rewrite`, `analysis`,
`report-assessment`, `migration`, `imports-headers`, `reports`, `verification`,
`validation`, `notebook-migration`.

Emit `run-init` **once** when `$CONVERSION` is first resolved (end of Phase 0
step 2), immediately after launching the server above, using the `manifest`
length as `total_files` and the Phase-0 connection as `connection_name`.
Because the server is started on `$RUN` (the resolved `<CONVERSION>` dir), no
temp-sentinel re-init is needed.

The dashboard also includes an **"Ask" chat** for stakeholders to ask about live
progress or the SCOS process. It is answered by `SNOWFLAKE.CORTEX.COMPLETE` over
the **same Snowflake connection** this run uses — the server reads
`connection_name` from the `run-init` event, so including it above is what keeps
chat on the Cortex-enabled connection (the Phase-0 Cortex preflight already
guarantees access) rather than falling back to `default`. Chat is read-only, no
extra emissions required. If the connection can't be opened the chat shows a
friendly error and the migration is unaffected.

> **Report linking is automatic.** The server also scans the `Reports/` folder
> and links `MigrationReadinessReport.html`, `AssessmentIR.json`, and the
> dashboard CSVs the moment they exist on disk, so they appear in the dashboard
> even if a `report-ready` hook is missed. The `report-ready` emits above are a
> belt-and-suspenders signal, not the only path.

### Startup — Resume from Existing Assessment

Before reading any phase playbook, probe the output root for a completed assessment.
This is deterministic: no handoff parameter is needed — the coordinator discovers state from disk.

Derive `<OUTPUT_ROOT>` as `$OUTPUT` if supplied, otherwise `${ARGUMENTS}_scos`. Then run:

```bash
python3 -c "
import json, os, glob, sys
root = sys.argv[1]
found = []
for p in glob.glob(os.path.join(root, 'Conversion-SCOS-*', 'migration_state.json')):
    try:
        with open(p) as f:
            s = json.load(f)
        status = s.get('phases_completed', {}).get('1a_assessment_report', {}).get('status')
        if status == 'passed':
            found.append(os.path.dirname(p))
    except (json.JSONDecodeError, OSError):
        pass
found.sort(key=lambda d: os.path.getmtime(os.path.join(d, 'migration_state.json')))
print(found[-1] if found else '')
" "<OUTPUT_ROOT>"
```

| Output | Action |
|--------|--------|
| Non-empty path | Assessment-complete workspace found. Set `<CONVERSION>` to that path. Read `migration_state.json` to restore `<SKILL_DIRECTORY>`, `coordinator_mode`, `manifest`, and `metadata`. **Ask the user before proceeding:** "Found existing assessment at `<CONVERSION>` — continue with migration from Phase 2? (Y/n)". **Yes** → skip phases 0, 0.5, 0.6, 1, 1.1, 1.2, and 1.3; jump directly to Phase 2. **No** → start fresh from Phase 0. |
| Empty string | No completed assessment found. Check for any `Conversion-SCOS-*` with a partial run (first key absent from `phases_completed`) and resume from there; otherwise start fresh from Phase 0. |

### Phase Playbooks

Every phase's step-by-step procedure lives in its own file under `phases/`.
**Read the playbook for the phase you are about to run, run it, then come back
here for the next one.** Do not preload them all — that is the whole point of
the split.

| # | Phase | Playbook | State key (`phases_completed`) | Must run |
|---|-------|----------|-------------------------------|----------|
| 0 | Collect info, create conversion folder | `phases/phase-0-setup.md` | — (writes state skeleton) | yes |
| 0.5 | Deterministic pre-processing (LibCST recipes) | `phases/phase-0.5-preprocess.md` | `0_5_preprocess` **✓** | **MUST RUN** |
| 0.6 | Standalone SQL rewrite | `phases/phase-0.5-preprocess.md` | `0_6_sql_rewrite` | **MUST RUN** |
| 1 | Analysis | `phases/phase-1-analysis.md` | `1_analysis` **✓** | yes |
| 1.1 | Adjudication | `phases/phase-1-analysis.md` | — | default |
| 1.2 | Render assessment report | `phases/phase-1.2-assessment.md` | `1a_assessment_report` **✓** | yes |
| 1.3 | Data-edge enrichment (LLM fallback) | `phases/phase-1.2-assessment.md` | `1b_data_edge_resolution` | optional |
| 2 | Apply fixes (parallel fixer pool) | `phases/phase-2-fixes.md` | `2_fixes` **✓** | yes |
| 2a | Coverage verification | `phases/phase-2-gates.md` | `2a_coverage` **✓** | **MUST RUN** |
| 2b | Compilation verification | `phases/phase-2-gates.md` | `2b_compilation` **✓** | **MUST RUN** |
| 2c | Evidence-based verification | `phases/phase-2-gates.md` | `2c_verification` **✓** | **MUST RUN** (once) |
| 3 | Imports and headers | `phases/phase-3-imports.md` | `3_imports` **✓** | yes |
| 4 | Generate reports | `phases/phase-4-reports.md` | `4_reports` **✓** | yes |
| 4a | Post-run state validation | `phases/phase-4-reports.md` | `4a_validation` | **MUST RUN** |
| 4b | Migration feedback file | `phases/phase-4-reports.md` | — | non-fatal |
| 5 | Offer validation | `phases/phase-5-6-followups.md` | — | optional |
| 6 | Offer notebook conversion | `phases/phase-5-6-followups.md` | — | standalone only |

**✓ = one of the 9 keys `validate_migration_state.py --strict` requires for Python.**
Note the naming trap: **Phase 1.2 writes `1a_assessment_report`** (not `1_2_...`).
The state key — not the phase number — is the contract with the validator.

### Phase Completeness

Because playbooks load one at a time, **the phase table above is your only map** —
work down it in order and do not rely on remembering what comes next.

Before you report the migration complete, run the completeness check (Phase 4a).
It is the single authority on whether every phase was navigated:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/validate_migration_state.py \
  --state <CONVERSION>/migration_state.json --strict --json
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

Per-phase `scos_gates.py` checks are the complement: they inspect the code on disk,
so they catch a phase that *ran but did nothing* (e.g. `unsupported_construct_live`,
`fixed_but_unchanged`, `phase2_not_orchestrated`) — see `references/gate-findings.md`.
State-key presence proves navigation; the gates prove work.


### Universal Gate Contract

Every deterministic gate (`scripts/scos_gates.py <section>` and the phase-specific
validators) uses the **same exit-code protocol**. Read the verdict from **stdout** —
never rely on a non-portable `$?` capture. A phase playbook only restates this when
its retry budget or recovery action differs.

| Exit | Verdict | Action |
|------|---------|--------|
| `0` | `PASS` / `PASS_WITH_GAPS` | Advance. `PASS_WITH_GAPS` carries advisory `WARN` findings only — record them, do not block. |
| `2` | `FAIL` | **Look up every `gaps[].code` in `references/gate-findings.md` first** (mandatory — see below), then **gap-scoped re-dispatch**: pass the gate's `gaps` array verbatim as `TARGET_ISSUES` and fix *only* those, leaving already-resolved lines untouched. Re-run the gate. Never re-run a whole file statelessly; that regresses correct work. |
| `3` | IO / usage error | **STOP and escalate.** A missing state file or bad path will not be fixed by re-running the specialist. |

**On every FAIL, read `references/gate-findings.md` before you act.** This is a
required step, not background reading. The gates emit **35 distinct codes** and the
code — not the message text — determines what to do. Crucially, several codes are
**not fixable by a specialist at all**: `phase2_not_orchestrated` and
`manifest_file_missing` mean *you* skipped a coordinator step, `preexisting_syntax`
is a defect in the customer's source, and `sql_mechanical_not_rewritten` means Phase
0.6 needs re-running. Re-dispatching a fixer for any of those spends a full pass
achieving nothing and risks regressing correct work. The reference gives an **Owner**
per code — `fixer` / `analyzer` / `reporter` / `coordinator` / `source` — so check the
owner before dispatching anything.

It also records the one known **unconvergeable** case: `convertible_not_converted`
fires on external-storage I/O whose repoint needs a customer stage name no agent can
invent. Never relabel a finding to silence a gate — leave the honest TODO and escalate.

**Empty stdout means the gate did not run — it is NOT a FAIL.** A malformed command
line (missing or invalid `--section`, unknown flag) exits **`2` with empty stdout**,
because `argparse` uses exit 2 and does not honour the `3` reserved above. So exit `2`
is ambiguous: it means FAIL *only* when stdout carries a verdict.

Before acting on any exit `2`, check that stdout parsed and `gaps` is non-empty. If
stdout is empty, or the verdict is missing, or `gaps` is empty — **fix your own
invocation and re-run the gate. Do NOT re-dispatch a specialist.** Re-dispatching here
spends a full fixer/reporter pass on a failure that does not exist, and with no `gaps`
there is nothing to target, so both retries burn and the gate still never ran.
Never record a phase as passed off a gate whose stdout was empty — that phase is
unverified, and Phase 4a cannot catch it because it only checks key presence.

Default retry budget is **2** attempts on exit `2`, then escalate to the user.
Phase 1.2 allows **3**. Phase 2b iterates up to **3**.

On every advance, record the outcome under `migration_state.json :: phases_completed`
with `status`, the `gate` name, and `attempts`; on an unrunnable phase record
`{"status": "skipped", "skip_reason": "<one-line reason>"}`. **You (the coordinator) are
the single writer of `migration_state.json`** — specialists return results for you to
merge (see `phases/phase-2-fixes.md` → state-write ownership).

After each phase, git checkpoint inside `<CONVERSION>`; the playbooks give the exact
commit messages and tags (`phase-0-source`, `phase-1-complete`, …), which the gates
depend on for baselines.

### Specialists and References

| File | Role |
|------|------|
| `agents/analyzer.md` | Phase 1 analysis procedure |
| `agents/adjudicator.md` | Phase 1.1b adjudication |
| `agents/reporter.md` | Phase 1.2 (Section A) + Phase 4 report rendering |
| `agents/data_edge_resolver.md` | Phase 1.3 data-edge enrichment |
| `agents/fixer.md` | Phase 2 code fixing (incl. targeted re-fix mode) |
| `references/fix-rules.md` | Python fix rules |
| `references/sql-fix-rules.md` | SQL fix rules |
| `references/gate-findings.md` | Gate finding codes → meaning + **owner**. **Required reading on any gate FAIL** |
| `<SKILL_DIRECTORY>/references/python/rdd-conversion.md` | Holistic RDD chain conversion (shared, parent dir) |

### Resumption

Both cases — continuing from a completed assessment and recovering from mid-migration context loss — are handled by the "Startup — Resume from Existing Assessment" probe above. It scans the output root on every startup, so no explicit handoff parameter is required in either case. Read `migration_state.json` from the discovered path to restore all coordinator state (`manifest`, `paths`, `coordinator_mode`, `pending_files`, `processed_files`).

## Stopping Points

- Phase 0: After collecting project info — confirm settings before starting
- Phase 2: If the fixer gate fails after 2 retries — escalate to user with specific errors
- Phase 5: After migration completes — ask user about validation

## Success Criteria

- `scripts/validate_migration_state.py --strict` exits 0 (this is the
  canonical, machine-checkable success criterion — see Phase 4a)
- `migration_state.json` shows all phases 1-4 completed with gate approval
- `Reports/Issues.csv` exists with data rows
- `Reports/InputFilesInventory.csv` row count matches manifest
- `Reports/MigrationReadinessReport.html` and `Reports/AssessmentIR.json` exist
- All `.py` files pass `py_compile` syntax check
- Every `.py` file has a migration header docstring
- File count matches between original and migrated directories

## Output

```
<output_root>/
  Conversion-SCOS-<timestamp>/                       ← <CONVERSION>
    Output/                                          ← <MIGRATED> — converted files
    Reports/
      Issues.csv                                     ← EWI issues (SPRKCNTPY*)
      InputFilesInventory.csv                        ← Source file inventory
      ArtifactDependencyInventory.csv                ← Import dependencies
      MigrationReadinessReport.html                  ← Stakeholder-facing readiness report
      AssessmentIR.json                              ← Structured IR (stable contract)
    Logs/                                            ← Migration log
    migration_state.json                             ← Phase gate tracking
    analysis.json                                    ← Compatibility analysis
```
