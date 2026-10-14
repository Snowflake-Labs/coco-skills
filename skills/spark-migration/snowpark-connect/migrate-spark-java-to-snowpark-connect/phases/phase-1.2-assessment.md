# Phase 1a: Render Assessment Report

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 1a: Render Assessment Report

**Render directly (no specialist agent)** — `render_assessment.py` is deterministic. This produces a **pre-migration** readiness view from the Phase 1 `analysis.json` and the **original source**.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --language java --project "<project>" \
  --analysis-json <CONVERSION>/analysis.json \
  --workload-dir <original_source_path> \
  --output-html <CONVERSION>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION>/Reports/AssessmentIR.json
```

**Quality gate**:
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/scos_gates.py reports --section assessment \
  --state <CONVERSION>/migration_state.json --json
```

- Exit `0` → advance and record `"1a_assessment_report": {"status": "passed", ...}`
- Exit `2` (`FAIL`) → re-render, retry at most 3 times. If still failing, STOP and escalate.
- Exit `3` (IO error) → STOP and escalate immediately.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 1a: assessment report rendered"`
