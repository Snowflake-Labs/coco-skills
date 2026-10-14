"""Annotate calls to PySpark builtins that SCOS implements via a server-side
Python UDF (slower than native SQL).

What it does
------------

A handful of ``pyspark.sql.functions`` builtins have no native Snowflake SQL
equivalent, so SCOS executes them through a server-side Python UDF. They produce
correct results but carry UDF overhead (serialization, per-row Python) and are
noticeably slower on hot paths. This recipe attaches a single leading
``# SCOS:`` perf-hint comment above any statement that calls one of them. It does
NOT rewrite the call — choosing a native alternative is workload-specific and
belongs to the LLM fixer / author.

Targeted (unconditionally UDF-backed in SCOS, per
``expression/map_unresolved_function.py``):

    crc32, format_number, format_string, printf, from_csv,
    map_concat, map_from_arrays

Deliberately NOT targeted
-------------------------

Some builtins are UDF-backed only for *specific* argument shapes that cannot be
determined statically — flagging every call site would produce false positives:

* ``bit_count``  — UDF only for integral inputs.
* ``encode``     — UDF only for the bare UTF-16 charset.
* ``transform``  — UDF only for the 2-arg index variant
                   (``transform(col, (x, i) -> ...)``).

These are left to the LLM analyzer (which can inspect argument types/shape).

Functions that are *commonly assumed* slow but are actually native in SCOS are
intentionally NOT flagged: ``array_repeat``, ``percentile_approx``,
``map_values``, ``map_filter``, ``map_zip_with``, ``transform_keys``,
``transform_values``, ``xxhash64``, ``schema_of_csv``, ``schema_of_json``,
``bin``, ``conv``.

Trigger
-------

A ``Call`` whose callee is one of the targeted names, either as a bare
``Name`` (``crc32(...)``) or as an attribute (``F.crc32(...)``,
``functions.crc32(...)``). ``crc32`` reached via a stdlib module
(``zlib.crc32``/``binascii.crc32``/``hashlib`` …) is NOT flagged — that is not
the Spark column function.

Negative cases (must NOT trigger)
---------------------------------

* Already-annotated lines (idempotent).
* ``zlib.crc32(...)`` / ``binascii.crc32(...)`` (stdlib, not the Spark function).
* Any of the "actually native" or conditional functions listed above.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "udf_backed_builtin_perf_annotate"
MIN_SCOS_VERSION = "0.4.0"

# Unconditionally UDF-backed Spark builtins (statically detectable by name).
_UDF_BACKED = frozenset(
    {
        "crc32",
        "format_number",
        "format_string",
        "printf",
        "from_csv",
        "map_concat",
        "map_from_arrays",
    }
)

# Stdlib receivers that expose a same-named function which is NOT the Spark
# column function (only relevant for ``crc32``).
_STDLIB_CRC32_RECEIVERS = frozenset({"zlib", "binascii", "hashlib"})


def _callee(call: cst.Call) -> tuple[Optional[str], Optional[str]]:
    """Return ``(function_name, receiver_name)`` for ``call``.

    ``receiver_name`` is the bare ``Name`` the function is an attribute of
    (e.g. ``F`` in ``F.crc32``), or ``None`` for a bare ``Name`` call or a
    non-``Name`` receiver.
    """
    func = call.func
    if isinstance(func, cst.Name):
        return func.value, None
    if isinstance(func, cst.Attribute) and isinstance(func.attr, cst.Name):
        recv = func.value
        recv_name = recv.value if isinstance(recv, cst.Name) else None
        return func.attr.value, recv_name
    return None, None


class _Detector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.matched: list[str] = []

    def visit_Call(self, node: cst.Call) -> None:
        name, recv = _callee(node)
        if name not in _UDF_BACKED:
            return
        if name == "crc32" and recv in _STDLIB_CRC32_RECEIVERS:
            return
        if name not in self.matched:
            self.matched.append(name)


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
        if not det.matched:
            return updated_node
        fns = ", ".join(f"{n}()" for n in det.matched)
        comment = (
            f"# SCOS: {RECIPE_ID}: {fns} is UDF-backed in SCOS (server-side "
            f"Python UDF, slower than native); prefer a native alternative on "
            f"hot paths. See references/python/udf-dependencies.md."
        )
        self._record(start, f"udf-backed builtin perf hint: {fns}")
        return _annotate.prepend_comment(updated_node, comment)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
