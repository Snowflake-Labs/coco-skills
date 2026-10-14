# Phase 1.2: Render Assessment Report

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

**Run mode (size-aware)**: if `coordinator_mode == false`, run this **inline** by reading `../agents/reporter.md` and following **Section A (Assessment Report) only** yourself; if `coordinator_mode == true`, **spawn a `task()` sub-agent** with `../agents/reporter.md` to run Section A, so the `AssessmentIR.json` read stays out of your window. Either way it renders `Reports/MigrationReadinessReport.html` + `Reports/AssessmentIR.json` from the Phase 1 `analysis.json` and a deterministic scan of the **pre-Phase-0.5** source (materialized from the `phase-0-source` git tag), producing a pre-migration readiness view for stakeholders. The reporter passes `--migration-state-json <CONVERSION>/migration_state.json` so the renderer materializes the original source itself and populates the standalone "Phase 0.5 auto-resolved" panel from `migration_state.json[recipe_edits]` — analyzer findings retain their post-Phase-0.5 risk math but their line numbers and code snippets are rebased back onto the original source.

**Talking about results in chat:** describe migration effort with the code-churn **categories** — Ready / Light Refactor / Active Refactor — and the per-bucket file counts. **Never quote a numeric "readiness score" or percentage**; the assessment is deliberately category-based (the old 0-100 score was nondeterministic analyzer confidence and is gone).

**Quality gate**: run the assessment-report gate (a deterministic script):

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/scos_gates.py reports --section assessment \
  --state <CONVERSION>/migration_state.json --json
```

The gate confirms `Reports/MigrationReadinessReport.html` and `Reports/AssessmentIR.json` exist and that the HTML has no unsubstituted Jinja placeholders (`{{` / `{%`). Read the verdict from stdout (do not rely on a non-portable `$?` capture).

**Gate (bounded retry, then hard fail)**:
- Exit `0` → advance and record:
  ```json
  "phases_completed": {"1a_assessment_report": {"status": "passed", "gate": "scos_gates.reports:assessment", "attempts": <n>}}
  ```
- Exit `2` (`FAIL`) → re-run Section A the same way you ran it (inline, or by re-dispatching the `../agents/reporter.md` Section A sub-agent in multi-file mode) with the gate's `gaps` array as feedback, then re-run the gate. Retry at most **3 times total**. If it still fails, **STOP and escalate to the user** — do NOT advance to Phase 2 with a missing or broken report. Record:
  ```json
  "phases_completed": {"1a_assessment_report": {"status": "skipped", "attempts": 3, "skip_reason": "<one-line reason>"}}
  ```
- Exit `3` (IO / usage error) → the gate could not read `migration_state.json` or the `Reports/` paths; re-running the reporter will NOT fix this. STOP and escalate immediately.

**Phase 1.3: Data-edge enrichment (LLM fallback) — explicit user decision.** Run this step **inline yourself (the main loop), never in a sub-agent** — it requires user interaction, and a spawned `task()` sub-agent cannot prompt. This runs regardless of `coordinator_mode`. It is **optional** (the user may decline) and so is not part of the required-phase set the final verification gate checks.

**Re-run guard (check first).** If `migration_state.json :: phases_completed.1b_data_edge_resolution` already exists with a terminal status (`passed`, `warned`, `not_needed`, or `skipped`), the decision was already made for this conversion — **do not re-prompt or re-run** (a fresh resolution pass is expensive and the IR already carries the prior result). Skip straight to the Git checkpoint. Only re-run if the user *explicitly* asks to redo enrichment.

The seed report's data dependency graph is only as complete as the static AST scanner could make it. Read the two incompleteness counts from `Reports/AssessmentIR.json` **without pulling the whole IR into context** — extract just the lengths:

```bash
python3 -c "import json,sys; d=json.load(open('<CONVERSION>/Reports/AssessmentIR.json')); print(len(d.get('unresolved_data_edges') or []), len(d.get('unresolved_dynamic_imports') or []))"
```

The two numbers are `N` (unresolved read/write call sites the scanner could not statically resolve) and `M` (dynamic import / dispatch sites it could not resolve).

- **If `N + M == 0`**: the static DAG has no unresolved gaps. Tell the user in one line that LLM enrichment is still available (it can also surface I/O the scanner never recognises — boto3, SQL template files, `dbutils.taskValues` handoffs) but is optional, then proceed **without stopping**. Record `"1b_data_edge_resolution": {"status": "not_needed"}`.
- **If `N + M > 0`**: **STOP and present this to the user** (fill in the counts), then wait for a Y/n answer before doing anything else:

  > ⚠️ **The data dependency graph is incomplete.** Static analysis left **{N} unresolved read/write edge(s)** and **{M} unresolved dynamic import(s)** — the DAG and data-lineage in the report may have blind spots at those sites.
  >
  > I can run an **LLM data-edge enrichment** pass that reads every `.py` / `.sql` / `.ipynb` file in the workload, traces the dynamic paths the scanner couldn't, resolves the dynamic imports, and discovers out-of-scope I/O (boto3, SQL template files, task-value handoffs). It needs **no Snowflake connection** and its results are cached in `AssessmentIR.json`, so the re-render is free.
  >
  > ⏱️ It reads the entire workload, so it typically takes **5+ minutes and scales with workload size** — larger workloads take longer.
  >
  > **Run LLM data-edge enrichment now? (Y/n)** — if you skip, the report ships with the static-only DAG (clearly labeled as such), and you can run enrichment later with `render_assessment.py --llm-resolved-edges --dump-ir <CONVERSION>/Reports/AssessmentIR.json` (the `--dump-ir` path is required — without it the flag loads nothing and silently produces a static-only report).

  - **Yes** → follow `../agents/reporter.md` Section A **Step 1b** (the resolver loop → gate → final `--llm-resolved-edges` render). The whole-workload file reading may be dispatched to a `task()` sub-agent on multi-file workloads, but the decision prompt above stays with you.
  - **No** → keep the static report; record `"1b_data_edge_resolution": {"status": "skipped"}` and proceed.

**After enrichment runs, report the outcome to the user (Yes path only).** Summarize the result (keeps the full IR out of context):

```bash
python3 <SKILL_DIRECTORY>/scripts/assessment/summarize_llm_resolution.py <CONVERSION>/Reports/AssessmentIR.json
```

The resolver stamped each confirmed-unresolvable site with a `severity`; the summary groups by it. Relay:

- **`clean: true`** (no `critical`) → one line: enrichment resolved all `baseline_unresolved_edges` read/write edge(s) and `baseline_unresolved_imports` import(s) the static scanner left open (add `newly_discovered` if > 0). If `informational` is non-empty, add one line noting those are runtime-only endpoints (config-driven paths etc.) that migrate fine but can't be drawn as exact lineage.
- **`clean: false`** → lead with ⚠️: for each `critical` item name the `file` and its `why` — a **missing input** (a file/module/caller/table absent from the export) that may block migration; these are also in the report's data-lineage section. Then note `informational` blind spots and the `benign_count` dead ends in one line each. Advisory only; never block — the report always ships with whatever was resolved.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 1: analysis complete + assessment report rendered" && git tag phase-1-complete`
