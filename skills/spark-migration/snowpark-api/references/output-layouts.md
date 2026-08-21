# SMA Output Layouts (v1 / v2 / v3) and Resolution Rules

The SMA CLI produces three different on-disk layouts depending on version. This reference defines how `migrate-pyspark-to-snowpark-api` and `validate-pyspark-to-snowpark-api` resolve `<output>` to the **workload root** (the directory containing `Output/` and `Reports/`).

> The detection algorithm itself lives in `dvp/dvp-orchestrator/SKILL.md` Step 1 and is reused verbatim — this reference is the human-readable spec.

## v1 — Timestamped `Conversion-*` Folder

SMA CLI (v1) creates a new timestamped folder **inside** `<output>` per run:

```
<output>/
  Conversion-MM-DD-YYYY-THH-MM-SS/
    Output/
    Reports/
      Issues.csv
      InputFilesInventory.csv
      ArtifactDependencyInventory.csv
    Logs/
  .snowma                            (project metadata, optional)
```

**Resolution rule.** Resolve `<output>` to the **most recent** `Conversion-*` child folder (alphabetic max = newest timestamp). All subsequent steps work **inside** that folder.

```bash
ls -d "<output>"/Conversion-* 2>/dev/null | sort | tail -1
```

⛔ **Do NOT copy `Output/`, `Reports/`, or `Logs/` up to the parent directory.** Always work inside the resolved `Conversion-*` folder.

If `.snowma` is present, `internalConversionOutputPath` inside it is the canonical resolved path.

## v2 — Flat `sma-output/`

SMA v2 writes directly to a sibling `sma-output/` directory:

```
<output>/
  sma-output/
    Output/
    Reports/
  .snowct                            (project ID only — no paths)
```

**Resolution rule.** If `<output>/sma-output/` exists, set `<output>` = `<output>/sma-output/`. `.snowct` carries no useful paths.

## v3 — Dual Conversion Folders

SMA v3 supports running both the Snowpark API and Snowpark Connect converters against the same project; outputs are namespaced:

```
<output>/
  Conversion_SnowparkAPI/
    sma-code-process-<timestamp>/
      Output/
      Reports/
  Conversion_SnowparkConnect/
    sma-code-process-<timestamp>/
      Output/
      Reports/
  .snowct
```

**Resolution rule.** Pick the directory matching the project's `conversion_type`:

| `conversion_type` | Folder |
|---|---|
| `snowpark-api` (canonical) | `Conversion_SnowparkAPI` |
| `snowpark-connect` (canonical) | `Conversion_SnowparkConnect` |

Inside the chosen folder, set `<output>` to the **most recent** `sma-code-process-*` child.

```bash
ls -d "<output>/Conversion_SnowparkAPI/sma-code-process-"* 2>/dev/null | sort | tail -1
```

## Detection Order

When validating an `already_migrated` input, check in this order:

1. `.snowma` in `<output>` → v1, use `internalConversionOutputPath`
2. `<output>/sma-output/` exists → v2
3. `<output>/Conversion_Snowpark*` with `sma-code-process-*` children → v3
4. Legacy `<output>/Conversion-*` siblings → v1 (no `.snowma`)
5. Ask the user

Names `Conversion_SnowparkAPI` and `Conversion_SnowparkConnect` are **v3-only** — never treat them as legacy v1 timestamp folders.

## Validation

After resolution, both of these must exist:

```bash
test -d "<output>/Output" && test -d "<output>/Reports"
```

If `Issues.csv` is missing, the conversion silently failed — stop and ask the user to re-run SMA.
