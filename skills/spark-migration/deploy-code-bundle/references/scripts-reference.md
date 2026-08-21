# Scripts reference (deploy-code-bundle)

Run via `uv run --project <SKILL_DIRECTORY> python <path>.py` (the `--project`
venv always supplies `snowflake-connector-python`).

**Shared vs bundle-specific.** Steps 0 / 0.5 / 1 use scripts shared with the
other deploy sub-skills; they live in the sibling `deploy-common/scripts/` dir.
For brevity, `<COMMON_SCRIPTS>` = `<SKILL_DIRECTORY>/../deploy-common/scripts`.
Bundle-specific scripts (Steps 0.7, 2, 3) live in `<SKILL_DIRECTORY>/scripts/`.

| Script | Location | Step | Type |
|--------|----------|------|------|
| `scan_migration_gaps.py` | deploy-common | 0 | deterministic — collects `# SCOS:`/`WARN`/`TODO` markers, `Reports/Issues.csv`, unresolved `<DATABASE>`/`<SCHEMA>`/`<WAREHOUSE>`/`<ROLE>` placeholders, `requirements.txt` |
| `ensure_namespace.py` | deploy-common | 0.5 | deterministic — `SHOW DATABASES`/`SHOW SCHEMAS` existence check; with `--create`, `CREATE DATABASE`/`SCHEMA IF NOT EXISTS` |
| `inspect_entrypoint.py` | deploy-code-bundle | 0.6 | deterministic — AST detect: `__main__` entrypoints + argparse contract, source root, config candidates, namespace use, local-file hints, `needs_wrapper` |
| `generate_entrypoint.py` | deploy-code-bundle | 0.6 | deterministic — draft+write a thin launcher (B1 snowpark_connect import, B2 namespace, B3 sys.path, B4 stage-file localize, B5 call existing entry) |
| `prepare_code_bundle.py` | deploy-code-bundle | 0.7 | deterministic — writes `code_bundle.yml` (compute_options.runtime_version always emitted; curates requirements: drops pyspark, adds snowpark-connect) |
| `upload_project.py` | deploy-common | 1 | deterministic — `CREATE STAGE` + recursive `PUT` preserving relative paths |
| `create_code_bundle.py` | deploy-code-bundle | 2 | deterministic — `CREATE CODE BUNDLE … FROM '@stage/…'` + `DESCRIBE CODE BUNDLE` verify |
| `execute_code_bundle.py` | deploy-code-bundle | 3 (opt-in) | deterministic — `USE WAREHOUSE` (via `--warehouse`) + `EXECUTE CODE BUNDLE … ENTRYPOINT=…` + PASS/FAIL report |
