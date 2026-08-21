# Validation Report App

This app renders a completed Spark Scala → Snowpark Connect validation run as an
interactive browser view.

Launch with a specific validation directory:

```bash
uv run --project data-engineering/spark-migration/snowpark-connect \
  streamlit run data-engineering/spark-migration/snowpark-connect/validate-spark-scala-to-snowpark-connect/scripts/report/validation_report_app.py \
  -- --run-root /path/to/conversion/Validation
```

`validate.py summary` prints this exact one-liner at the end of a run (and
writes it into `results/REPORT.md`) — copy it verbatim.

Or set an environment variable:

```bash
export VALIDATION_RUN_ROOT=/path/to/conversion/Validation
uv run --project data-engineering/spark-migration/snowpark-connect \
  streamlit run data-engineering/spark-migration/snowpark-connect/validate-spark-scala-to-snowpark-connect/scripts/report/validation_report_app.py
```

You can also use **Browse directory** in the sidebar or paste a path manually.
The directory must contain `run_index.json`.

The app reads files directly from disk and does not generate intermediate report
artifacts. It surfaces:

- `run_index.json` run metadata, milestones, and per-entrypoint verdicts
  (including each trial's `migration_fix_commits`)
- `results/summary.json` decision and manual-review context
- `results/REPORT.md` markdown summary
- `events.jsonl` timeline events
- `shared/analysis.json` selected entrypoints, external sources, and sinks
- per-trial `_index.json`, `_manual_review.json`, `workload_error.txt`, diff JSON
- parquet outputs under `tables/` (captured as Spark output directories)

Notes:

- Parquet previews use `pandas.read_parquet`, so `pyarrow` is included in the
  skill project dependencies.
- The folder picker uses the native macOS dialog (`osascript`) when running
  locally on macOS, so it works from Streamlit's background thread.
- The UI is intentionally dynamic; refreshing the app re-reads the selected run
  from disk.
