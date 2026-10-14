# Entrypoints `source` Field — Hybrid Format Specification

The `source` and `adapted_source` fields in `entrypoints.json` follow the same hybrid composite format. This document defines the format, its rationale, and how each DVP skill uses it.

## Format

```
<relative_path>:<lineno>(::segment)*
```

When `::` segments are present, the **last** segment is always the `method` (the callable). Any preceding segments form the `scope` (a list of containing classes/objects).

### Part 1 — `file:line` (physical location)

The universal format adopted by C/GCC compilers, Python tracebacks, and grep. Any IDE (VS Code, IntelliJ, PyCharm) resolves the reference with a single click (Ctrl+Click) and navigates directly to the exact line.

### Part 2 — `::Scope::method` (logical identification)

The `::` operator is the Scope Resolution Operator from C++, Rust, and PHP. Pytest adopts it natively to filter test execution:

```bash
pytest file.py::TestClass::test_method
```

By using `::` in `source`, the JSON speaks Pytest's native language. A consumer only needs to check whether `source` contains `::` to know there is a specific callable to invoke.

### Part 3 — Fully Qualified Name (unambiguous identification)

Convention from the Language Server Protocol (LSP) and Java/Scala. Required in Scala because a single file may contain multiple `object` definitions each with their own `main`. Without the `::` hierarchy, the reference would be ambiguous.

## Comparison with other formats

| Format | Example | Problem for DVP |
|--------|---------|-----------------|
| Dot Notation | `pkg.mod.Class.method` | Conflicts with relative file paths |
| URI Format | `file:///path#L10` | Too verbose, hard to pass to Pytest |
| GDB Style | `file.c:function` | No support for nested scope hierarchy |
| **DVP Style** | `file.py:40::Class::main` | **Pytest-ready, IDE-friendly, unambiguous** |

## Format by entrypoint type

### Python — Script with `__main__`

```json
{
  "name": "pipeline_mrb",
  "source": "mrb_spark/src/main/pipeline.py:40",
  "type": "script",
  "reason": "main_guard"
}
```

- `source`: `<path>:<lineno>` — the exact line where `if __name__ == '__main__':` appears, extracted via AST during parsing.
- No `::` scope — the entrypoint is the whole file activated by the guard. There is no specific method to invoke.

### Scala — Object with `def main`

```json
{
  "name": "GlobalTransactions",
  "source": "src/main/scala/GlobalTransactions.scala:5::GlobalTransactions::main",
  "type": "module",
  "reason": "main_method"
}
```

- `source`: `<path>:<lineno>::<Object>::main` — the line of `def main` plus the full scope hierarchy.
- With `::` scope — uniquely identifies the method within the file. Required because a single `.scala` file can contain multiple `object` definitions.

### Databricks Notebook

```json
{
  "name": "ingest_notebook",
  "source": "notebooks/ingest_notebook.py:1",
  "type": "databricks_notebook",
  "reason": "notebook"
}
```

- `source`: always `<path>:1`. A notebook is executed sequentially from the first cell to the last; there is no internal "entry method". Line 1 is correct by convention.
- No `::` scope — the notebook itself is the entrypoint in its entirety.

## The `adapted_source` field

After `dvp-code-adapter` transforms the code for testability, the callable may differ from what was originally detected. The `adapted_source` field records the **post-adaptation invocation target** using the same hybrid format.

### When `adapted_source` is set

`dvp-code-adapter` writes `adapted_source` on every entrypoint it processes. The field tells downstream skills (especially `dvp-test-setup-generator`) exactly what to invoke.

### Semantics

| Field | Set by | Meaning |
|-------|--------|---------|
| `source` | `dvp-entrypoint-identifier` | Where the entrypoint was **detected** (original code) |
| `adapted_source` | `dvp-code-adapter` | What to **invoke** after adaptation |

### Consumer rule

```
if adapted_source exists:
    parse adapted_source → extract function (last :: segment)
    use that function for test invocation
else:
    code-adapter has not run yet (or entrypoint was disabled)
```

If `adapted_source` contains `::` → there is a callable function (import and call it).
If `adapted_source` has no `::` → execute the file as a script (the entrypoint is the whole file).

### Adaptation cases

#### Case A — `__main__` delegates to existing function

Original code already has `def main()` that `__main__` calls.

```json
{
  "source": "workload.py:217",
  "reason": "main_guard",
  "adapted_source": "workload.py:163::main"
}
```

The adapter injected `spark` parameter into `def main()`. The `adapted_source` points to the function's actual line.

#### Case B — `__main__` has session + function delegation

`__main__` creates a session and calls one or more functions.

```python
if __name__ == "__main__":
    spark = get_spark_session("DailyReport")
    try:
        generate_daily_report(spark)
    finally:
        spark.stop()
```

```json
{
  "source": "jobs/daily_report.py:33",
  "reason": "main_guard",
  "adapted_source": "jobs/daily_report.py:10::generate_daily_report"
}
```

The adapter injected `spark=None` into the existing function's signature. The `__main__` block was simplified.

#### Case C — `__main__` has inline logic (no function)

All orchestration logic is inside the `__main__` block.

```python
if __name__ == "__main__":
    spark = create_spark_session()
    input_dfs = read_inputs(spark)
    output_dfs = run_pipeline(spark, input_dfs)
    write_outputs(spark, output_dfs)
    spark.stop()
```

```json
{
  "source": "workload.py:291",
  "reason": "main_guard",
  "adapted_source": "workload.py:295::main_entrypoint"
}
```

The adapter **extracted** the inline logic into a new `def main_entrypoint(spark=None):` function, and reduced `__main__` to `main_entrypoint()`.

Naming: Python uses `main_entrypoint` (snake_case), Scala uses `mainEntrypoint` (camelCase). If the name collides with an existing symbol, append an incrementing suffix: `main_entrypoint_02`, `main_entrypoint_03`, etc.

#### Scala — Object method

```json
{
  "source": "App.scala:5::GlobalTransactions::main",
  "reason": "main_method",
  "adapted_source": "App.scala:5::GlobalTransactions::main"
}
```

Scala entrypoints already have a named method. The adapter injected the session parameter; the invocation target stays the same.

#### Notebook (converted to script)

```json
{
  "source": "ingest_notebook.py:1",
  "reason": "notebook",
  "adapted_source": "ingest_notebook.dbx.py:15::run"
}
```

Notebooks are converted to scripts by `dvp-notebook-to-script`. The `run()` wrapper function is the adapted invocation target.

## Parsing the format

A single parser handles both `source` and `adapted_source`:

```python
def parse_source(value: str) -> dict:
    """Parse hybrid source format into components.

    Returns dict with keys: file, lineno, scope (list, optional), method (str, optional).
    Last :: segment is always method; preceding segments form scope.

    Examples:
        "workload.py:134"                               -> {file, lineno}
        "workload.py:295::main_entrypoint"               -> {file, lineno, method}
        "App.scala:5::GlobalTransactions::main"           -> {file, lineno, scope: [...], method}
        "file.py:10::Outer::Inner::run"                  -> {file, lineno, scope: ["Outer","Inner"], method}
    """
    segments = value.split("::")
    file_lineno = segments[0].rsplit(":", 1)
    result = {"file": file_lineno[0], "lineno": int(file_lineno[1])}
    qualname = segments[1:]
    if qualname:
        result["method"] = qualname[-1]
        if len(qualname) > 1:
            result["scope"] = qualname[:-1]
    return result
```

## Which skills produce and consume `source`

| Skill | Produces | Consumes | Field |
|-------|----------|----------|-------|
| `dvp-entrypoint-identifier` | Yes | — | `source` |
| `dvp-code-adapter` | Yes | `source` | `adapted_source` |
| `dvp-test-setup-generator` | — | `adapted_source` (fallback: `source`) | invocation target |
