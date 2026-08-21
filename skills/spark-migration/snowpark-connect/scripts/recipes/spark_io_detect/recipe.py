"""Detect Spark read/write (I/O) calls and classify their SCOS disposition.

What it does
------------

Walks every ``DataFrameReader`` / ``DataFrameWriter`` call chain — ``spark.read``,
``<df>.write``, ``spark.readStream``, ``<df>.writeStream`` — and attaches a single
leading ``# SCOS:`` marker classifying the I/O by its **target and format**, so
*all* file/external I/O surfaces deterministically (instead of relying on the
LLM fixer's free-text comment). It annotates only; it never rewrites the call
(the concrete stage/table choice is workload-specific and belongs to the fixer).

Classification (target/format aware)
------------------------------------

* JDBC (``.jdbc(...)`` or ``.format("jdbc")``) -> **Error** (SPRKCNTPY6000): JDBC
  needs a JVM driver not available in Spark Connect.
* Structured Streaming (``readStream`` / ``writeStream``) -> **Error**
  (SPRKCNTPY2000): SCOS hosts no streaming engine.
* File I/O — a path-based reader/writer (``parquet``/``csv``/``json``/``orc``/
  ``text``/``load``/``save``) whose format is a plain file format (or unset) ->
  **IO** (SPRKCNTPY3200): file I/O must go through a Snowflake stage/table.
  This covers external cloud URIs (``s3://`` …), wildcard/glob paths, local
  paths, and non-literal path variables alike (all need a stage on SCOS).
* Iceberg (``.format("iceberg")``) -> **IO** (SPRKCNTPY3200): catalog table I/O
  is surfaced for review. No kb_rule/analyzer rule covers the
  ``.format("iceberg").load(...)`` API pattern, so tagging it here is what makes
  the data boundary visible instead of silently dropped.
* Table I/O (``saveAsTable`` / ``insertInto`` / ``spark.read.table``) -> **IO**
  (SPRKCNTPY3200): the read/write itself is supported on SCOS, but the table
  name/namespace must resolve to the intended Snowflake table, so it is surfaced
  for verification (catalog/schema mapping can differ from the source).

Deliberately NOT handled here (left to dedicated recipes / the analyzer, to
avoid double-annotation):

* ``.format("delta")``  -> ``delta_write_to_parquet_rewrite`` / DeltaTable rules.
* ``.format("snowflake")`` -> ``snowflake_connector_io_to_snowflake_session_rewrite``.
* Snowflake stage paths (``@stage/...``) — already the recommended form.

This recipe subsumes the older read-only annotators
``external_cloud_read_stage_perf_comment`` and ``wildcard_file_read_todo_annotate``
(both covered only ``spark.read.*``); it adds the missing writer + JDBC + stream
coverage and unifies the disposition.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "spark_io_detect"
MIN_SCOS_VERSION = "0.4.0"

# Terminal reader/writer methods that carry a file PATH argument.
_PATH_METHODS = frozenset({"parquet", "csv", "json", "orc", "text", "load", "save"})
# Table-based terminals — supported on SCOS (Snowflake table), never flagged.
_TABLE_METHODS = frozenset({"saveastable", "insertinto", "table"})
# Formats handled by other recipes / the analyzer — skip to avoid double-annotation.
_SKIP_FORMATS = frozenset({"delta", "snowflake"})
# Plain file formats (used when .format(...) is present with a path terminal).
_FILE_FORMATS = frozenset({"parquet", "csv", "json", "orc", "text", "avro", "xml", ""})

_CLOUD_PREFIXES = (
    "s3://", "s3a://", "s3n://", "gs://", "gcs://", "abfs://", "abfss://",
    "azure://", "wasb://", "wasbs://", "adl://", "oss://", "oci://", "dbfs:/", "hdfs://",
)
_GLOB_META = ("*", "?", "[")

_CODE_IO = "SPRKCNTPY3200"
_CODE_JDBC = "SPRKCNTPY6000"
_CODE_STREAM = "SPRKCNTPY2000"


def _leaf(node) -> Optional[str]:
    return node.value if isinstance(node, cst.Name) else None


def _walk_chain(call: cst.Call):
    """Inspect a terminal Call and its receiver chain.

    Returns None if the chain is not a Spark reader/writer, else a dict with:
      role      -- "read" | "write" | "readStream" | "writeStream"
      terminal  -- lower-cased terminal method name (parquet/save/jdbc/...)
      fmt       -- lower-cased .format("x") value found in the chain, or ""
      strings   -- list of string-literal args on the terminal call
      has_arg   -- True if the terminal call has any positional arg
    """
    if not isinstance(call.func, cst.Attribute) or not isinstance(call.func.attr, cst.Name):
        return None
    terminal = call.func.attr.value.lower()
    role = None
    fmt = ""
    node: Optional[cst.CSTNode] = call.func.value
    seen = 0
    while node is not None and seen < 100:
        seen += 1
        if isinstance(node, cst.Call):
            # A .format("x") link in the chain contributes the format.
            f = node.func
            if isinstance(f, cst.Attribute) and isinstance(f.attr, cst.Name) and f.attr.value == "format":
                for a in node.args:
                    if a.keyword is None:
                        val = _string_of(a.value)
                        if val is not None:
                            fmt = val.lower()
                            break
            node = node.func
            continue
        if isinstance(node, cst.Attribute):
            name = _leaf(node.attr)
            if name in ("read", "write", "readStream", "writeStream"):
                role = name
                break
            node = node.value
            continue
        if isinstance(node, cst.Name):
            break
        node = getattr(node, "value", None)
    if role is None:
        return None
    strings = []
    has_arg = False
    for a in call.args:
        if a.keyword is None:
            has_arg = True
            s = _string_of(a.value)
            if s is not None:
                strings.append(s)
    return {"role": role, "terminal": terminal, "fmt": fmt, "strings": strings, "has_arg": has_arg}


def _string_of(node) -> Optional[str]:
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        try:
            return node.evaluated_value
        except Exception:  # noqa: BLE001
            return None
    return None


def _classify(info: dict):
    """Return (code, status, message) for a reader/writer, or None to skip."""
    role, terminal, fmt, strings = info["role"], info["terminal"], info["fmt"], info["strings"]

    # Streaming: no engine in SCOS.
    if role in ("readStream", "writeStream"):
        return (_CODE_STREAM, "Error",
                "Structured Streaming (readStream/writeStream) is not supported in SCOS — "
                "no streaming engine. Rewrite as a batch read/write.")
    # JDBC: needs a JVM driver not available in Spark Connect.
    if terminal == "jdbc" or fmt == "jdbc":
        return (_CODE_JDBC, "Error",
                "JDBC source/sink requires a JVM driver not available in Spark Connect — "
                "use the Snowflake connector, an external table, or load the data to a Snowflake table.")
    # Table I/O — reads/writes a Snowflake table (supported on SCOS), but the
    # table name/namespace must resolve in Snowflake, so surface it for review.
    # delta/snowflake formats on a table terminal are still owned by their
    # dedicated recipes, so defer those.
    if terminal in _TABLE_METHODS:
        if fmt in _SKIP_FORMATS:
            return None
        verb = "reads from" if role == "read" else "writes to"
        return (_CODE_IO, "IO",
                f"table I/O — {verb} a Snowflake table; verify the table "
                "name/namespace (database.schema.table) resolves to the intended "
                "Snowflake table (catalog/schema mapping may differ from the source).")
    # Formats owned by dedicated recipes / the analyzer.
    if fmt in _SKIP_FORMATS:
        return None
    # Iceberg catalog I/O — tag as IO so all data boundaries are visible. No
    # kb_rule/analyzer rule covers the .format("iceberg").load(...) pattern, so
    # without this the read/write would fall through untagged.
    if fmt == "iceberg":
        verb = "reads from" if role == "read" else "writes to"
        return (_CODE_IO, "IO",
                f"Iceberg catalog table I/O — {verb} an Iceberg-managed table; "
                "verify the table is accessible in Snowflake (Iceberg Tables, "
                "external catalog integration, or migrate to a native Snowflake table).")
    # Path-based file I/O: only when the terminal is a path method AND (if a
    # format is set) it is a plain file format.
    if terminal in _PATH_METHODS and (fmt in _FILE_FORMATS):
        # Already a Snowflake stage path -> recommended form, skip.
        if any(s.lstrip().startswith("@") for s in strings):
            return None
        if any(s.startswith(_CLOUD_PREFIXES) for s in strings):
            where = "external cloud path"
        elif any(any(m in s for m in _GLOB_META) for s in strings):
            where = "wildcard/glob path"
        elif strings:
            where = "file path"
        elif info["has_arg"]:
            where = "path variable"
        else:
            return None  # no path arg (e.g. .load() with only options) — skip
        verb = "read from" if role == "read" else "write to"
        return (_CODE_IO, "IO",
                f"file I/O — {verb} a {where}; on SCOS this must go through a Snowflake "
                f"stage/table (external stage, storage integration, or COPY INTO an internal stage).")
    return None


class _Detector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.hit = None  # (code, status, message)

    def visit_Call(self, node: cst.Call) -> None:
        if self.hit is not None:
            return
        info = _walk_chain(node)
        if info is None:
            return
        verdict = _classify(info)
        if verdict is not None:
            self.hit = verdict


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _annotate.comment_above_contains(self._lines, start, RECIPE_ID):
            return updated_node
        det = _Detector()
        updated_node.visit(det)
        if det.hit is None:
            return updated_node
        code, status, message = det.hit
        comment = f"# SCOS: [{code}-{status}] {RECIPE_ID}: {message}"
        self._record(start, f"io {status}: {code}")
        return _annotate.prepend_comment(updated_node, comment)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
