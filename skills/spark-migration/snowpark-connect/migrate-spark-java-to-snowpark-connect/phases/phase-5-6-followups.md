# Phase 5: Offer Validation (Optional)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 5: Offer Validation (Optional)

Ask the user:
```
Migration complete! Would you like to validate the migrated workload
by running it end-to-end with synthetic data?
```

If yes, load `validate-spark-java-to-snowpark-connect/SKILL.md` with `<MIGRATED>` as `$ARGUMENTS`.

⛔ Do NOT route to `validate-spark-scala-to-snowpark-connect` — that skill checks
`.scala` files and `build.sbt` and will produce incorrect results on Java output.
The Java validator shares the JVM test kit with the Scala one but drives it with
`.java` sources and Maven.

### Phase 6: Offer Notebook Conversion (Standalone Only)

Java Spark workloads rarely use Databricks notebooks, so this phase is typically skipped. If the user has `.ipynb` notebooks with Java cells, offer to run `snowflake-notebook-migration` if installed.

Skip this phase when `snowpark_connect_invoker == "orchestrator"`.

### Resumption

If context is lost mid-migration, read `migration_state.json` to determine the last completed phase and resume from the next one. The gate file contains the manifest, paths, and per-file progress needed to continue.
