#!/usr/bin/env python3
"""Generate the published JSON Schema for the ``llm_resolved_data_edges`` block.

Single source of truth for the schema file the resolver agent and the coverage
gate both consume. The schema is derived from the pydantic models in
``assess_ir.py`` (the real runtime validator), so it can never drift from what
the code accepts.

Usage — regenerate the committed file after changing the models::

    uv run --project <SKILL_DIR> \\
      python scripts/assessment/export_llm_schema.py

``tests/test_schema_export.py`` asserts the committed file equals ``build()``,
failing CI if someone edits the models without regenerating.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from assess_ir import LLMResolvedDataEdges  # noqa: E402

# Committed location — under the skill's agent references (loaded by
# data_edge_resolver.md and validated against by check_data_edges_gate.py).
SCHEMA_PATH = (
    _SCRIPT_DIR.parent.parent
    / "migrate-pyspark-to-snowpark-connect"
    / "references"
    / "llm_resolved_data_edges.schema.json"
)


def build() -> dict:
    """Return the full JSON Schema document for the llm block."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://snowflake.com/scos/llm_resolved_data_edges.schema.json",
        "title": "llm_resolved_data_edges",
        **LLMResolvedDataEdges.model_json_schema(),
    }


def main() -> int:
    SCHEMA_PATH.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"[export_llm_schema] Wrote {SCHEMA_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
