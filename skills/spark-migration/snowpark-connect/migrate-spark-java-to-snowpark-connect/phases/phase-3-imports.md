# Phase 3: Imports, Session, Build, and Headers

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 3: Imports, Session, Build, and Headers

**Run the import-updater directly (no specialist agent)**:
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/update_imports_java.py \
  --state <CONVERSION>/migration_state.json
echo "update_imports_java_exit=$?"
```

What it does for every `.java` file: injects `SnowparkConnectSession` import, materializes `// SCOS-RECIPE-PRESERVED-CONFIG: k=v` markers into `.config("k","v")` on the builder, removes unsupported import lines, transforms `pom.xml`/`build.gradle`/`build.gradle.kts` (snowpark-connect-java-client + `--add-opens` flags), and prepends an idempotent SCOS migration header.

> **Maven version pinning**: Maven has no safe dynamic-version keyword, so when the concrete `snowpark-connect-java-client` version is unknown the script emits a `PIN_CONCRETE_VERSION` placeholder. The Phase-3 gate then FAILs on that placeholder — a human must pin the version.

**Verify (deterministic)**:
```bash
python3 <SKILL_DIRECTORY>/scripts/verify_phase.py \
  --phase 3 --language java --strict \
  --state <CONVERSION>/migration_state.json
echo "verify_phase3_exit=$?"
```

**Gate**: exit 0 ⇒ `PASS` or `PASS_WITH_GAPS`. On exit 1 (`FAIL`), address the listed failures and re-run the verifier.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 3: imports, session, build, and headers updated"`
