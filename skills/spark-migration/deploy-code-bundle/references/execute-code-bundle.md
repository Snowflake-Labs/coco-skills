# Step 3: Validate by executing the bundle (OPT-IN)

**Run this step ONLY when the user explicitly asks to validate / run the deployed
bundle.** A normal deploy ends at Step 2 and must not execute anything.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/execute_code_bundle.py \
  --bundle <DATABASE>.<SCHEMA>.<BUNDLE_NAME> --entrypoint "<ENTRYPOINT>" \
  --warehouse <WAREHOUSE> \
  [--arguments '<args>'] [--history] --connection <CONNECTION> --json
```

Runs `USE WAREHOUSE <WAREHOUSE>` then `EXECUTE CODE BUNDLE <fqn> ENTRYPOINT='<entrypoint>'`
in one session, synchronously. `--warehouse` is **required for warehouse compute_type**
when the connection has no default warehouse (the spec cannot carry `query_warehouse`
for warehouse). `PASS` → report success (and history if requested). `FAIL` → surface
the error tail. The run is identified by its Query ID; recent runs are in
`SNOWFLAKE.INFORMATION_SCHEMA.CODE_BUNDLE_HISTORY(BUNDLE_NAME => '<name>')`.

**Scope boundary**: failures rooted in SCOS code incompatibilities (import order
of `snowpark_connect` before `pyspark`, `USE SCHEMA` namespace, `udf.imports` for
first-party modules, `sklearn` import relocation, single-arg `to_timestamp`, etc.)
are the **migration skill's** responsibility, not deploy — route those back to
`spark-migration`.
