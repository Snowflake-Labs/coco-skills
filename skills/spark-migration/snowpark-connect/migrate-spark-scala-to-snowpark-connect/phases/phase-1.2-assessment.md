# Phase 1a: Render Assessment Report

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 1a: Render Assessment Report

**Render directly (no specialist agent)** — `render_assessment.py` is deterministic, so the coordinator runs it itself. This produces a **pre-migration** readiness view for stakeholders from the Phase 1 `analysis.json` and the **original source** passed via `--workload-dir` (the user's untouched Spark code — not `<MIGRATED>`, which Phase 0.5 has already rewritten). "Before any fixes" means before the LLM fixer in Phase 2 (the same generator Phase 4 used to call, now rendered early to match the PySpark flow).

1. **Metadata:** `project` was collected in Phase 0 (`migration_state.json :: metadata`). Prompt the user only if it is missing.

2. **Readiness HTML + IR** (from the existing `analysis.json`; `--language scala`):
   ```bash
   uv run --project <SKILL_DIRECTORY> \
     python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
     --language scala --project "<project>" \
     --analysis-json <CONVERSION>/analysis.json \
     --workload-dir <original_source_path> \
     --output-html <CONVERSION>/Reports/MigrationReadinessReport.html \
     --dump-ir <CONVERSION>/Reports/AssessmentIR.json
   ```
   If `<original_source_path>` is unavailable, fall back to `--workload-dir <MIGRATED>`.

**Quality gate**: run the assessment-report gate (a deterministic, language-agnostic script):

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
- Exit `2` (`FAIL`) → re-run `render_assessment.py` with the gate's `gaps` array as feedback, then re-run the gate. Retry at most **3 times total**. If it still fails, **STOP and escalate to the user** — do NOT advance to Phase 2 with a missing or broken report. Record:
  ```json
  "phases_completed": {"1a_assessment_report": {"status": "skipped", "attempts": 3, "skip_reason": "<one-line reason>"}}
  ```
- Exit `3` (IO / usage error) → the gate could not read `migration_state.json` or the `Reports/` paths; re-rendering will NOT fix this. STOP and escalate immediately.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 1a: assessment report rendered"`
