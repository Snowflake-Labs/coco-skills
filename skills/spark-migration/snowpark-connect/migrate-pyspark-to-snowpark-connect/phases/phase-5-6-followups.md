# Phase 5: Offer Validation (Optional)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

Ask the user:
```
Migration complete! Would you like to validate the migrated workload
by running it end-to-end with synthetic data?
```

If yes, load `validate-pyspark-to-snowpark-connect/SKILL.md` with `<MIGRATED>` as `$ARGUMENTS`.

If the user accepted validation and it completed successfully, run the validation feedback generator (non-fatal). Skip this step entirely if the user declined validation or if validation did not complete:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/generate_validate_feedback.py \
  --conv-root <CONVERSION>
```

Output: `<CONVERSION>/Feedback/validate_feedback.md`

# Phase 6: Offer Notebook Conversion (Standalone Only)

<!-- Added for native notebook processing — only runs in standalone invocation. -->
Skip this phase entirely when the parent orchestrator (`snowflake-migration`) invoked Snowpark Connect. The orchestrator runs notebook conversion as its own later step and passes an explicit flag in the invocation context:

```
snowpark_connect_invoker: orchestrator
```

1. **Log the detected invoker** as the FIRST action of this phase. Parse the invocation context for `snowpark_connect_invoker`. Treat any value except `orchestrator` (including `standalone` and the missing case) as standalone. Print one line:
   ```
   Phase 6 invoker: <orchestrator|standalone>
   ```
2. **Orchestrator-mode gate**. Skip Phase 6 and proceed to Resumption if either:
   - `snowpark_connect_invoker == "orchestrator"`, OR
   - (legacy) the literal string `snowflake-migration` appears as the invoker — preserved only for callers that predate the invoker flag. New callers MUST set `snowpark_connect_invoker` explicitly.
   When skipping, print `Phase 6 skipped: orchestrator mode` and exit.

3. **Check whether `snowflake-notebook-migration` is installed** — use the same lookup pattern as `snowflake-migration/SKILL.md` Step 7 (search available/installed skills for `snowflake-notebook-migration`).

4. **If the skill is NOT installed**, print the informational note and exit Phase 6:
   ```
   Notebook Conversion (optional follow-up):
   To convert the migrated notebooks under <MIGRATED> to Snowflake Workspace
   `.ipynb` format, install the `snowflake-notebook-migration` skill and run
   it against the Output/ directory.
   ```

5. **If the skill IS installed**, ask the user:
   ```
   I can also convert the migrated Databricks notebooks to Snowflake
   Workspace format using the `snowflake-notebook-migration` skill.
   Would you like me to run that now on <MIGRATED>? (y/n)
   ```

6. **If the user answers yes**, load the bundled `snowflake-notebook-migration` sub-skill **in the foreground** and follow it inline. Resolve its path relative to the `spark-migration` root (`<spark_migration_root>` = this skill's grandparent = `snowpark-connect/..`):
   ```bash
   NB_MIGRATION_SKILL="<spark_migration_root>/snowflake-notebook-migration/SKILL.md"
   ```
   Read that file with the Read tool, then follow its instructions with `<MIGRATED>` as the argument. Never spawn it as a background agent. Do NOT use `skill("snowflake-notebook-migration")` — it is a bundled sub-skill at the `spark-migration` root, not a registered top-level skill in this nested context.

7. **If the user answers no**, print the informational note above and exit Phase 6.
