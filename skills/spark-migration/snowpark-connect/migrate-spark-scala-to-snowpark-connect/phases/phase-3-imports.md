# Phase 3: Imports, Session, Build, and Headers

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 3: Imports, Session, Build, and Headers

**Run the import-updater directly (no specialist agent)** — every action of this phase is mechanical (rename the session builder, drop unsupported imports, materialize preserved config, transform build files, stamp a header), so the coordinator runs the deterministic `update_imports_scala.py` itself instead of spawning an LLM specialist. This replaces the former `agents/import-updater.md` LLM specialist, mirroring how `verify_phase.py` replaced the LLM critic agents. This is the Scala counterpart of PySpark's `update_imports.py` (which likewise replaced PySpark's former `agents/import-updater.md`).

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/update_imports_scala.py \
  --state <CONVERSION>/migration_state.json
echo "update_imports_scala_exit=$?"
```

What it does for every manifest file (and Scala cells in notebooks): injects the `SnowparkConnectSession` import, materializes `// SCOS-RECIPE-PRESERVED-CONFIG: k=v` markers (emitted by Phase 0.5) into `.config("k","v")` on the builder, removes unsupported import lines, transforms `build.sbt`/`pom.xml`/`build.gradle`/`build.gradle.kts` (snowpark-connect-java-client + `--add-opens` flags, removing `spark-connect-client-jvm`/`spark-hive`), and prepends an idempotent SCOS migration header. The `SparkSession` → `SnowparkConnectSession` rename and the `.master()`/`.enableHiveSupport()`/`.remote()` drops were already done by the Phase 0.5 `ScosSparkSessionBuilderRewrite` Scalafix rule; Phase 3 repeats those steps only as a fallback when Phase 0.5 did not run (no JVM/sbt toolchain). Test files (`*Spec/Test/Suite`) keep `master("local[*]")` and are left on `SparkSession` with a TODO.

> **Maven version pinning**: Maven has no safe dynamic-version keyword, so when the concrete `snowpark-connect-java-client` version is unknown the script emits a `PIN_CONCRETE_VERSION` placeholder + a `SCOS: TODO`. The Phase-3 gate then FAILs on that placeholder by design — a human must pin the version. sbt/Gradle use the valid `latest.release` and pass automatically.

**Verify (deterministic)**: run `verify_phase.py --phase 3` — covers migration header, session init replacement, `SnowparkConnectSession` present, no unsupported imports, build files transformed, syntax artifacts, file count, and recipe-preserved-config materialization:

```bash
python3 <SKILL_DIRECTORY>/scripts/verify_phase.py \
  --phase 3 --language scala --strict \
  --state <CONVERSION>/migration_state.json
echo "verify_phase3_exit=$?"
```

**Gate**: exit 0 ⇒ `PASS` or `PASS_WITH_GAPS`. On exit 1 (`FAIL`), address the listed failures (e.g. pin a Maven version) and re-run the verifier. Update `migration_state.json` phase to 3.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 3: imports, session, build, and headers updated"`
