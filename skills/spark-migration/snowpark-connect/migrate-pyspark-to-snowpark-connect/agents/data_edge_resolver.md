# Data Edge Resolver Agent

**Goal:** produce a complete data dependency graph for this PySpark / Python
workload by reading every source file and tracing data flow until every edge
either leads to another file in the workload (which must also be analyzed) or
reaches an external system (S3, database table, message queue, etc. —
the dead-end that terminates a chain).

No Snowflake connection required.  Any LLM agent with file-system access can
perform this analysis.

---

## Inputs

- `assessmentir_path` — path to `AssessmentIR.json`
- `workload_dir` — root directory of the workload to analyze

Read `AssessmentIR.json`. Extract:
- `unresolved_data_edges` — read/write call sites the static scanner found but
  could not resolve; treat these as starting hints, not as the complete set
- `unresolved_dynamic_imports` — dynamic-import call sites (`importlib`,
  `__import__`, `spec_from_file_location`, `imp.load_source`, `entry_points`,
  factory dicts) whose target the static dispatch could not resolve to a
  workload file. You must resolve or confirm-unresolvable **each** of these too.
- `data_dependency_graph.nodes` / `.edges` — existing graph context
- Any existing `llm_resolved_data_edges` — if already populated and the caller
  has not requested a re-run, skip this run

Read `workload_dir`.  List every `.py`, `.sql`, and `.ipynb` file recursively.
**These files are your work queue.**

---

## The reconciliation contract (read this first)

The renderer and the coverage gate reconcile your output against the IR's
`unresolved_data_edges` and `unresolved_dynamic_imports` by matching on the
**exact `(file, line, kind)` triple**. This has two hard consequences:

1. **Reuse the original `(file, line, kind)` verbatim.** When you resolve or
   confirm-unresolvable an entry that was in `unresolved_data_edges` /
   `unresolved_dynamic_imports`, copy its `file`, `line`, and `kind` unchanged
   into your `edges` / `unresolvable_edges` / `resolved_imports` output. A
   different line number (even off by one) means the gate cannot pair them and
   reports the original as a **leak** → the gate exits 2 and you are re-invoked.

2. **Do NOT clear `unresolved_data_edges` or `unresolved_dynamic_imports`.**
   Leave both lists exactly as you found them. They are the reconciliation
   baseline; the renderer subtracts what you accounted for and displays only
   the remainder. Clearing them destroys the gate's ability to verify your work.

Every entry in those two lists must end up accounted for by **exactly one** of:
- an `edges` entry with `source = "resolved_unresolved"` (you resolved it), or
- an `unresolvable_edges` entry (data read/write you confirmed unresolvable), or
- a `resolved_imports` entry (dynamic import — resolved OR confirmed unresolvable).

---

## Analysis

### Step 1 — Read every file in the workload

Read **all** `.py`, `.sql`, and `.ipynb` files under `workload_dir`.  For each
file, discover every data I/O operation:

- Spark reads and writes (`spark.read`, `df.write`, `spark.sql`, `spark.table`,
  `DeltaTable.for*`, streaming sources/sinks, etc.)
- Pandas / Arrow I/O (`pd.read_*`, `df.to_*`)
- Cloud storage clients (boto3, Azure SDK, GCS — any `upload_file`,
  `put_object`, `download_file`, `get_object`, `read`, etc.)
- Database connections (psycopg2, pymysql, cx_Oracle, sqlalchemy `execute`,
  Snowflake connector, JDBC)
- Message queues and streams (Kafka producers/consumers, SQS, Pub/Sub)
- ML tracking (mlflow `log_artifact`, `log_model`, etc.)
- File opens for data (`open(path, ...)` on CSV / Parquet / JSON files)
- Any other pattern whose call signature suggests external data access

Do not stop at the first pattern you recognize.  Read the **whole file**.

### Step 2 — Trace dynamic arguments to their values

When an I/O call uses a variable, f-string, or function return value instead of
a literal, trace the value:

- Follow assignment chains across the file and its local imports
- Read function bodies and class `__init__` methods
- Expand f-strings, string concatenations, and `os.path.join` calls
- Substitute `os.environ.get("X", "default")` → the default value
- Evaluate `dict[constant_key]` lookups
- Read config files opened with a **literal filename** (`open("config.yaml")`,
  `yaml.load(...)`, `json.load(...)`, `configparser.read(...)`)
- Follow `dbutils.widgets.get()` defaults and `%run` notebook magic

### Step 3 — Trace intra-workload file references (recursive)

When a source file **references another file inside `workload_dir`** — for
example by opening a `.sql` file, reading a config, or `%run`-ing a notebook —
you **must** also read and analyze that referenced file.  Recursively follow
every intra-workload file reference until every chain either:

- terminates at a file you have already analyzed, **or**
- terminates at an external system (S3 path, database table, message queue,
  etc.) that is not a file in `workload_dir`

**Example**: a Python file does
```python
with open(SQL_FILE_PATH, 'r') as fp:
    query = fp.read()
query = Template(query).safe_substitute(DATABASE_NAME=db, SCHEMA_STAGING=stg)
spark.sql(query)
```
Trace `SQL_FILE_PATH` to its literal value, locate the file in `workload_dir`,
read it, and extract every table the SQL reads (`FROM`, `JOIN`, temporary view
sources) and writes (`INSERT INTO`, `UPDATE`, `MERGE INTO`, `DELETE FROM`,
`CREATE TABLE`, `CREATE OR REPLACE TABLE`).  The SQL file itself must also
appear in your `analyzed_files` output.

**CRITICAL — SQL files are first-class DAG nodes.**

Every table read and write discovered inside a SQL file **must be emitted as
an edge where `file` = the SQL file's workload-relative path**, NOT the Python
file that loads and executes it.

```jsonc
// WRONG — table attributed to Python caller; queries.sql becomes a DAG island:
{"file": "etl/queries.py", "kind": "read", "resolved_signature": "v_fct_price_latest", ...}

// RIGHT — table attributed to the SQL file that actually reads it:
{"file": "sql/queries.sql", "kind": "read", "resolved_signature": "{db}.{ml}.v_fct_price_latest", ...}
```

The Python file's own `file` field appears **only** for:
- Direct Python I/O the Python code performs itself (boto3 S3, paramiko SFTP,
  pandas `read_csv`, explicit `saveAsTable` calls in the Python file, etc.)
- NOT for table reads/writes that happen inside a SQL file the Python loads

A SQL file in `analyzed_files` with zero edges causes the gate to exit 2
(`edge_gaps`).  The gate enforces this: every file in `analyzed_files` must
have at least one edge **or** at least one unresolvable entry whose `file`
matches it.

For a SQL file that has no Python caller within the workload but contains data
I/O (e.g., a library SQL script), emit edges with `file` = the SQL file path
and `source` = `"newly_discovered"`.  If the SQL file has no call sites in the
workload and its execution context cannot be traced, add an unresolvable entry
explaining that the caller is absent from the workload export.

When table names use runtime-substituted catalog/schema prefixes
(`${DATABASE_NAME}.${SCHEMA_STAGING}.MY_TABLE`), the **table name component
is a literal** and is sufficient for DAG construction.  Use the form
`{db}.{staging}.my_table` (replace `${}` with `{}`, lowercase) as the
`resolved_signature`.  The renderer normalizes `{placeholder}` tokens away,
so `{db}.{staging}.my_table` → `my_table` — a valid DAG node key.

### Step 4 — Infer implicit relationships

Read the **entire file set together**.  Use cross-file context to infer
relationships that are only visible when you see multiple files at once:

- If file A writes table `FCT_PRICE` and file B reads `V_FCT_PRICE_LATEST`,
  and the `V_` naming convention and absence of any DDL creating the view
  suggest it is a database view over `FCT_PRICE`, emit a connection between
  A and B via the underlying table.
- If two files share a staging table (one writes it, another reads it),
  they are data-dependent even if no Python import links them.
- If a config file lists input/output paths used by multiple Python files,
  trace through the config to link producer and consumer.

You are not restricted to patterns the static scanner understands.  Any
cross-file data relationship visible from reading the code belongs in the graph.

### Step 5 — Resolve every unresolved dynamic import

The workload's orchestration layer often loads task modules dynamically
(`spec_from_file_location(path)`, `importlib.import_module(name)`, a factory
dict, an `entry_points` lookup). When the path/name is a runtime-computed
value, the static dispatch gives up and files the site under
`unresolved_dynamic_imports`. You read the whole codebase — so you can finish
the job the static scanner could not.

For **each** entry in `unresolved_dynamic_imports`:

- Read the orchestrator file at that line. Trace the argument: follow the
  variable/f-string/loop that builds the module name or file path, expand it
  against directory listings and any config, and identify which workload
  file(s) the import actually loads at runtime.
- If you can determine the target file(s), emit a `resolved_imports` entry with
  `resolved_targets` set to their workload-relative paths and
  `resolution_type` = `literal_found` / `traced` / `inferred`.
- If the target genuinely cannot be determined (the value comes from
  `sys.argv`, an env var with no default, or a live service; or the loaded file
  is not part of this workload export), emit a `resolved_imports` entry with
  `resolution_type` = `"unresolvable"` and a clear `why_unresolvable`.

Reuse the exact `(file, line, kind)` from the `unresolved_dynamic_imports`
entry (see the reconciliation contract above). Resolving an import is an
orchestration fact, not a data edge — do **not** invent data read/write edges
for it unless the code at that site actually performs I/O.

---

## Classifying each resolved value

| `resolution_type` | When to use |
|---|---|
| `literal_found` | Extracted a literal string directly; cite exact file + line |
| `traced` | Followed a multi-step chain; describe each step in `explanation` |
| `inferred` | Cross-file inference with no single traceable chain — use sparingly |

All three resolution types are drawn in the DAG (`inferred` is lower-confidence,
not excluded) and all count as resolved for reconciliation. Reserve `inferred`
for cross-file inferences backed by clear contextual evidence; prefer
`unresolvable_edges` over `inferred` for pure guesses.

### Edge `kind` — use the real verb

Set `kind` to the actual I/O verb at the call site: `read`, `write`, `merge`,
`delete`, `drop`, `truncate`. Report destructive/DDL operations honestly — a
`DROP TABLE` is `kind: "drop"`, not `"write"`.

The renderer maps kinds to lineage roles: `read` consumes (source), `write` /
`merge` produce (sink), and **`drop` / `delete` / `truncate` are neutral** — a
teardown is not a data write, so it never creates a writer→reader edge. If you
mislabel a `DROP` as `write`, the file that drops a temp table looks like its
producer and a false backward edge appears to whoever reads it. So label the
verb accurately and the graph orients correctly.

Place a call site in `unresolvable_edges` when the target genuinely cannot be
determined — runtime argparse / `sys.argv`, env var without a default, live
config service, or a function with **no call sites in these files**.  Do not
use `unresolvable_edges` just because an argument uses a runtime prefix for
catalog or schema — trace the table name.

For every `unresolvable_edges` entry, set `severity` to how much the gap
matters for migration — you read the code, so you make this call:
- `critical` — a required input is **absent from the export**: a caller,
  source file, module, or upstream table that was not included. This can block
  a correct migration and the user likely must supply the missing piece.
- `informational` — a genuine external read/write whose exact target is only
  known at runtime (config-driven path, a parameter with no static caller in
  scope). The code migrates fine; this is a data-lineage blind spot to note.
- `benign` — not actually an external dependency: a scanner misclassification
  (e.g. an in-memory DataFrame op), dead code, or a destructive / no-op call.

Set the same `severity` on any `resolved_imports` entry whose `resolution_type`
is `unresolvable` (a missing orchestrator/module the workload needs is
`critical`; runtime-only dispatch is `informational`; dead code is `benign`).

**Always fill `severity` — never leave it blank or `null`.** It is required on
every `unresolvable_edges` entry and on every `unresolvable` `resolved_imports`
entry. You read the code, so assign it confidently; if you are genuinely
uncertain, use your best judgment rather than omitting it. When truly on the
fence, choose `informational` (treat it as a lineage note) instead of guessing
`critical` or `benign` — but still fill the field.

### Step 6 — Record pipeline handoffs that share no table

Stages are often chained by **control / parameter handoffs** rather than a
shared table: a Databricks `dbutils.jobs.taskValues.set(...)` in one notebook
read by `taskValues.get(...)` in the next, a `%run ./other_notebook`, a
notebook-workflow call, or an external job dependency. Data-signature matching
cannot see these, so the downstream stage looks like a disconnected island even
though it clearly runs after the upstream one.

For every such handoff you can see from reading the code, add an
`orchestration_edges` entry `{from_file, to_file, mechanism, explanation}`.
The renderer draws these as dashed **orchestrates** arrows so a fan-out /
orchestration stage (e.g. a `Part_0` that seeds task values for `Part_1`) joins
the DAG instead of floating alone. This is the same information your advisory
prose describes ("Part_0 fans out … Part_1 runs …") — record it as edges too,
so the diagram matches the narrative.

---

## Write results back → then the gate runs

Construct the `llm_resolved_data_edges` object (edges, unresolvable_edges,
resolved_imports, orchestration_edges, analyzed_files, excluded_files,
llm_insights) and write it into `AssessmentIR.json` at `assessmentir_path`.
Then the reporter runs the coverage gate; **your job is done when the gate
exits `0`**, and on exit `2` you re-run only the files/edges it names, reusing
each item's **exact `(file, line, kind)`** (a mismatched line is the usual
cause of a leak). Do **not** clear `unresolved_data_edges` /
`unresolved_dynamic_imports` — they are the reconciliation baseline.

**Load** `references/llm_resolved_data_edges.schema.json` — the JSON Schema
(draft 2020-12) your output must satisfy: required fields, types, and the
`source` / `resolution_type` enums, with each field's meaning in its
`description`. The gate validates your block against this schema and reports any
violation as `schema_errors`, so conforming to it is not optional.

---

## Guidelines

- **Read the whole workload, not just the static scanner's list.**  The static
  scanner's `unresolved_data_edges` are hints; the real completeness criterion
  is that every file in `workload_dir` is covered.
- **One edge per distinct table/path per call site.**  A single `spark.sql()`
  executing a SQL file that touches 5 tables produces 5 edges.
- **Prefer `unresolvable_edges` over `inferred`** for pure guesses.  Use
  `inferred` only for cross-file inferences with clear contextual evidence.
- **Normalise resolved signatures**: lowercase; replace `${VAR}` with `{var}`;
  strip trailing slashes; collapse repeated separators.
- **Workload completeness signal**: if a function defined in the workload has no
  call sites within these files, note it in `why_unresolvable` AND in
  `llm_insights` — a strong signal that caller scripts are missing from the export.
