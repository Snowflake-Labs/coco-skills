# Tests for snowpark-connect scripts

Unit tests for the helper scripts under `snowpark-connect/scripts/`.

Currently covered:

- **`generate_scos_reports.py`** — `# SCOS:` comment block extraction
  (multi-line flattening + inline `[SPRKCNT...]` code split-out), plus the
  recipe-emitted `# SCOS-WARN:` / `# SCOS-TODO:` markers (categorised by
  flavour, left untouched by the annotator), inline EWI code normalization
  (reuse the fixer's code, inject a generic one when absent, drop legacy
  `#EWI:` lines directly above a `# SCOS:` comment; idempotent), and
  `Issues.csv` generation that reads the inline codes.

## Running

From the `snowpark-connect/` directory:

```bash
make test
```

Or directly with pytest (via the project's `uv` env):

```bash
uv run pytest                       # uses [tool.pytest.ini_options] in pyproject.toml
uv run pytest -v                    # verbose
uv run pytest scripts/tests -k snap # filter by keyword
```

`scripts/` is added to `pythonpath` via `pyproject.toml`, so test modules
can import the scripts directly (`from generate_scos_reports import ...`).
