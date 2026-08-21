# ASG Operations Guide for AI Agents

## What This Document Is

This is a step-by-step operational guide for an AI agent that receives an
ASG (Abstract Semantic Graph) file. It tells you **what to do**, not what
the format is.

**Companion resources you should have access to:**

- `docs/29_ASG_REFERENCE.md` — explains the ASG structure, field
  definitions, and design principles.
- `docs/schemas/asg_schema.json` — the formal JSON Schema for
  validation and field-level type reference.

If you need to know what a field means or what values an enum accepts,
consult those files. This document assumes you already understand the
structure and focuses on **how to reason over it**.

---

## Step 1: Assess the Workload at a Glance

Before diving into details, build a mental model of the workload.

```
Read: extraction_metadata.app_name
Read: parsing_report.{total_files, syntax.errors, understanding.errors}
Count: len(data_in), len(data_out), len(transformations)
Count: len(functions), len(control_nodes)
```

From these numbers you can classify the workload:

| Pattern                            | Interpretation                         |
|------------------------------------|----------------------------------------|
| 1 file, 0 errors, few transforms   | Simple linear ETL                      |
| Multiple files, functions > 3      | Modular pipeline with reusable logic   |
| control_nodes > 0                  | Branching/looping logic (complex)      |
| data_out > 3                       | Multi-output pipeline (fan-out)        |
| syntax.errors > 0                  | Parsing issues — results may be partial|

---

## Step 2: Map Data Sources and Sinks

Build a table of what the workload reads and writes.

```
For each item in data_in where is_test_file != true:
  Record: id, name, type, len(inferred_columns)

For each item in data_out where is_test_file != true:
  Record: id, name, type, source_id, mode
```

**Flag issues:**
- `data_in` with `name` starting with `runtime:` — the name is dynamic
  and could not be resolved statically. Treat with caution.
- `data_in` with 0 `inferred_columns` — no schema could be inferred.
  This source needs external schema information or is unused.
- `data_in` with `is_indirect = true` — data is read through a wrapper
  function (`via_function`), not a direct `spark.read`.

---

## Step 3: Understand the Transformation DAG

Build the graph topology by following `inputs` edges.

```
For each transformation:
  node = tx.id
  parents = tx.inputs   (list of in_NNN or tx_NNN)
  operation = tx.operation

For each data_out:
  terminal_node = data_out.source_id
```

**Identify key patterns:**

- **Joins**: transformations where `operation` is `join` or `join_custom`.
  These have exactly 2 entries in `inputs`. Cross-reference with
  `column_relationships` for the join keys and type.
- **Aggregations**: `operation = groupBy_agg`. The `parameters.group_columns`
  are the dimensions, `parameters.column_aliases` are the measures.
- **Filters**: `operation = filter`. The `parameters.condition` contains
  the predicate. Cross-reference with `column_constraints` for the
  structured constraint.
- **Created columns**: `operation = withColumn`. The
  `parameters.column_name` is a new column that does not exist in any
  source data.

---

## Step 4: Infer Schemas for Each Source

For each `data_in`, build its column schema.

```
For each data_in:
  If data_in.columns is not empty:
    Use columns (explicit schema — highest reliability)
  Else:
    Use inferred_columns (code-analysis schema)
```

**Prioritize columns by confidence:**

| Priority | `source` values                                      |
|----------|------------------------------------------------------|
| 1st      | `explicit`, `schema_definition`                      |
| 2nd      | `filter_condition`, `join_key`, `group_by`, `aggregation` |
| 3rd      | `select`, `order_by`, `function_arg`                 |
| 4th      | `xref_output`, `xref_input`, `xref_function`         |
| 5th      | `usage`, `naming_convention`                          |

**Type reliability:** When `inferred_type` is `UNKNOWN`, the type could
not be determined. Consider the column name as a hint:
- `*_id`, `*_key`, `*_code`, `*_name` → likely `STRING`
- `*_amount`, `*_price`, `*_total`, `*_rate` → likely `DECIMAL`
- `*_date`, `*_at`, `*_timestamp` → likely `DATE` or `TIMESTAMP`
- `*_count`, `*_num`, `*_qty` → likely `INT` or `LONG`
- `*_flag`, `is_*`, `has_*` → likely `BOOLEAN`

---

## Step 5: Map Relationships Between Sources

Use `column_relationships` to understand how tables connect.

```
For each relationship:
  left:  {left_source}.{left_column}
  right: {right_source}.{right_column}
  type:  join_type (inner, left, right, ...)
  via:   source_transformation (which tx_NNN)
```

**Critical for synthetic data:** If you are generating test data, columns
linked by `join_key` relationships MUST share a common value domain. For
example, if `in_001.customer_id` joins with `in_009.customer_id`, the
generated `customer_id` values in both CSV files must overlap.

**Cardinality hints:**
- `inner` join: both sides must have matching keys
- `left` join: left side drives; right side may have no match (NULLs)
- `anti` join: left side rows where right side has NO match

---

## Step 6: Extract Constraints for Data Generation

Use `column_constraints` to ensure generated data passes filters.

```
For each constraint:
  column:  column_name
  rule:    constraint_type (equals, gt, lt, in, not_null, ...)
  value:   value
  source:  source_transformation
```

**Apply constraints when generating synthetic data:**

| Constraint    | Data generation rule                              |
|---------------|---------------------------------------------------|
| `equals`      | At least some rows must have exactly this value   |
| `gt` / `gte`  | Values must exceed the threshold                  |
| `lt` / `lte`  | Values must be below the threshold                |
| `in`          | Values must come from the specified list           |
| `not_null`    | No NULL values allowed in this column              |
| `between`     | Values must fall within the range                  |
| `like`/`rlike`| String values must match the pattern               |

If constraints conflict (e.g., `gt 100` and `lt 50`), the filter may
produce zero rows. Flag this as a potential issue.

---

## Step 7: Trace Column Lineage (When Needed)

To understand where a specific output column comes from:

```
1. Start at data_out → find column in inferred_columns
2. Follow source_id to the terminal transformation
3. At each transformation:
   a. If column is in inferred_output but NOT inferred_input:
      → Column was CREATED here (withColumn, alias, aggregation)
      → Record: "created at {tx.id} via {tx.operation}"
      → Stop tracing for this column
   b. If column is in both inferred_output AND inferred_input:
      → Column passes through
      → Read from_inputs on the inferred_input entry
      → Follow to the upstream node
4. Repeat step 3 until you reach a data_in node
5. Record: "originates from {data_in.name}.{column_name}"
```

**Special cases:**
- After a `join`, a column may appear with multiple `from_inputs` entries.
  Check `column_relationships` to determine which side it comes from.
- After a `groupBy_agg`, only group keys and aggregation aliases survive.
  All other columns are dropped.
- A `select` restricts which columns pass through. If a column is NOT in
  the `parameters.columns` list, it is dropped.

---

## Step 8: Detect Anomalies and Gaps

Scan the ASG for potential issues:

**Schema gaps:**
```
For each data_in where len(inferred_columns) == 0:
  → "Source {name} has no detected schema"

For each column in data_in.inferred_columns:
  If inferred_type == "UNKNOWN":
    → "Column {name} has unknown type"
  If confidence == "low" and source == "naming_convention":
    → "Column {name} type inferred only from name pattern"
```

**Lineage gaps:**
```
For each data_out:
  If source_id is null:
    → "Output {name} has no lineage to a transformation"

For each data_in:
  connected = false
  For each transformation:
    If data_in.id in transformation.inputs:
      connected = true
  If not connected:
    → "Source {name} is read but never used in transformations"
```

**Quality signals from parsing_report:**
```
If syntax.errors > 0:
  → "Some files failed to parse — ASG may be incomplete"
If understanding.errors > 0:
  → "Pipeline extraction failed for some files"
```

---

## Step 9: Generate Synthetic Test Data (If Applicable)

When generating test CSV/Parquet files for a workload:

1. **One file per `data_in`** with `is_test_file = false`.
2. **Column names** from `inferred_columns[].name`.
3. **Column types** from `inferred_columns[].inferred_type`.
4. **Join integrity** from `column_relationships`: shared key columns
   must have overlapping values across files.
5. **Filter compliance** from `column_constraints`: generated values
   must satisfy predicates so rows survive filters.
6. **Default values** from `inferred_columns[].default_value` when
   available.
7. **Row count**: Start with 10-20 rows per source. For joins, ensure
   the driving side (left in a left join) has more rows than the
   lookup side.

---

## Step 10: Assess Workload Complexity

Use these heuristics to gauge how complex the workload is:

| Metric                          | Simple     | Moderate    | Complex     |
|---------------------------------|------------|-------------|-------------|
| Number of `data_in`             | 1-2        | 3-5         | 6+          |
| Number of `data_out`            | 1          | 2-3         | 4+          |
| Number of `transformations`     | 1-5        | 6-15        | 16+         |
| Number of `join` operations     | 0-1        | 2-3         | 4+          |
| Number of `control_nodes`       | 0          | 1-2         | 3+          |
| Number of `functions`           | 1-2        | 3-5         | 6+          |
| `parsing_report.syntax.errors`  | 0          | 0           | > 0 (risk)  |
| Total unique columns            | < 20       | 20-50       | 50+         |

---

## Quick Reference: Field Locations

| Question                                | Where to look                          |
|-----------------------------------------|----------------------------------------|
| What sources does the workload read?    | `data_in[].name`, `data_in[].type`     |
| What does the workload produce?         | `data_out[].name`, `data_out[].type`   |
| What columns does source X have?        | `data_in[X].inferred_columns`          |
| What type is column Y?                  | `inferred_columns[Y].inferred_type`    |
| How are sources joined?                 | `column_relationships`                 |
| What filters exist?                     | `column_constraints`                   |
| What is the DAG topology?               | `transformations[].inputs`             |
| Which node feeds output Z?              | `data_out[Z].source_id`               |
| What functions exist?                   | `functions[].name`                     |
| Is there branching logic?               | `control_nodes`                        |
| Were there parsing issues?              | `parsing_report`                       |
| What window functions are used?         | `window_specs`                         |
| Is a column newly created or from source?| Compare `inferred_input` vs `inferred_output` on the transformation |
