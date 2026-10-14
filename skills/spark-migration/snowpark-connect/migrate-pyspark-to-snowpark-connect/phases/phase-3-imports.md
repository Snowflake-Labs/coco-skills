# Phase 3: Imports and Headers

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

<!-- Deterministic: replaces the former agents/import-updater.md LLM specialist (removed),
     mirroring how scos_gates.py replaced the LLM critic agents. Updating imports
     and stamping a header is a mechanical (replace/prepend) step, so it runs as a
     reproducible script the coordinator invokes directly — no sub-agent dispatch. -->
**Run the deterministic import updater** — it processes **every** manifest file
(`.py` and notebooks): replaces `SparkSession.builder...getOrCreate()` (and the
`DatabricksSession` variant) with `snowpark_connect.init_spark_session()` and
inserts `from snowflake import snowpark_connect`, comments out unsupported
`databricks` / `delta` imports (standard `pyspark` imports are kept), prepends a
SCOS migration-header docstring, and records `phases_completed["3_imports"]`.
`.config(...)` calls in builder chains are preserved via the shared LibCST recipe
(no timezone-drop). The transform is idempotent — re-running it is a safe no-op.

**This script is the sole author of the rich migration header.** It builds the
header's `Changes Overview` / `Known Limitations` from the `# SCOS:` annotations
in each file, so it MUST run on every workload. Do not hand-write headers and do
not let the report generator's placeholder stand in for it. If a file already
carries the *placeholder* stub (`Deterministic header added by report generator`,
stamped by `generate_scos_reports.py` only when this phase was skipped),
`update_imports.py` strips and replaces it with the real header.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/update_imports.py \
  --state <CONVERSION>/migration_state.json
echo "update_imports_exit=$?"
```

**Quality gate**: run the imports gate (a deterministic script):

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/scos_gates.py imports \
  --state <CONVERSION>/migration_state.json --json
```

The gate verifies every manifest `.py` has a migration header in its first 15 lines, has no `SparkSession.builder` left in live (non-comment, non-docstring) code, has no unsupported imports (`databricks`, `delta.tables`), and that at least one file references `snowpark_connect`. It also FAILs (`stub_header`) if a file carries the report-generator placeholder header instead of a real one — proof this phase was skipped. Read the verdict from stdout.

**Gate**:
- Exit `0` → advance and update `migration_state.json` phase to 3.
- Exit `2` (`FAIL`) → the deterministic updater should already satisfy every
  check. A `stub_header` finding means `update_imports.py` never ran for that
  file — **run it now** (it strips the placeholder and writes the real header),
  then re-run the gate. Any other `FAIL` means an unusual input the transform
  could not normalise (e.g. a builder expression it could not parse, or an exotic
  multi-line import). Inspect the gate's `gaps` array, hand-correct the named
  `file:line`(s), then re-run the imports gate. If it still fails after one
  correction pass, escalate to the user.
- Exit `3` (IO / usage error) → STOP and escalate.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 3: imports and headers updated"`
