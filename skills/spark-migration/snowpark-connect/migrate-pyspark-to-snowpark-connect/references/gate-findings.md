# Gate Findings — Code Lookup

> **Mandatory on any gate FAIL.** The coordinator's *Universal Gate Contract*
> requires looking up every `gaps[].code` here **before** re-dispatching a
> specialist. The code — not the message text — is the contract; several codes are
> **not** fixable by a specialist at all, and re-dispatching those wastes a full
> pass and can regress correct work.
>
> Not needed on a PASS.

## How to use this

1. Read the `gaps[].code` values from the gate's JSON stdout.
2. Look each one up below. The **Owner** column says who can actually fix it.
3. Only re-dispatch a specialist for codes owned by *fixer* / *analyzer* / *reporter*.
   For **coordinator**-owned codes you ran a step wrong or skipped it — fix that
   instead. For **source**-owned codes the customer's input is at fault; escalate.

| Owner | Meaning |
|-------|---------|
| `fixer` | Re-dispatch `agents/fixer.md` in targeted mode with these gaps as `TARGET_ISSUES`. |
| `analyzer` | Re-run Phase 1 analysis / append the missing supplementary entries. |
| `reporter` | Re-run the relevant reporter section. |
| `coordinator` | **You** skipped or misran a phase. Re-run that step — a specialist cannot fix this. |
| `source` | Pre-existing defect in the customer's code or export. Escalate; do not fake a fix. |

## Fixer gate (`scos_gates.py fixer`)

| Code | Sev | Owner | Action |
|------|-----|-------|--------|
| `convertible_not_converted` | CRITICAL | fixer | A known rewrite exists but was left `resolution="todo"`. Apply the rewrite. **See caveat below.** |
| `high_risk_unmarked` | CRITICAL | fixer | High-risk issue has no fix, no `# SCOS` marker, and no `analysis.json` resolution. Give it a real fix **or** an honest `# SCOS-TODO:` plus a matching resolution. |
| `safe_without_reason` | CRITICAL | fixer | Marked `resolution="safe"` with no `resolution_reason`. Justify it or reclassify. |
| `mustfix_error_unresolved` | CRITICAL | fixer | KB `Error`-tier op still live, unresolved, unmarked. Convert, or comment out + TODO. |
| `unsupported_construct_live` | CRITICAL | fixer | Label-independent AST check: an unconditionally unsupported construct is live in the output. Convert or comment out. |
| `fixed_but_unchanged` | CRITICAL | fixer | Marked `fixed` but byte-identical to baseline and still unsupported. Actually edit the code. |
| `sql_mechanical_not_rewritten` | CRITICAL | coordinator | A mechanical SQL gap is only annotated. Re-run the Phase 0.6 SQL rewrite. |
| `phase2_no_effort` | CRITICAL | fixer | `issues_fixed=0` while actionable issues remain — the fixer did nothing. |
| `phase2_not_orchestrated` | CRITICAL | **coordinator** | You improvised an inline fix on a multi-file workload. Run `orchestrate_phases.py --phase 2` and dispatch the waves. A fixer cannot fix this. |
| `empty_file` | CRITICAL | fixer | Migrated `.py` is 0 bytes — restore from baseline and redo. |
| `syntax_error` / `notebook_cell_syntax_error` | CRITICAL | fixer | Output does not compile. Fix or let Phase 2b revert it. |
| `invalid_notebook` | CRITICAL | fixer | `.ipynb` is not well-formed JSON. |
| `manifest_file_missing` | CRITICAL | **coordinator** | A manifest file is absent from `Output/`. Coverage problem — do not advance; escalate. |
| `preexisting_syntax` | WARN | **source** | Broken in the customer's source; Phase 0.5 could not auto-repair. Advisory — **not** fixer-caused. Do not re-dispatch. |
| `fix_reverted` | WARN | — | Phase 2b reverted a non-compiling file to baseline. Advisory record. |
| `noop_over_annotation` | WARN | — | A no-op method carries a `# SCOS` annotation. Cosmetic; do not block. |
| `recipe_adjacent_unmarked` | WARN | fixer | `recipe_adjacent` issue unannotated; recipe-coverage mining needs it. |

## Analyzer gate (`scos_gates.py analyzer`)

| Code | Sev | Owner | Action |
|------|-----|-------|--------|
| `analysis_missing` | CRITICAL | coordinator | `analysis.json` absent — Phase 1 did not run. |
| `analysis_invalid_json` | CRITICAL | analyzer | Not a valid JSON array. |
| `no_py_files` | CRITICAL | coordinator | Nothing found under `migrated_dir` — Phase 0 copy failed. |
| `empty_analysis_with_pyspark` | CRITICAL | analyzer | 0 issues but source imports pyspark — likely analyzer failure. |
| `all_low_risk_with_red_flags` | WARN | analyzer | All risks < 0.1 despite `sparkContext`/`.rdd`/`broadcast` — likely false negatives. |

## Reports gates (`--section assessment` / `--section csvs`)

| Code | Sev | Owner | Action |
|------|-----|-------|--------|
| `missing_html` / `missing_ir` | CRITICAL | reporter | Assessment output absent. Re-run Section A. |
| `unrendered_jinja` | CRITICAL | reporter | HTML has unsubstituted `{{` / `{%`. |
| `missing_csv` | CRITICAL | reporter | A required CSV is absent. Re-run Section B. |
| `issues_no_data` | CRITICAL | reporter | `Issues.csv` is header-only. |
| `inventory_no_data` | CRITICAL | reporter | `InputFilesInventory.csv` has no data rows. |
| `wrong_ewi_prefix` | CRITICAL | reporter | Data rows but no `SPRKCNTPY` codes — wrong language prefix. |
| `unexpected_columns` | WARN | reporter | `Issues.csv` header lacks expected columns. |

## Imports / headers gate (Phase 3)

| Code | Sev | Owner | Action |
|------|-----|-------|--------|
| `missing_header` | CRITICAL | coordinator | No SCOS header in first 15 lines — re-run `update_imports.py`. |
| `stub_header` | CRITICAL | coordinator | Placeholder header: Phase 3 was skipped. Re-run `update_imports.py`. |
| `missing_snowpark_connect` | CRITICAL | coordinator | No file references `snowpark_connect`; entry-point init missing. |
| `spark_builder_in_code` | CRITICAL | coordinator | `SparkSession.builder` still live; session init not replaced. |
| `unsupported_import` | CRITICAL | fixer | An unsupported import remains. |

## Known false-positive caveat

`convertible_not_converted` fires on KB `status_class=Fixed`, which includes
**external-storage I/O** (e.g. S3 parquet paths). Repointing those needs the
customer's Snowflake stage name, which no agent can invent — so the gate demands a
rewrite that cannot honestly be performed and the retry loop **cannot converge**.
If a gap is external-storage I/O whose only blocker is an unknown stage name, do
**not** invent a stage and do **not** relabel to silence the gate: leave the honest
`# SCOS-TODO:`, record the reason, and escalate the residual gap to the user.

Also note: for Databricks notebooks the `lines` value in `analysis.json` is
**cell-relative**, so a reported `file:2` can match several unrelated rows. Match on
the gap's `code` + surrounding code text, not the line number alone.

## Detailed rationale (verbatim from the pre-split SKILL.md)

The next three checks are **KB-`status_class`-driven and family-agnostic** (not RDD-only): they read each `analysis.json` issue's tier from `kb_rules.json` — `Fixed` (a rewrite exists) / `Error` (unsupported, no auto-fix) / `Warning` (behavioral, advisory) / `IO` (repoint) — plus `rdd_class` for the RDD detector (which uses that field instead of `status_class`). So they enforce Delta, dbutils, streaming, SQL, etc. the same way they enforce RDD.

**Convertible left as TODO (`convertible_not_converted`)**: FAILS if an issue that **has a known rewrite** — `rdd_class` `convertible`/`mixed`, **or** KB `status_class=Fixed` — is left as `resolution="todo"`. A rewrite exists, so it must be applied (per `references/`), not annotated as a TODO. (Genuine `no_equivalent` / `Error`-with-no-fix ops are exempt — TODO + comment-out is correct for them.)

**Error-tier left live & unhandled (`mustfix_error_unresolved`)**: FAILS if a KB `Error`-tier incompatibility (any family — Delta/dbutils/streaming/…) is **still live** in the output (its code survives unchanged), is **not resolved** (`fixed`/`safe`), and carries **no `# SCOS` marker** — i.e. the fixer neither converted nor honestly flagged it. The behavioral config family (`spark.conf.set`, ~6 rules) is excluded (they run and may be kept). If the op has no equivalent, comment it out + TODO; otherwise convert it.

**Ground-truth: unsupported construct still live (`unsupported_construct_live`)**: the strongest, **label-independent** check — it ignores `resolution` entirely (which is self-reported and gameable via *any* label: `fixed`/`safe`/`todo`/`None`) and scans the migrated **output** (AST-based, so comments/docstrings never false-positive) for constructs that are **unconditionally unsupported** in Spark Connect: attribute gateways sourced from `kb_rules.json` (`kind=python_attribute`, Error — `rdd`, `sparkContext`, `readStream`, `writeStream`, `streams`, `_metadata`) and the no-DataFrame-homonym RDD/SparkContext methods (`parallelize`, `emptyRDD`, `textFile`, `glom`, `reduceByKey`, `mapPartitions`, …). Any such call **live** in executable code FAILS the gate — it raises at runtime regardless of the label (this is what catches a `sc = spark.sparkContext` left live with only a recipe annotation, or a `sc.parallelize(...)` marked `fixed` without an edit). It needs no execution/data (the failure is data-independent — missing API surface), so it is a cheap static check, not the runtime validate step. Re-dispatch the fixer to convert (or comment out + TODO) the named lines.

**Anti-gaming: `fixed`-but-unchanged (`fixed_but_unchanged`)**: because `resolution` is self-reported, the gate does not trust it — it verifies ground truth. It FAILS if an issue marked `resolution="fixed"` has code **byte-identical to the pre-Phase-2 baseline** (`phase-1-complete`, comments/whitespace ignored) that **still contains an unsupported RDD/SparkContext construct** (`sc.<entry>`, `.sparkContext`, an RDD-exclusive method). That means the fixer labelled the issue done without editing the code — it will crash at runtime despite compiling and despite the `fixed` label. (The construct requirement keeps this precise: an unchanged line that is genuinely fine — e.g. `.randomSplit`/`.sample`, valid on a DataFrame once its source was converted upstream — or a docstring, does not trip it.) Re-dispatch the fixer to actually rewrite the code.

**Orchestration enforcement (`phase2_not_orchestrated`)**: for any workload with **≥ 2** code files, the gate FAILS if `migration_state.json` has no orchestrator plan (`max_parallel_fixers` + `phase2_chunks`). This is the deterministic guard against the coordinator *improvising* an inline single-agent fix and silently bypassing the parallel fixer pool. If you see this finding, you skipped `orchestrate_phases.py` — run it, dispatch the printed waves, then re-run the gate.
