"""Guardrails for the EWI ``Error`` disposition — keep it a TRUE code-failure.

Goal: zero false-positive ``Error`` markers. ``Error`` means the emitted code
WILL fail on SCOS. Everything else has its own bucket:
  * data input/output plumbing (paths, unsupported file formats, external
    data-source connectors, filesystem) -> ``IO``
  * behavioral differences, no-ops, perf/lifecycle advice, or a construct a
    recipe already neutralized so the line runs -> ``Warning``

These tests fail loudly if a future rule/recipe re-introduces a known
false-positive-Error shape, so the classification cannot silently regress.
"""
import importlib.util
import json
import re
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
_KB = _SCRIPTS / "data" / "kb_rules.json"


def _load_gsr():
    spec = importlib.util.spec_from_file_location(
        "gsr_under_test", _SCRIPTS / "generate_scos_reports.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A rule whose ANCHOR (api / rule_id) names a data-I/O construct is an I/O
# repoint, never a code-conversion Error. Matched on identity — NOT note text,
# which can mention example paths/keys and cause false matches.
_IO_IDENTITY = re.compile(
    r"\borc\b|\bavro\b|\bjdbc\b|redshift|hivewarehouse|dbutils\.fs", re.I
)


def test_data_io_rules_are_not_tagged_error():
    rules = json.loads(_KB.read_text())
    offenders = []
    for r in rules:
        if r.get("status_class") != "Error":
            continue
        ident = " ".join(map(str, r.get("api") or [])) + " " + (r.get("rule_id") or "")
        if _IO_IDENTITY.search(ident):
            offenders.append(r.get("rule_id"))
    assert not offenders, (
        "data-I/O constructs must be status_class 'IO', not 'Error' "
        f"(false-positive Errors): {offenders}"
    )


# ``Error`` rules that are NOT ``Unsupported`` and whose note reads as
# advisory/IO under the shared classifier. Each has been reviewed and is kept
# ``Error`` on purpose — this list is a change-detector, not an approval to
# grow it. A NEW entry here means someone added an Error-tagged rule with a
# behavioral/advisory note: review it (downgrade to Warning, gate it, or add it
# here with a justification). The tail is intentionally small.
_REVIEWED_ERROR_TAIL = {
    # regexp_instr: Perl-only patterns (lookahead/backrefs) genuinely RAISE on
    # SCOS's POSIX engine — real conditional failure, kept Error.
    "csv:sql_test:regexp-functions.sql",
    # row_number without ORDER BY: window resolution can fail — kept Error.
    "csv:sql_test:window.sql",
    # DISTINCT-then-ORDER-BY on a non-projected column: precision-gated rule that
    # fails on SCOS when it truly fires — kept Error (gating handles over-fire).
    "csv:expectation_tests_xfail:test_order_by.py",
    # spark.conf.set raise-family: genuinely raises ValueError at runtime. Only
    # *reads* as IO because the note lists example configs (s3/jdbc keys) — a
    # note-text artifact, not a real IO reclassification. Kept Error.
    "scos:spark.conf.set#static-spark-internals-raise",
}


def test_error_behavioral_tail_is_reviewed():
    # Guardrail: the set of Error rules that are non-Unsupported AND read as
    # advisory/IO must not grow beyond the reviewed allowlist above. New
    # entries force a human decision instead of silently shipping a maybe-FP.
    gsr = _load_gsr()
    rules = json.loads(_KB.read_text())
    flagged = set()
    for r in rules:
        if r.get("status_class") != "Error":
            continue
        if r.get("status") in ("Unsupported", "Partial"):
            continue
        if gsr._message_signal(r.get("note") or "") in ("Warning", "IO"):
            flagged.add(r.get("rule_id"))
    new = flagged - _REVIEWED_ERROR_TAIL
    assert not new, (
        "New Error-tagged rule(s) with a behavioral/advisory note — review and "
        f"either downgrade/gate or add to the reviewed allowlist: {sorted(new)}"
    )


def test_message_signal_dispositions():
    gsr = _load_gsr()
    sig = gsr._message_signal
    # stubbed / dropped / no-op -> code RUNS -> Warning (manual follow-up)
    assert sig("dbutils.secrets has no SCOS equivalent; stubbed to None.") == "Warning"
    assert sig("format() dropped in saveAsTable; use write().save()") == "Warning"
    assert sig("spark.driver.memory is a no-op on SCOS") == "Warning"
    # behavioral / perf advisories -> Warning (execute fine)
    assert sig("Timestamp operations may need verification") == "Warning"
    assert sig("count() can hang/timeout; consider SQL COUNT(*)") == "Warning"
    assert sig("cache() lifecycle differs; consider materialization strategy") == "Warning"
    # data I/O -> IO
    assert sig("JDBC source/sink requires a JVM driver; use the Snowflake connector") == "IO"
    assert sig("cloud-storage paths (s3://, dbfs:, gs://) must repoint to a stage") == "IO"
    assert sig("SCOS has no ORC reader (only parquet, json, csv)") == "IO"
    # genuine code-logic failures -> Error (must NOT be softened)
    assert sig("RDD not supported; rewrite with DataFrame API") == "Error"
    assert sig(".rdd.collectAsMap() is not supported; use DataFrame.collect()") == "Error"
    assert sig("Python UDF in lambda/transform not supported in Snowflake") == "Error"
    assert sig("parse_json is a Snowflake-native function, not available in PySpark") == "Error"
    assert sig("<session>.sparkContext ... 'broadcast' cannot be auto-rewritten") == "Error"
