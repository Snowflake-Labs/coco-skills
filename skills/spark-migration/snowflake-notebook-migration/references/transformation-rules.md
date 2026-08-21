# Transformation Rules Registry

Single source of truth for incompatible-pattern rules applied by the `snowflake-notebook-migration` skill. Consumed by both standalone (`references/standalone-mode.md`) and orchestrator (`references/orchestrator-mode.md`) modes.

## Contract

To add or change a rule, append or edit a row in the table below. Do not touch `SKILL.md` or the mode reference files — they are pure orchestrators.

Columns:

- `pattern` — concrete token, call, or structural match (e.g. `%md`, `dbutils.widgets.text(...)`, `display(df)` where `df` is a PySpark DataFrame).
- `category` — one of `magic`, `dbutils`, `display`, `sql_ref`, `unsupported`, `ignored`.
- `action` — what the skill does when it matches.
- `preserves` — what is kept as-is (variable names, imports, comments, surrounding code).
- `notes` — caveats, anti-patterns, links to examples.

## Core principle (reminder)

This is a migration, not a rewrite. If a cell has no matching rule, copy it as-is. If a cell mixes compatible and incompatible lines, apply rules only to the incompatible lines and leave everything else untouched. If the entire cell is covered by an unsupported rule, convert the cell to a markdown migration note that preserves the original code in a fenced block (see [Migration Note Format](#migration-note-format)).

## Rules

| # | pattern | category | action | preserves | notes |
|---|---------|----------|--------|-----------|-------|
| 1 | `%md` (first line of cell) | magic | Convert cell to `cell_type: "markdown"`. Remove the `%md` line. | Remaining cell content unchanged. | Only the directive is stripped. |
| 2 | `%sql` (first line of cell) | magic | Convert cell to `cell_type: "sql"`, `language: "sql"`. Remove the `%sql` line. Add `resultVariableName` metadata with a meaningful name. | Remaining SQL body unchanged. | See rule 8 for `_sqldf` handling in downstream cells. |
| 3 | `%scala` (first line of cell) | magic | Convert cell to markdown migration note with the original code in a fenced block. | Original code preserved inside the fenced block. | Not supported in Snowflake Workspace notebooks. |
| 4 | `%r` (first line of cell) | magic | Convert cell to markdown migration note with the original code in a fenced block. | Original code preserved inside the fenced block. | Same treatment as `%scala`. |
| 5 | `%sh` (first line of cell) | magic | Convert cell to Python (`cell_type: "code"`). Remove `%sh`. Prefix each command line with `!`. | Command text, order, and comments preserved. | Shell availability in Snowflake Workspace is limited — owner may need to rewrite for Python equivalents. |
| 6 | `%fs` (first line of cell) | magic | Convert cell to markdown migration note. Suggest Snowflake stage equivalents in the note. | Original commands preserved inside the fenced block. | DBFS does not exist in Snowflake. |
| 7 | `%run ./path` | magic | Keep as-is; rewrite the path to target the post-conversion filename following the File Naming Convention in `SKILL.md`. `config.py` → `%run ./config.py.ipynb`. Existing `.ipynb` targets are unchanged. | Cell as a whole; only the path is edited. | Do NOT inline the referenced notebook's code. If the target file does not exist at the expected path, leave the path and add an inline migration note. |
| 8 | `_sqldf` reference in a Python cell | sql_ref | Apply the `_sqldf` migration procedure — see [SQL cell result type](#sql-cell-result-type) below. | Surrounding Python code; the original variable name. | Snowflake's SQL cell result is **Snowpark on Notebook Runtime ≥ 2.6, pandas on < 2.6**, so behavior is runtime-dependent. |
| 9 | `{{sql_var_name}}` (or absence of it) in a SQL cell that references another SQL cell's result | sql_ref | Use Jinja `{{variable_name}}` to reference the upstream SQL cell's `resultVariableName`. | Rest of the SQL unchanged. | Only applies to SQL-to-SQL cross-references. SQL-to-SQL Jinja is the lowest-risk vector (the substituted value is a controlled `resultVariableName`, not arbitrary input), but Jinja IS string substitution — see [`snowflake-notebooks` skill § Referencing Variables Between Cells › Python to SQL (Jinja templating)](../../../../snowflake-notebooks/SKILL.md#referencing-variables-between-cells) for the full Jinja injection note that also applies to any `{{var}}` slot the migration introduces from Databricks widget/parameter inputs. |
| 10 | `dbutils.widgets.text(...)`, `dbutils.widgets.dropdown(...)`, `dbutils.widgets.get(...)`, `dbutils.widgets.multiselect(...)`, `dbutils.widgets.combobox(...)`, `dbutils.widgets.getArgument(...)`, `dbutils.widgets.removeAll()`, `dbutils.widgets.*` | dbutils | Replace with `sys.argv` parameters with hardcoded fallback defaults. Use the notebook's own filename to detect whether arguments were passed: `_NOTEBOOK_NAME = "<notebook_filename>.ipynb"; _has_args = os.path.basename(sys.argv[0]) == _NOTEBOOK_NAME; param = sys.argv[1] if _has_args and len(sys.argv) > 1 else "default_value"`. Replace each `dbutils.widgets.get("name")` / `dbutils.widgets.getArgument("name")` with the corresponding variable. Remove all `dbutils.widgets.text/dropdown/multiselect/combobox/removeAll` setup calls. | Variable names referenced by downstream cells. | Do NOT convert the whole cell to markdown — downstream cells likely depend on the variable. `ipywidgets` support for interactive UI controls is planned for Workspace notebooks; once available, widgets can be converted to `ipywidgets` equivalents. |
| 11 | `dbutils.notebook.run(...)` | dbutils | Convert to markdown migration note suggesting `%run` (for variable sharing) or a Snowflake Task DAG with `EXECUTE NOTEBOOK` (for orchestration). | Original call inside the fenced block. | `%run` only covers the variable-sharing use case, not the return-value use case. Flag both options in the note. |
| 12 | `dbutils.notebook.exit(...)` | dbutils | Convert to `%notebook_exit`. Do NOT replace with `raise SystemExit()`. | Surrounding code. | `%notebook_exit` is the Snowflake Workspace equivalent. |
| 13 | `dbutils.secrets.get(scope=..., key=...)` | dbutils | Comment out the call in place. Assign a placeholder value (`None` or `"TODO"`) so downstream cells that reference the variable continue to resolve. Add an inline comment noting the owner must migrate to Snowflake Secrets. | All other lines in the cell, including the variable name bound to the secret. | Do NOT convert the entire cell to markdown — downstream cells depend on the variable. |
| 14 | `dbutils.fs.ls(...)`, `dbutils.fs.cp(...)`, `dbutils.fs.rm(...)`, `dbutils.fs.*` | dbutils | Convert the cell (or just the incompatible lines) to a markdown migration note. For `fs.ls`: simple local file listing → suggest `!ls`; cloud storage patterns (recursive listing, attribute filtering, paths like `dbfs:/`, `s3://`, `/mnt/`) → suggest `session.sql(f"LIST @stage PATTERN = '...'").collect()` and note: DBX `fs.ls` is single-level (needs manual recursion), SF `LIST` is flat (no recursion needed); attributes differ — `file_info.name`/`.path` → `row["name"]` (includes full path), `.modificationTime` (epoch ms) → `row["last_modified"]` (date string), `.isDir()` → filter by trailing `/`. For `fs.cp`: suggest `PUT` (upload to stage) or `GET` (download from stage). For `fs.rm`: suggest `REMOVE @stage/<path>`. | Original commands inside the fenced block. | DBFS does not exist in Snowflake. `!ls` is valid for local workspace files only. |
| 15 | `display(df)` where `df` is a PySpark or Snowpark DataFrame | display | Replace with `df.show()`. | Variable name and surrounding code. | Do NOT leave a bare `df` — lazy DataFrames print only the schema string, not the data. |
| 16 | `display(pdf)` where `pdf` is a pandas DataFrame | display | Replace with a bare `pdf` as the last expression of the cell. | Variable name and surrounding code. | Snowflake Workspace notebooks render pandas DataFrames when they are the final expression. |
| 17 | `display(plt.gcf())` and similar matplotlib figure calls | display | Replace with `plt.show()`. | Surrounding matplotlib code. | Applies to any pyplot figure passed to `display`. |
| 18 | `from pyspark.rdd import ...`, `rdd.*`, `pyspark.RDD` usage | unsupported | Convert to markdown migration note; preserve original code in a fenced block. | Original code inside the fenced block. | RDD API is not provided by Snowpark Connect. |
| 19 | `pyspark.ml.*` usage | unsupported | Convert to markdown migration note; preserve original code in a fenced block. | Original code inside the fenced block. | ML library is not available. |
| 20 | `pyspark.streaming.*` usage | unsupported | Convert to markdown migration note; preserve original code in a fenced block. | Original code inside the fenced block. | Structured streaming is not available in Snowpark Connect. |
| 21 | `DataFrameWriter.jdbc(...)`, `DataFrameReader.orc(...)`, `DataFrameWriter.orc(...)` | unsupported | Flag each call with an inline migration note; preserve the call. | All surrounding code. | Do not silently remove — the owner must decide on the destination. |
| 22 | Iterator type in UDFs (`Iterator[...] -> Iterator[...]`) | unsupported | Flag with an inline migration note; preserve the UDF. | UDF body. | Iterator-typed UDFs are not supported. |
| 23 | Reads/writes against external databases (Redshift, RDS/MySQL, Postgres, etc.) | unsupported | Flag with an inline migration note; preserve the call. | All surrounding code. | The owner must migrate these to Snowflake connectors or stages. |
| 24 | Cloud-storage writes through custom libraries (e.g. S3 utility wrappers) | unsupported | Flag with an inline migration note; preserve the call. | All surrounding code. | Suggest Snowflake stages (`PUT`, `COPY INTO`) in the note. |
| 25 | Imports from custom utility libraries that are not in the migration scope | unsupported | Flag with an inline migration note; preserve the import and its usages. | All surrounding code. | Do not attempt to inline or rewrite custom utilities. |
| 26 | Hardcoded credentials or secret-reference constants | unsupported | Flag with an inline migration note; preserve the constant. | All surrounding code. | Recommend migration to Snowflake Secrets in the note. Do not scrub the value silently — the owner must decide. |
| 27 | `DataFrame.hint(...)` | ignored | No action. Leave the call untouched. | Entire call. | Snowpark Connect silently ignores this call; no behavioral effect. |
| 28 | `DataFrame.repartition(...)` | ignored | No action. Leave the call untouched. | Entire call. | Snowpark Connect silently ignores this call; no behavioral effect. |
| 29 | `%md-sandbox` (first line of cell) | magic | Same as rule 1. Convert cell to `cell_type: "markdown"`. Remove the `%md-sandbox` line. | Remaining cell content unchanged. | Databricks sandbox variant of `%md`; treated identically. |
| 30 | `%python` / `%py` (first line of cell) | magic | Remove the magic line. Keep the cell as `cell_type: "code"`. | All code in the cell unchanged. | Cell is already Python — the magic is redundant in Snowflake Workspace notebooks. |
| 31 | `%pip` (first line of cell) | magic | Keep as-is. No transformation needed. | Entire cell unchanged. | `%pip` is supported in Snowflake Workspace notebooks. |
| 32 | `%time` (first line of cell) | magic | Keep as-is. No transformation needed. | Entire cell unchanged. | IPython magic; supported in Snowflake Workspace notebooks. |
| 33 | `%load_ext` (first line of cell) | magic | Keep as-is. No transformation needed. | Entire cell unchanged. | IPython magic; supported in Snowflake Workspace notebooks. |
| 34 | `%environment` (first line of cell) | magic | Convert to `%env`. | Remaining cell content unchanged. | `%environment` is Databricks-specific; `%env` is the IPython equivalent supported in Workspace notebooks. |
| 35 | `dbutils.library.installPyPI(...)` | dbutils | Convert to `!pip install <package>`. Extract the package name from the call arguments. | Surrounding code in the cell. | Databricks library utility; `!pip install` is the Workspace equivalent. |
| 36 | `dbutils.library.restartPython()` | dbutils | Remove the call. | All other lines in the cell. | Not needed in Snowflake Workspace notebooks — kernel restarts are handled differently. |
| 37 | `dbutils.jobs.taskValues`, `dbutils.jobs.TaskValuesUtils.get(...)`, `dbutils.jobs.TaskValuesUtils.set(...)` | dbutils | Convert to markdown migration note; preserve original code in a fenced block. | Original code inside the fenced block. | Databricks job orchestration APIs with no direct Snowflake equivalent. Suggest Snowflake Task DAG for orchestration in the note. |
| 38 | `dbutils.secrets.list(...)`, `dbutils.secrets.listScopes(...)`, `dbutils.secrets.getBytes(...)` | dbutils | Flag with an inline migration note; preserve the call. | All surrounding code. | Non-`get` secrets APIs. No Snowflake equivalent yet. See rule 13 for `dbutils.secrets.get`. |
| 39 | `dbutils.fs.mount(...)`, `dbutils.fs.unmount(...)`, `dbutils.fs.mounts()`, `dbutils.fs.refreshMounts()` | dbutils | Convert to markdown migration note; preserve original code in a fenced block. | Original code inside the fenced block. | DBFS mounts do not exist in Snowflake. Suggest external volumes or stages in the note. |

## SQL cell result type

A Snowflake SQL cell binds its result to a variable whose **type depends on the Notebook Runtime version**:

| Runtime | Result type |
|---------|-------------|
| **≥ 2.6** (default) | `snowflake.snowpark.DataFrame` |
| **< 2.6** (legacy) | `pandas.DataFrame` |

### `_sqldf` migration procedure (referenced by rule 8)

1. **Rename**: replace `_sqldf` in the cell with the upstream SQL cell's `resultVariableName` (from rule 2).
2. **Strip**: remove any `.toPandas()` call immediately following the renamed variable. See [Why strip rather than rewrite](#why-rule-8-strips-topandas-rather-than-rewriting-it) below for the rationale.
3. **Scan**: search the rest of the cell against the [Pandas-only API trigger list](#pandas-only-api-trigger-list) below.
4. **Convert (conditional)**: only if a trigger matches *on the renamed variable*, insert the [Defensive conversion](#defensive-conversion-use-only-when-a-trigger-matches) snippet immediately before the first matching line, after any filter/aggregate/limit. If nothing matches, leave the result as-is — do not pre-emptively convert.
5. **Rewrite downstream references (only when step 4 inserted a conversion)**: in every pandas-only line at or after the inserted snippet, replace `<var>` with `<var>_pdf` so the pandas-only calls actually run on the materialized pandas frame, not on the still-Snowpark original. Concretely: `df = active_users; df.head()` after step 4 becomes `df = active_users_pdf; df.head()`. Without this rewrite the conversion is inserted but never used, and `active_users.head()` still raises `AttributeError` on runtime ≥ 2.6 (Snowpark has no `.head`). Leave references outside the affected block untouched — `<var>` remains the canonical source handle for anything that stays on Snowpark.

### Why rule 8 strips `.toPandas()` rather than rewriting it

The PySpark spelling `.toPandas()` (camelCase) does not exist on either Snowflake type — it raises `AttributeError` on Snowpark (which uses `.to_pandas()`, snake_case) and on pandas (which has no such method at all). A naive rewrite to `.to_pandas()` would also be wrong: on < 2.6 it still raises `AttributeError`, and on ≥ 2.6 it eagerly collects the whole result into kernel memory and defeats Snowpark's warehouse pushdown. The correct action is therefore to **strip the call entirely** and only re-introduce a controlled conversion where downstream code actually requires pandas.

### Pandas-only API trigger list

After stripping `.toPandas()`, scan the rest of the cell for any of these text patterns. If any match, the downstream needs a real pandas DataFrame:

- Positional indexers: `.iloc[`, `.loc[`, `.at[`, `.iat[`
- Row-wise computation: `.apply(`, `.applymap(`, `.map(` (there is no `Snowpark DataFrame.map` method — only a module-level `snowflake.snowpark.dataframe.map(df, func, ...)` function — so a `df.map(...)` method call is always pandas-Series-style or PySpark and is a real trigger)
- Conversion / array bridge: `.values`, `.to_numpy(`, `.astype(`, `np.asarray(`, `np.array(`
- pandas-native reshape: `.pivot_table(`, `.melt(`, `.stack(`, `.unstack(`
- pandas-native joins (top-level functions): `pd.merge(`, `pd.concat(`
- Library bridges: `sklearn.`, `scipy.`, `seaborn.`, `statsmodels.`

**Ambiguous triggers — verify intent before converting:**
- `.pivot(` — Snowpark has `df.group_by(...).pivot(pivot_col, values)`; only treat as pandas if the call shape is `.pivot(index=..., columns=..., values=...)`.
- `.head(`, `.tail(` — Snowpark `DataFrame` has no `.head` or `.tail` method (calling either raises `AttributeError`; the action methods that return `List[Row]` are `.first(n)` and its alias `.take(n)`). When migrated Databricks code calls `.head(n)` or `.tail(n)` on the SQL-cell variable — typically as the tail of `df.toPandas().head()` — the call works on pandas (< 2.6) but fails on Snowpark (≥ 2.6). Treat as a real trigger so the defensive conversion is inserted; the conversion makes the cell runtime-portable. (Not listed as a hard trigger because some downstream code legitimately wants Snowpark's `.first(n)`/`.take(n)` semantics; verify intent before mechanically converting.)

**Not** triggers (cross-compatible — work on both Snowpark and pandas):
- `df[["A", "B"]]` (list-of-columns selection — Snowpark routes through `.select(...)`)
- `df[<col_expression>]` (filter — Snowpark routes through `.filter(...)`)
- `df["A"]` (single column reference)

**Heuristic, not certainty.** Trigger matches are text occurrences in the cell body. Before inserting the defensive conversion, sanity-check that the matching call is actually on the SQL-result variable `<var>` (e.g. `<var>.iloc[...]`), not on some unrelated object (`np.array(other_list)`, `dict.get("k").apply(...)`, etc.). If the match is unrelated, no conversion is needed. If a call like `pd.concat([<var_a>, <var_b>])` mixes multiple SQL-result variables, each one needs its own conversion before the call.

If no trigger matches anything on `<var>`, the migration leaves the result as-is. The agent should NOT pre-emptively insert a conversion "just in case" — that re-introduces the eager-collect anti-pattern.

### Defensive conversion (use only when a trigger matches)

Insert this snippet **immediately before** the first pandas-only line, **after** any filter / aggregate / limit. Parameterize on the actual upstream variable name (preserve names — see [Core principle](#core-principle-reminder)); do not rename to a generic `df`:

```python
import pandas as pd
# Replace <var> with the upstream SQL cell's resultVariableName (e.g. active_users)
<var>_pdf = <var>.copy() if isinstance(<var>, pd.DataFrame) else <var>.to_pandas()
# ... pandas-only code below uses <var>_pdf instead of <var>
```

`<var>_pdf` is always a fresh frame — `.copy()` on the pandas branch matches the Snowpark branch (where `.to_pandas()` already returns a new frame), so mutations to `<var>_pdf` never leak back into `<var>`. Putting the conversion **after** reductions instead of at the top of the cell keeps Snowpark's pushdown intact on ≥ 2.6 and avoids pulling the entire SQL result into the kernel.

**Column-casing note:** Snowflake uppercases unquoted SQL identifiers, so the migrated `<var>_pdf` has uppercase column names (`pdf["TOTAL_ORDERS"]`) regardless of runtime — even if the original Databricks code referenced lowercase columns (`pdf["total_orders"]`). After inserting the defensive conversion, also update any downstream `<var>_pdf[<lowercase>]`, `<var>_pdf.<lowercase>`, or `<var>_pdf.loc[:, <lowercase>]` references to uppercase. Quoted identifiers in the original SQL (`"customerId"`) preserve case.

## Migration Note Format

When a rule's action is "convert to markdown migration note", use this exact template for the new markdown cell:

````markdown
> **Migration Note**: [Brief description of the issue]
> [Why it's incompatible with Snowflake]
>
> **Owner action required**: [What the owner needs to decide or do]

Original code:
```[language]
[original code preserved exactly]
```
````
