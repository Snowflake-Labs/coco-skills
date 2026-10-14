# ASG (Abstract Semantic Graph) — Reference for AI Agents

## Purpose of This Document

This document describes the **ASG** format produced by Warp-Suite. The ASG is a
structured JSON representation of a data engineering workload (PySpark or Scala
Spark) that captures **what the code does with data** without requiring the
source code itself. An AI agent receiving an ASG file can reason about data
flows, infer schemas, generate synthetic test data, and detect anomalies
without reading a single line of Python or Scala.

---

## 1. What the ASG Represents

The ASG is a **Directed Acyclic Graph (DAG)** encoded as JSON. It models a
complete Spark workload as a data flow from sources to sinks through a chain of
transformations.

```
┌─────────┐     ┌─────────────┐     ┌─────────────┐     ┌──────────┐
│ data_in │────>│ transform   │────>│ transform   │────>│ data_out │
│ (source)│     │ (tx_003)    │     │ (tx_004)    │     │ (sink)   │
└─────────┘     └─────────────┘     └─────────────┘     └──────────┘
   in_001           join              withColumn           out_007
                  ┌────┘
┌─────────┐      │
│ data_in │──────┘
│ (source)│
└─────────┘
   in_002
```

Every node has a unique ID. Edges are encoded via `inputs` arrays on
transformation and sink nodes.

### Node Types

| ID prefix  | Type                 | Role                                  |
|------------|----------------------|---------------------------------------|
| `in_NNN`   | `DataSource`         | Where data enters the pipeline        |
| `tx_NNN`   | `TransformationNode` | A DataFrame operation                 |
| `out_NNN`  | `DataSink`           | Where data exits the pipeline         |
| `ctrl_NNN` | `ControlNode`        | Branching, loops, error handling       |
| `call_NNN` | `ExecutionCall`      | A function invocation                 |

---

## 2. Top-Level Structure

An ASG JSON file contains these top-level keys:

```json
{
  "extraction_metadata": { ... },
  "source_files":        [ ... ],
  "functions":           [ ... ],
  "execution_calls":     [ ... ],
  "execution_instances": [ ... ],
  "data_in":             [ ... ],
  "data_out":            [ ... ],
  "transformations":     [ ... ],
  "control_nodes":       [ ... ],
  "warnings":            [ ... ],
  "window_specs":        [ ... ],
  "parsing_report":      { ... },
  "column_constraints":  [ ... ],
  "column_relationships":[ ... ]
}
```

### 2.1 `extraction_metadata`

Project-level context about the extraction.

| Field           | Type       | Description                              |
|-----------------|------------|------------------------------------------|
| `workload_root` | `string`   | Absolute path to the workload directory  |
| `app_name`      | `string?`  | Spark application name if detected       |
| `spark_version` | `string?`  | Spark version if detected                |
| `generated_at`  | `datetime` | When the ASG was generated (ISO 8601)    |

### 2.2 `source_files`

List of source code files that were parsed. Each entry includes:

| Field                   | Type      | Description                              |
|-------------------------|-----------|------------------------------------------|
| `path`                  | `string`  | Relative path from `workload_root`       |
| `source_type`           | `enum`    | `notebook`, `module`, `script`, `unknown`|
| `is_entry_point`        | `bool`    | True if this file starts execution       |
| `has_spark_session`     | `bool`    | True if file creates a SparkSession      |
| `imports`               | `object`  | Map of import alias to import details    |
| `notebook_dependencies` | `array`   | Other notebooks invoked via `%run`       |

### 2.3 `functions`

All function definitions found across source files.

| Field                | Type      | Description                               |
|----------------------|-----------|-------------------------------------------|
| `name`               | `string`  | Function name                             |
| `containing_class`   | `string?` | Class name if it is a method              |
| `source_file`        | `string?` | Which file defines this function          |
| `arguments`          | `array`   | List of `{name, inferred_type, inferred_schema_origin}` |
| `returns`            | `object?` | `{ref_type, ref_id, inferred_type}`       |
| `is_udf`             | `bool`    | True if registered as a Spark UDF         |
| `udf_return_schema`  | `string?` | Spark return type for UDFs                |

**`returns.ref_type`** tells you what the function returns:
- `transformation` — points to a `tx_NNN` node (the last in a Spark chain)
- `variable` — returns a named variable
- `void` — no return (action/sink function)
- `data_source` — returns a read DataFrame

### 2.4 `execution_calls`

The call graph: which function calls which, with what arguments.

| Field                | Type      | Description                                       |
|----------------------|-----------|---------------------------------------------------|
| `call_id`            | `string`  | Unique ID (`call_001`, `call_002`, ...)           |
| `caller`             | `object`  | `{function, line, file}` — where the call occurs  |
| `callee`             | `object`  | `{function, file}` — what is being called          |
| `bindings.inputs`    | `array`   | Maps each argument to its source (data_in, tx, variable, literal) |
| `bindings.output`    | `object?` | Where the return value is stored                   |
| `literal_arguments`  | `object`  | Resolved string literals (e.g., table names)       |

### 2.5 `execution_instances`

Each entry represents a complete execution flow from an entry point. The
`bindings` array traces how data flows from literals/sources through function
calls, enabling late binding resolution (e.g., when a function parameter
resolves to a table name at runtime).

---

## 3. The Data Flow Core

### 3.1 `data_in` — Data Sources

Each entry represents a point where data enters the pipeline.

| Field              | Type      | Description                                     |
|--------------------|-----------|-------------------------------------------------|
| `id`               | `string`  | Unique ID (`in_001`, `in_002`, ...)            |
| `type`             | `enum`    | `table`, `csv`, `parquet`, `json`, `delta`, `jdbc`, `sql`, `memory`, `config`, `other` |
| `name`             | `string?` | Resolved table/file name                       |
| `path`             | `string?` | File path or connection string                 |
| `query`            | `string?` | SQL query if type is `sql`                     |
| `columns`          | `array`   | Explicit schema columns (`{name, dtype}`)      |
| `inferred_columns` | `array`   | Columns discovered by code analysis            |
| `required_columns` | `array`   | Columns required by downstream transformations |
| `is_indirect`      | `bool`    | True if read via a utility function            |
| `via_function`     | `string?` | Name of the wrapper function                   |
| `is_test_file`     | `bool`    | True if from a test file                       |

#### `inferred_columns` — the most important field for schema inference

Each element describes a column discovered by static analysis:

| Field              | Type      | Description                               |
|--------------------|-----------|-------------------------------------------|
| `name`             | `string`  | Column name                               |
| `inferred_type`    | `string`  | `STRING`, `INT`, `LONG`, `DECIMAL`, `NUMERIC`, `DATE`, `TIMESTAMP`, `BOOLEAN`, `UNKNOWN` |
| `source`           | `enum`    | How the column was discovered (see below) |
| `confidence`       | `enum`    | `high`, `medium`, `low`                   |
| `default_value`    | `string?` | Known default value                       |
| `first_seen_nodes` | `array`   | Node IDs where this column first appeared |
| `usage_count`      | `int`     | How many times this column is referenced  |

**`source` values** (inference provenance):

| Source               | Meaning                                               | Reliability |
|----------------------|-------------------------------------------------------|-------------|
| `explicit`           | Schema defined in code (`StructType`)                 | Highest     |
| `schema_definition`  | Schema from catalog or DDL                            | Highest     |
| `filter_condition`   | Column appears in `.filter()` / `.where()`            | High        |
| `join_key`           | Column used as join key                               | High        |
| `group_by`           | Column used in `.groupBy()`                           | High        |
| `aggregation`        | Column used inside `sum()`, `count()`, etc.           | High        |
| `select`             | Column appears in `.select()`                         | Medium      |
| `order_by`           | Column appears in `.orderBy()`                        | Medium      |
| `function_arg`       | Column passed as argument to a function               | Medium      |
| `xref_output`        | Inferred from downstream output                      | Medium      |
| `xref_input`         | Inferred from upstream input                          | Medium      |
| `xref_function`      | Inferred across function boundaries                   | Lower       |
| `usage`              | Column referenced somewhere in logic                  | Lower       |
| `naming_convention`  | Type inferred from column name pattern (e.g., `*_id` is STRING) | Lowest |

### 3.2 `data_out` — Data Sinks

Each entry represents a point where data exits the pipeline.

| Field              | Type      | Description                                     |
|--------------------|-----------|-------------------------------------------------|
| `id`               | `string`  | Unique ID (`out_NNN`)                          |
| `type`             | `enum`    | Same types as `data_in`                        |
| `name`             | `string?` | Target table/file name                         |
| `path`             | `string?` | Target path                                    |
| `mode`             | `enum`    | `overwrite`, `append`, `ignore`, `error`       |
| `source_id`        | `string?` | The transformation node that feeds this sink   |
| `inferred_columns` | `array`   | Columns that will be written                   |

**Key relationship**: `source_id` links back to a `tx_NNN` node. Following
`source_id` then `tx.inputs` recursively traces the full lineage from
output back to input.

### 3.3 `transformations` — The Processing Chain

Each entry represents a single DataFrame operation.

| Field                    | Type      | Description                                |
|--------------------------|-----------|--------------------------------------------|
| `id`                     | `string`  | Unique ID (`tx_NNN`)                      |
| `operation`              | `string`  | The Spark operation (see table below)     |
| `inputs`                 | `array`   | IDs of upstream nodes (`in_NNN` or `tx_NNN`) |
| `outputs`                | `array`   | IDs of downstream nodes (rarely used)     |
| `logic`                  | `string?` | Human-readable Spark expression           |
| `parameters`             | `object`  | Operation-specific parameters             |
| `inferred_input`         | `array`   | Columns entering this transformation (with `from_inputs`) |
| `inferred_output`        | `array`   | Columns exiting this transformation       |
| `category`               | `enum?`   | `relational`, `builtin_function`, `python_udf`, `rdd_low_level`, `system_ops` |
| `feasibility`            | `enum?`   | Migration feasibility: `high`, `medium`, `low`, `blocker` |
| `is_deterministic`       | `bool`    | False if uses `current_timestamp()`, `rand()`, etc. |
| `is_convergence_point`   | `bool`    | True if this is where control-flow branches merge |
| `convergence_inputs`     | `array`   | SSA branch output IDs converging here      |

#### Operations and Their Parameters

| Operation           | What it does                      | Key `parameters`                        |
|---------------------|-----------------------------------|-----------------------------------------|
| `join`              | Equi-join on key columns          | `join_condition`, `join_type`           |
| `join_custom`       | Non-equi or complex join          | `join_condition`, `join_type`           |
| `filter`            | Row filtering (WHERE)             | `condition`                             |
| `select`            | Column projection                 | `columns` (array of expressions)        |
| `withColumn`        | Add or replace a column           | `column_name`, `expression`             |
| `withColumnRenamed` | Rename a column                   | `existing`, `new`                       |
| `groupBy_agg`       | GROUP BY with aggregations        | `group_columns`, `column_aliases`       |
| `orderBy`           | Sort rows                         | `columns`, `ascending`                  |
| `union`             | Combine DataFrames vertically     | (inputs are the two DataFrames)         |
| `unionByName`       | Union by column name              | (inputs are the two DataFrames)         |
| `distinct`          | Remove duplicates                 | (no special parameters)                 |
| `drop`              | Remove columns                    | `columns`                               |
| `crossJoin`         | Cartesian product                 | (second DataFrame in inputs)            |
| `cache` / `persist` | Caching hint                      | `storage_level`                         |
| `repartition`       | Repartition DataFrame             | `num_partitions`, `columns`             |
| `window`            | Window function application       | `window_spec`, `expression`             |
| `pivot`             | Pivot table operation             | `pivot_column`, `values`                |
| `unpivot`           | Melt/unpivot operation            | `id_columns`, `value_columns`           |
| `explode`           | Flatten array/map column          | `column`                                |

#### `inferred_input` vs `inferred_output`

- **`inferred_input`**: Columns the transformation consumes. Each has a
  `from_inputs` field listing which upstream nodes provided it.
- **`inferred_output`**: Columns the transformation produces. This is the
  schema **after** the operation runs.

For a `select`, `inferred_output` is a subset of `inferred_input`.
For a `withColumn`, `inferred_output` is `inferred_input` plus the new
column. For a `groupBy_agg`, `inferred_output` is only the group keys
and aggregation results.

---

## 4. Graph Topology and Traversal

### 4.1 How Edges Work

The ASG is a DAG. Edges are encoded in the `inputs` field of each
transformation. To traverse the graph:

**Forward (source to sink)**: Start from any `data_in` node. Find all
transformations where `inputs` contains that `in_NNN`. Follow the chain
through `tx_NNN` nodes. A `data_out` node's `source_id` points to the
final transformation in a chain.

**Backward (sink to source)**: Start from a `data_out` node. Follow
`source_id` to a `tx_NNN`. Read that transformation's `inputs` to find
its predecessors. Repeat until you reach `in_NNN` nodes.

### 4.2 Common Topologies

**Linear pipeline**:
```
in_001 -> tx_001 -> tx_002 -> tx_003 -> out_001
```

**Fan-in (join)**:
```
in_001 --+
         +--> tx_003 (join) -> tx_004 -> out_001
in_002 --+
```

**Fan-out (multiple outputs from same source)**:
```
             +--> tx_005 -> tx_006 -> out_001
in_001 -> tx_003 -+
             +--> tx_008 -> tx_011 -> out_002
```

**Diamond (join after independent processing)**:
```
in_001 -> tx_003 -> tx_004 --+
                              +--> tx_010 (join) -> out_002
in_009 ----------------------+
```

### 4.3 Determining a Column's Origin

To trace where a column comes from:

1. Find the column in a `data_out.inferred_columns` or a
   transformation's `inferred_output`.
2. Look at the transformation's `inferred_input` for that column — the
   `from_inputs` field tells you which upstream node provided it.
3. Follow that node's `inputs` recursively until you reach a `data_in`.
4. The column in `data_in.inferred_columns` is the origin.

If a column appears in `inferred_output` but NOT in `inferred_input`, it
was **created** by that transformation (e.g., `withColumn`, aggregation
alias).

---

## 5. Relational Metadata

### 5.1 `column_relationships`

Relationships between columns across data sources, extracted from join
conditions.

| Field                   | Type     | Description                           |
|-------------------------|----------|---------------------------------------|
| `left_column`           | `string` | Column from left side of join        |
| `left_source`           | `string` | Source ID (`in_NNN`) of left column  |
| `right_column`          | `string` | Column from right side of join       |
| `right_source`          | `string` | Source ID of right column            |
| `relationship_type`     | `enum`   | `join_key`, `fk`, `same_domain`      |
| `join_type`             | `string` | `inner`, `left`, `right`, etc.       |
| `source_transformation` | `string` | Which `tx_NNN` defined this join     |

**Use case**: When generating synthetic data, columns linked by
`join_key` must share a common value domain to maintain referential
integrity.

### 5.2 `column_constraints`

Constraints extracted from filter/where conditions.

| Field                   | Type     | Description                         |
|-------------------------|----------|-------------------------------------|
| `column_name`           | `string` | Constrained column                 |
| `constraint_type`       | `enum`   | `equals`, `not_equals`, `gt`, `lt`, `gte`, `lte`, `in`, `not_null`, `is_null`, `between`, `like`, `rlike`, `enum` |
| `value`                 | `any`    | Constraint value or list           |
| `value_type`            | `string` | Inferred type of the value         |
| `source_transformation` | `string` | Which `tx_NNN` has this filter     |

**Use case**: When generating synthetic data, generated values must
satisfy these constraints for rows to survive the filter and produce
output.

### 5.3 `window_specs`

Window specifications defined as variables.

| Field           | Type      | Description                             |
|-----------------|-----------|-----------------------------------------|
| `scope`         | `string`  | Function where the spec is defined      |
| `variable_name` | `string`  | Variable name (e.g., `window_spec`)     |
| `pyspark_expr`  | `string`  | Original PySpark expression             |
| `sql_expr`      | `string?` | Resolved SQL equivalent (if available)  |

---

## 6. Control Flow

### 6.1 `control_nodes`

Represent branching, loops, and error handling.

| Field               | Type      | Description                                    |
|---------------------|-----------|------------------------------------------------|
| `node_id`           | `string`  | Unique ID (`ctrl_NNN`)                        |
| `control_type`      | `enum`    | `BRANCH` (if/else), `LOOP` (for/while), `PROTECTED` (try/except), `SCOPED` (with) |
| `logic`             | `object`  | `{expression, resolved_expression}`            |
| `branches`          | `array`   | Each branch: `{label, condition, steps, sub_controls}` |
| `exit_strategy`     | `enum`    | `MERGE`, `INDEPENDENT_SINK`, `TERMINATE`       |
| `loop_type`         | `enum?`   | `CODE_GENERATION`, `DATA_ITERATION`, `TABLE_ITERATION` |
| `is_unrollable`     | `bool`    | True if loop iterates over a static known list |
| `convergence_point` | `string?` | ID of the first node after branches converge   |
| `branch_outputs`    | `array?`  | IDs of each branch's final transformation      |

**`exit_strategy`** is critical for understanding data flow:
- `MERGE`: All branches assign to the same variable (common pattern).
  The `convergence_point` is where code resumes.
- `INDEPENDENT_SINK`: Each branch writes to a different output. No merge.
- `TERMINATE`: A branch ends execution (raise, return, sys.exit).

---

## 7. Quality and Diagnostics

### 7.1 `parsing_report`

Summary of the extraction process.

| Field                   | Type  | Description                           |
|-------------------------|-------|---------------------------------------|
| `total_files`           | `int` | Files found in workload               |
| `databricks_notebooks`  | `int` | How many are Databricks notebooks     |
| `python_scripts`        | `int` | How many are plain Python files       |
| `scala_files`           | `int` | How many are Scala files              |
| `syntax.ok`             | `int` | Files parsed without errors           |
| `syntax.corrected`      | `int` | Files with auto-corrected syntax      |
| `syntax.errors`         | `int` | Files that failed to parse            |
| `understanding.ok`      | `int` | Files whose pipeline was understood   |
| `understanding.errors`  | `int` | Files where extraction failed         |

### 7.2 `warnings`

Structured issues found during extraction.

| Field             | Type      | Description                                |
|-------------------|-----------|--------------------------------------------|
| `code`            | `string`  | Warning code (e.g., `W001`, `W_PAR_001`)   |
| `severity`        | `enum`    | `info`, `warning`, `error`                 |
| `node_id`         | `string?` | Related ASG node                           |
| `message`         | `string`  | Human-readable description                 |
| `suggested_action`| `string?` | Recommended fix                            |

---

## 8. Common AI Inference Patterns

### 8.1 "What data does this workload read and write?"

Read `data_in` for inputs and `data_out` for outputs. Use `name` and
`type` to identify each. Filter out entries where `is_test_file = true`
to focus on production I/O.

### 8.2 "What columns does source X have?"

Read `data_in[].inferred_columns` for the source with the matching `name`
or `id`. Sort by `confidence` (high > medium > low) to prioritize
reliable inferences. The `inferred_type` gives the data type.

### 8.3 "How are two sources related?"

Check `column_relationships`. Each entry tells you which columns are
join keys between which sources, and what type of join connects them.

### 8.4 "What constraints must test data satisfy?"

Read `column_constraints`. These are filter conditions extracted from the
code. If a filter says `col('amount') > 500`, test data must include rows
with `amount > 500` for them to survive the filter and produce output.

### 8.5 "What is the full lineage of output column X?"

1. Find the output in `data_out` and note its `source_id`.
2. Find that transformation in `transformations`.
3. Check if column X is in `inferred_output`. If yes, check
   `inferred_input` for the same column. If it is NOT in `inferred_input`,
   this transformation created it (e.g., via `withColumn`).
4. If it IS in `inferred_input`, follow `from_inputs` backward.
5. Repeat until you reach a `data_in` node.

### 8.6 "Is this workload a single pipeline or multiple?"

Count `data_out` entries. If there is more than one, the workload likely
has multiple output branches. Check the transformation DAG topology.
Fan-out from a shared transformation means branching from a common
intermediate result.

### 8.7 "What functions does this workload define and call?"

Read `functions` for definitions and `execution_calls` for invocations.
The `bindings` in `execution_calls` show how DataFrames flow through
function boundaries. `execution_instances` show complete execution paths
from entry points.

### 8.8 "What type of join connects sources A and B?"

Find the `column_relationships` entry where `left_source` and
`right_source` match the two source IDs. The `join_type` field gives you
`inner`, `left`, `right`, etc. The `left_column` and `right_column` are
the join keys.

### 8.9 "Which columns are created (not from source data)?"

Scan all transformations. For each `withColumn` operation, the
`parameters.column_name` is a newly created column. For `groupBy_agg`,
the `parameters.column_aliases` are new aggregation columns. These
columns exist only as computed results and have no source data to read.

### 8.10 "Is this column used anywhere downstream?"

Search all transformations' `inferred_input` arrays for the column name.
If found, check the `from_inputs` to confirm it comes from the expected
source. The `usage_count` on `data_in.inferred_columns` also indicates
how often a column is referenced.

---

## 9. Key Design Principles

1. **IDs are stable references**: `in_001`, `tx_003`, `out_007` are
   consistent within an ASG and used across all cross-references.

2. **`inputs` is the universal edge**: Every transformation's `inputs`
   array is the definitive source of graph connectivity.

3. **Inferred over explicit**: Most schemas come from `inferred_columns`
   (code analysis) rather than `columns` (explicit StructType). Always
   check both, preferring `columns` when present.

4. **Confidence is graded**: `high` > `medium` > `low`. Higher confidence
   means the column was seen in more authoritative context (schema
   definition > join key > naming convention).

5. **The `logic` field is human-readable**: It contains the original Spark
   expression as a string. It is useful for display but should not be
   parsed programmatically. Use `parameters` for structured access.

6. **Test artifacts are flagged**: `is_test_file = true` on any node means
   it came from test code. Filter these out for production analysis.

7. **Runtime values use the `runtime:` prefix**: When a value could not be
   resolved statically (e.g., `runtime:config.get('table_name')`), it
   means the actual value depends on execution context.

8. **The ASG is language-agnostic in structure**: The same JSON schema
   represents PySpark and Scala Spark workloads. The `operation` values
   and `parameters` keys are normalized across both languages.
