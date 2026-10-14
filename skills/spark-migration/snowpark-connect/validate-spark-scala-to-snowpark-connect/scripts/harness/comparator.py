"""Thin shim — single-source comparator from the PySpark validator.

Canonical implementation lives at:
  ``$VALIDATOR_SCRIPTS/harness/comparator.py``
  (= ``validate-pyspark-to-snowpark-connect/scripts/harness/comparator.py``)

This module re-exports that file so Scala ``compare_trial.py`` and summary
auto-compare never drift from a local fork.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PYSPARK_COMPARATOR = (
    Path(__file__).resolve().parents[3]
    / "validate-pyspark-to-snowpark-connect"
    / "scripts"
    / "harness"
    / "comparator.py"
)
if not _PYSPARK_COMPARATOR.is_file():
    raise ImportError(
        f"canonical PySpark comparator not found at {_PYSPARK_COMPARATOR} "
        "(expected $VALIDATOR_SCRIPTS/harness/comparator.py)"
    )

_spec = importlib.util.spec_from_file_location(
    "scos_pyspark_comparator", _PYSPARK_COMPARATOR
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load comparator from {_PYSPARK_COMPARATOR}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules[__name__] = _mod
