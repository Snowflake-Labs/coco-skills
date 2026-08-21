# Validation Report App

This app renders a completed Snowpark Connect validation run as an interactive browser view.

Launch with a specific validation directory:

```bash
uv run --project data-engineering/spark-migration/snowpark-connect \
  streamlit run data-engineering/spark-migration/snowpark-connect/validate-pyspark-to-snowpark-connect/scripts/report/validation_report_app.py \
  -- --run-root /path/to/conversion/Validation
```

Example — iterate-patching runs:

```bash
# r1 (3/4 passed)
uv run --project data-engineering/spark-migration/snowpark-connect \
  streamlit run data-engineering/spark-migration/snowpark-connect/validate-pyspark-to-snowpark-connect/scripts/report/validation_report_app.py \
  -- --run-root ~/code/snowpark-migration-dev/iterate-patching/r1/conversion/Validation

# r2 (2/4 passed)
uv run --project data-engineering/spark-migration/snowpark-connect \
  streamlit run data-engineering/spark-migration/snowpark-connect/validate-pyspark-to-snowpark-connect/scripts/report/validation_report_app.py \
  -- --run-root ~/code/snowpark-migration-dev/iterate-patching/r2/conversion/Validation

# r3 (4/4 passed)
uv run --project data-engineering/spark-migration/snowpark-connect \
  streamlit run data-engineering/spark-migration/snowpark-connect/validate-pyspark-to-snowpark-connect/scripts/report/validation_report_app.py \
  -- --run-root ~/code/snowpark-migration-dev/iterate-patching/r3/conversion/Validation
```

Or set an environment variable:

```bash
export VALIDATION_RUN_ROOT=/path/to/conversion/Validation
uv run --project data-engineering/spark-migration/snowpark-connect \
  streamlit run data-engineering/spark-migration/snowpark-connect/validate-pyspark-to-snowpark-connect/scripts/report/validation_report_app.py
```

You can also use **Browse directory** in the sidebar or paste a path manually. The directory must contain `run_index.json`.

The app reads files directly from disk and does not generate intermediate report artifacts. It surfaces:

- `run_index.json` run metadata, milestones, and per-entrypoint verdicts
- `results/summary.json` decision and manual-review context
- `results/REPORT.md` markdown summary
- `events.jsonl` timeline events
- per-trial `_index.json`, `_manual_review.json`, `workload_error.txt`, diff JSON files
- parquet outputs under `tables/`

Notes:

- Parquet previews use `pandas.read_parquet`, so `pyarrow` is included in the skill project dependencies.
- The folder picker uses the native macOS dialog (`osascript`) when running locally on macOS, so it works from Streamlit's background thread.
- The UI is intentionally dynamic; refreshing the app re-reads the selected run from disk.
