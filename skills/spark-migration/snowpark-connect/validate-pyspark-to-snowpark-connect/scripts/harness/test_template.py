"""Template — copied to ``Validation/tests/test_<ep_id>.py`` per entrypoint.

The runner authors one of these per entrypoint. Customize the constants marked
``# CUSTOMIZE``; everything else is workload-agnostic. Provisioning, execution,
capture, and (for Phase B) comparison all happen inside
``runtimes.driver.run_validation_trial`` via the selected runtime — this file
only declares WHAT to run.
"""

from __future__ import annotations

import pytest

from runtimes.driver import build_trial_request, run_validation_trial


# ---------------------------------------------------------------------------
# Per-entrypoint constants — CUSTOMIZE.
# ---------------------------------------------------------------------------

# Must match this file's name's <ep_id> portion.
TRIAL_ID = "<ep_id>"

# Relative path under Validation/source/ (Phase A) or <conv-root>/Output/ (Phase B).
ENTRYPOINT_PATH = "src/<path>/<module>.py"

# Callable to invoke.
# None  = script mode: the entrypoint is a top-level script (Databricks notebook
#         style) — the harness just executes the module; no function is called.
# "run" = callable mode: call the named function with (spark, **kwargs).
ENTRYPOINT_CALLABLE = None  # set to e.g. "run" for function-based entrypoints


# Optional kwargs for the entrypoint callable (callable mode only).
ENTRYPOINT_KWARGS: dict = {}


# Optional schema-dependent kwargs. Merged over ENTRYPOINT_KWARGS at call time.
def ENTRYPOINT_KWARGS_FACTORY(output_schema):  # noqa: N802
    return {
        # "DATABASE_NAME": "spark_catalog",
        # "SCHEMA_STAGING": output_schema,
    }


# Optional module globals the entrypoint expects at runtime.
def MODULE_GLOBALS_FACTORY(output_schema):  # noqa: N802
    return {
        # "DATABASE_NAME": "spark_catalog",
        # "SCHEMA_STAGING": output_schema,
    }


# ---------------------------------------------------------------------------
# The test. Workload-agnostic — do not edit below this line.
# ---------------------------------------------------------------------------

def test_main_entrypoint():
    # Guard: skip when this template has not been rendered (placeholder still present).
    if TRIAL_ID == "<ep_id>":
        pytest.skip("Unrendered test_template.py — not a real trial")

    request = build_trial_request(
        trial_id=TRIAL_ID,
        entrypoint_path=ENTRYPOINT_PATH,
        callable_name=ENTRYPOINT_CALLABLE,
        kwargs=ENTRYPOINT_KWARGS,
        kwargs_factory=ENTRYPOINT_KWARGS_FACTORY,
        module_globals_factory=MODULE_GLOBALS_FACTORY,
    )
    run_validation_trial(request)
