"""Deterministic workload synthesizer for PySpark->Snowpark-Connect validation.

Takes a workload directory and, WITHOUT executing code or touching the user's
real tables, produces an entrypoint-keyed analysis: every Spark entrypoint and,
within each, its sources and sinks with mined schema + an explicit list of what
still needs an LLM.

Pipeline:
  1. Entrypoint detection (deterministic): files with an entrypoint marker
     (Databricks notebook / native .ipynb / __main__ / creates SparkSession /
     module-level Spark I/O) that nothing else imports or %run's, and whose
     import closure touches Spark. Non-Spark files are skipped. Both ``.py`` and
     Jupyter ``.ipynb`` notebooks are read (``.ipynb`` code cells are concatenated
     and magics neutralized at the read boundary, so all layers below are
     format-agnostic).
  2. For each entrypoint, mine the entrypoint + its transitive import closure and
     union sources/sinks (reads/writes often live in imported reader/transformer/
     writer modules).
  3. Schema mining layers (confidence order):
       A. StructType/StructField literal extraction   (exact: name+type+nullable)
       B. embedded spark.sql() lineage via sqlglot     (tables + col->table; temp
          views folded into their backing read source)
       B2. project ``*.sql`` template files           (persistent top-level
          ``sql_files`` catalog with per-file table/column lineage; LLM merges
          into entrypoint sources/sinks; catalog rows stay for datagen)
       C. role-aware ast DataFrame column mining       (inputs = refs - outputs;
          handles helper-wrapped reads + cross-file StructType binding)
       D. sqlframe validation                          (replay SQL vs mined catalog)
   4. Cross-entrypoint schema inference: a name that is a sink of one entrypoint
      and read back as a source elsewhere (or in the same run) inherits the
      producer's authoritative schema. Entrypoint trials are ISOLATED, so this
      only sharpens the consumer's schema -- it never implies runtime data reuse;
      every source is seeded independently (datagen generates a shared name's data
      once, then copies a private dataset into each consuming entrypoint). A sink
      is provisioned from its ``kind`` (table -> create empty for writes to land;
      file -> output captured, nothing to precreate).
   5. Source classification: every source carries ``relational`` (true|false).
      Relational sources have a tabular schema and are seeded by datagen.
      Non-relational sources (config/document blobs read via json.load / yaml /
      open() outside Spark) have no tabular schema -- they are flagged
      ``relational: False`` with an ``llm_reason`` so the LLM supplies the schema
      and generates the file.

Public API:
    synthesize(root, entrypoints=None) -> {root, complete, entrypoints:[...],
                                         sql_files:[...], summary}
    detect_entrypoints(root) -> ([entrypoints], facts)
    mine(entrypoint_path, ...) -> per-file contract (internal core)
    main()  (CLI: python schema_mine.py <workload_dir> [--entrypoints ...] [--json])

The output is a COMPLETABLE contract: it states what static analysis knows and
marks every gap with an ``llm_todo`` (a guessed source name, an open column set,
a non-relational ``document_schema`` to fill, an unmined sink schema). The LLM
resolves each ``llm_todo`` and deletes it; when none remain ``complete`` is true
and the file is fed to datagen, which mocks every relational source and
pre-creates every sink. Each source carries ``relational`` (true|false); each
sink carries ``kind`` (table|file).
"""
from __future__ import annotations

import ast
import inspect
import warnings
# parsing arbitrary user workloads: their escape/regex warnings are not ours
warnings.filterwarnings("ignore", category=SyntaxWarning)
import json
import os
import re
import sys
from typing import Any


def _notebook_source_to_python(path: str) -> str:
    """Delegate ``.ipynb`` translation to ``notebook_source.to_python`` if available.

    Imports lazily from the sibling ``harness/`` dir (Python caches the module, so
    repeat calls are cheap); returns "" on ImportError so the caller falls back to
    inline concat.
    """
    _harness_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")
    if _harness_dir not in sys.path:
        sys.path.insert(0, _harness_dir)
    try:
        import notebook_source
    except ImportError:
        return ""
    return notebook_source.to_python(path)


def _dbx_source_to_python(text: str) -> str | None:
    """Translate a Databricks notebook-source ``.py`` (``# MAGIC`` cells) to Python.

    Returns the translated source when *text* is a dbx notebook, ``None`` when it
    is a plain ``.py`` module (caller uses the raw text). Delegates to
    ``notebook_source``; returns ``None`` if it cannot be imported."""
    _harness_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")
    if _harness_dir not in sys.path:
        sys.path.insert(0, _harness_dir)
    try:
        import notebook_source
    except ImportError:
        return None
    if notebook_source.is_dbx_notebook_py(text):
        return notebook_source.dbx_py_to_python(text)
    return None


def _read_source(path: str) -> str:
    """Read a workload file as Python source text. For a Jupyter ``.ipynb`` the
    code cells are translated into executable Python (``%sql`` → ``spark.sql()``,
    ``%run`` → ``_nb_run()``, other magics neutralized) so the AST parses and the
    spark.sql() lineage layer sees SQL table references. A Databricks
    notebook-source ``.py`` (``# MAGIC``/``# COMMAND`` cells) is translated the
    same way, so its ``# MAGIC %sql`` cells contribute lineage instead of being
    read as dead comments. Every data-synthesizer read goes through here, so the
    rest of the pipeline (entrypoint detection + all mining layers) is
    format-agnostic.

    Delegates to ``scripts/harness/notebook_source.py`` when available; falls
    back to inline JSON-concat + regex neutralization if the import fails."""
    if path.endswith(".ipynb"):
        try:
            nb_src = _notebook_source_to_python(path)
            if nb_src:
                return nb_src
        except Exception:
            pass
        # Fallback: inline concat + neutralize (format-agnostic minimum).
        try:
            nb = json.load(open(path, encoding="utf-8"))
        except Exception:
            return ""
        blocks = []
        for cell in nb.get("cells", []):
            if cell.get("cell_type") != "code":
                continue
            src = cell.get("source", "")
            cell_src = "".join(src) if isinstance(src, list) else str(src)
            first = next((ln for ln in cell_src.splitlines() if ln.strip()), "")
            if first.strip().startswith("%%"):
                # A cell magic (%%sql, %%bash, ...) owns the WHOLE cell body — the
                # lines after it are not Python. Neutralize the whole cell so the
                # SQL/other body can't leak as bare (unparseable) text.
                blocks.append("pass  # notebook-magic")
            else:
                # Neutralize per-line magics / shell escapes within a normal cell.
                blocks.append(re.sub(r"(?m)^[ \t]*[%!].*$", "pass  # notebook-magic", cell_src))
        text = "\n\n".join(blocks)
        return text
    with open(path, encoding="utf-8") as _fh:
        raw = _fh.read()
    # A Databricks notebook-source .py: translate its # MAGIC cells so %sql/%run
    # lineage is mined. A plain .py module is returned verbatim.
    try:
        translated = _dbx_source_to_python(raw)
        if translated is not None:
            return translated
    except Exception:
        pass
    return raw


def _loc(path: str, lineno) -> str | None:
    """`file:line` provenance for a mined source/sink so the LLM can pin which
    read/write a (possibly placeholder) name refers to. Path is left as given
    here; `synthesize()` rewrites it relative to the workload root."""
    return "%s:%d" % (path, lineno) if lineno else None


def _opt_value(v):
    """Coerce a Spark option literal: 'true'/'false' strings -> bool; else as-is."""
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return v

# ---------------------------------------------------------------------------
# PySpark introspection helpers (pyspark is a project dependency)
# ---------------------------------------------------------------------------

_CAST_ALIASES = {
    "integer": "int", "bigint": "long", "smallint": "short", "tinyint": "byte",
    "real": "float", "bool": "boolean", "str": "string",
}


def _norm_type(t: str) -> str:
    t = t.strip().lower().split("(")[0].strip()
    return _CAST_ALIASES.get(t, t)


def _spark_class_public_names(cls) -> set[str]:
    names: set[str] = set()
    for base in cls.__mro__:
        if base is object:
            continue
        for name, _ in inspect.getmembers(base):
            if not name.startswith("_"):
                names.add(name)
    return names


def _spark_members_with_return(cls, return_ann) -> frozenset[str]:
    """Public members whose ``__annotations__['return']`` equals *return_ann*."""
    names: set[str] = set()
    for base in cls.__mro__:
        if base is object:
            continue
        for name, member in inspect.getmembers(base):
            if name.startswith("_"):
                continue
            if getattr(member, "__annotations__", {}).get("return") == return_ann:
                names.add(name)
    return frozenset(names)


def _spark_read_methods() -> frozenset[str]:
    """``DataFrameReader`` terminal loaders (return ``DataFrame``, not ``DataFrameReader``)."""
    from pyspark.sql import DataFrameReader

    return _spark_members_with_return(DataFrameReader, "DataFrame")


def _spark_write_terminal_methods() -> frozenset[str]:
    """``DataFrameWriter`` terminal writes (return ``None``)."""
    from pyspark.sql import DataFrameWriter

    return _spark_members_with_return(DataFrameWriter, None)


def _spark_temp_view_methods() -> frozenset[str]:
    """``DataFrame.create*TempView`` methods (fold SQL lineage into backing source)."""
    return frozenset(n for n in _spark_df_api_names() if "tempview" in n.lower())


def _spark_session_markers() -> frozenset[str]:
    """Calls that create a ``SparkSession`` (builder API + common helper names)."""
    from pyspark.sql import SparkSession

    markers = {
        n for n in _spark_class_public_names(type(SparkSession.builder))
        if n in ("getOrCreate", "create")
    }
    markers.add("init_spark_session")  # common workload helper; not a PySpark API name
    return frozenset(markers)


def _spark_table_write_methods() -> frozenset[str]:
    """Writer terminal methods that land in a catalog table (not a file path)."""
    return frozenset({"saveAsTable", "insertInto"} & _spark_write_terminal_methods())


def _spark_format_io_methods() -> frozenset[str]:
    """File-format methods shared by ``DataFrameReader`` and ``DataFrameWriter``."""
    return _spark_read_methods() & _spark_write_terminal_methods()


def _spark_connector_formats() -> frozenset[str]:
    """Formats that read from an external system (native reader methods + ``.format()`` strings)."""
    file_formats = frozenset({"csv", "json", "parquet", "orc", "text"})
    non_connector_reads = file_formats | {"table", "load"}
    from_reader = {m.lower() for m in _spark_read_methods()} - {m.lower() for m in non_connector_reads}
    return frozenset({
        "snowflake", "bigquery", "mongo", "mongodb", "redshift", "cosmos", "delta", "kafka", "avro",
    }) | from_reader


def _spark_df_property_attrs() -> frozenset[str]:
    """``df.<attr>`` property / chain-head accesses that are never column names."""
    heads = {
        "write", "writeStream", "writeTo", "schema", "columns", "dtypes", "rdd", "na", "stat",
        "storageLevel", "isStreaming", "isLocal", "sparkSession", "sql_ctx", "pandas_api",
        "inputFiles", "isEmpty",
    }
    return frozenset(n for n in heads if n in _spark_df_api_names())


def _spark_input_methods() -> frozenset[str]:
    """DataFrame/GroupedData methods whose string args are input column names."""
    from pyspark.sql import DataFrame
    from pyspark.sql.group import GroupedData

    exclude = {
        "withColumn", "withColumns", "withColumnsRenamed", "withMetadata", "withWatermark",
        "alias", "limit", "offset", "sample", "randomSplit", "checkpoint", "localCheckpoint",
        "cache", "persist", "unpersist", "hint", "observe", "toDF", "to", "melt", "unpivot",
        "colRegex", "fillna", "dropna", "replace", "transform", "mapInPandas", "mapInArrow",
        "applyInPandas", "applyInPandasWithState", "apply", "cogroup", "pivot",
        "createOrReplaceTempView", "createTempView", "createGlobalTempView",
        "createOrReplaceGlobalTempView", "registerTempTable",
        "dropDuplicatesWithinWatermark", "crosstab", "freqItems", "summary", "describe",
        "exceptAll", "intersect", "intersectAll", "subtract", "union", "unionAll",
        "unionByName", "distinct", "coalesce", "repartitionByRange", "sampleBy",
    }
    methods = set(_spark_members_with_return(DataFrame, "DataFrame"))
    methods |= _spark_class_public_names(GroupedData)
    methods |= {"where", "drop_duplicates", "groupby", "groupBy", "selectExpr",
                "first", "last", "withColumnRenamed"}
    return frozenset(methods - exclude)


def _spark_df_api_names() -> frozenset[str]:
    """Declared DataFrame API members (methods + properties).

    Mirrors PySpark's ``df.<name>`` resolution: real class members win; anything
    else is treated as a column via ``__getattr__``."""
    from pyspark.sql import DataFrame

    return frozenset(_spark_class_public_names(DataFrame))


def _spark_struct_type_ctors() -> dict[str, str]:
    """Scalar ``*Type`` class names -> canonical type strings for StructField mining."""
    import pyspark.sql.types as types
    from pyspark.sql.types import AtomicType

    skip = {
        "DataType", "AtomicType", "NumericType", "IntegralType", "FractionalType",
        "AnsiIntervalType", "DayTimeIntervalType", "YearMonthIntervalType",
        "DecimalType", "ArrayType", "MapType", "StructType", "UserDefinedType",
    }
    out: dict[str, str] = {}
    for name, cls in inspect.getmembers(types, inspect.isclass):
        if not name.endswith("Type") or name in skip:
            continue
        if name == "NullType":
            out["NullType"] = "string"
            continue
        if not issubclass(cls, AtomicType):
            continue
        try:
            inst = cls()
        except TypeError:
            if name not in ("CharType", "VarcharType"):
                continue
            try:
                inst = cls(1)
            except Exception:
                continue
        except Exception:
            continue
        try:
            base = inst.simpleString().split("(")[0].lower()
        except Exception:
            continue
        canon = _norm_type(base)
        if canon in ("char", "varchar", "void") or name in ("CharType", "VarcharType", "NullType"):
            canon = "string"
        out[name] = canon
    return out


def _spark_non_input_calls() -> frozenset[str]:
    """Call names whose string args are values/patterns/IO targets, not input columns."""
    from pyspark.sql import Column, DataFrameReader, DataFrameWriter
    import pyspark.sql.functions as F

    names = (
        set(_spark_members_with_return(DataFrameReader, "DataFrameReader"))
        | set(_spark_members_with_return(DataFrameWriter, "DataFrameWriter"))
        | set(_spark_read_methods())
        | set(_spark_write_terminal_methods())
        | set(_spark_class_public_names(Column))
        | set(_spark_temp_view_methods())
    )
    for fn in ("lit", "when"):
        if hasattr(F, fn):
            names.add(fn)
    return frozenset(names)


def _spark_reader_opt_keys() -> frozenset[str]:
    """CSV/JSON/text reader option keys from format-method signatures + Spark aliases."""
    from pyspark.sql import DataFrameReader, DataFrameWriter

    keys: set[str] = set()
    for cls, methods in (
        (DataFrameReader, ("csv", "json", "text", "orc")),
        (DataFrameWriter, ("csv", "json", "text")),
    ):
        for meth in methods:
            member = getattr(cls, meth, None)
            if member is None:
                continue
            for param in inspect.signature(member).parameters:
                if param != "self":
                    keys.add(param.lower())
    if "sep" in keys:
        keys.add("delimiter")
    if "encoding" in keys:
        keys.add("charset")
    return frozenset(keys)


# Loaded once at import from the installed PySpark version.
_READ_METHODS = _spark_read_methods()
_WRITE_TERMINAL = _spark_write_terminal_methods()
_WRITE_ATTRS = _WRITE_TERMINAL | {"write"}
_TABLE_WRITE_METHODS = _spark_table_write_methods()
_FILE_WRITE_METHODS = frozenset({"save"} & _WRITE_TERMINAL)

# pandas file readers (pd.read_csv / pandas.read_parquet / …). These bypass
# spark.read entirely, so the DataFrame miner would otherwise never see the file.
# Limited to formats datagen mocks and Snowflake COPY ingests cleanly — pandas
# readers with tricky defaults or non-file formats (`read_table` is TAB-delimited,
# `read_excel` is a binary workbook, `read_sql`/`read_html` aren't files) are
# intentionally excluded; those surface via the data-synthesizer's missed-table pass.
_PANDAS_READ_METHODS = {"read_csv": "csv", "read_json": "json",
                        "read_parquet": "parquet"}
_PANDAS_ALIASES = {"pd", "pandas"}
_FORMAT_IO_METHODS = _spark_format_io_methods()
_CONNECTOR_FMTS = _spark_connector_formats()
_STRUCT_TYPE_CTORS = _spark_struct_type_ctors()
_NON_INPUT_CALLS = _spark_non_input_calls()
_READER_OPT_KEYS = _spark_reader_opt_keys()
_DF_METHODS = _spark_df_api_names()
_DF_PROPERTY_ATTRS = _spark_df_property_attrs()
_TEMP_VIEW_METHODS = _spark_temp_view_methods()
_SESSION_MARKERS = _spark_session_markers()
_INPUT_METHODS = _spark_input_methods()

# ---------------------------------------------------------------------------
# Type maps (semantic — not derivable from introspection alone)
# ---------------------------------------------------------------------------

_AGG_FUNCS_NUMERIC = {"sum": "double", "avg": "double", "mean": "double",
                      "stddev": "double", "variance": "double"}
_AGG_FUNCS_LONG = {"count": "long", "countDistinct": "long"}

# tokens that look like identifiers but are SQL/Spark keywords or type names
_TYPE_KEYWORDS = set(_STRUCT_TYPE_CTORS) | {
    "long", "int", "integer", "string", "double", "float", "boolean", "bool",
    "date", "timestamp", "decimal", "bigint", "short", "byte", "binary",
    "array", "map", "struct", "true", "false", "null",
}

# join-type strings (3rd positional arg of DataFrame.join) -- never columns
_JOIN_TYPES = {
    "inner", "outer", "full", "fullouter", "full_outer", "cross",
    "left", "leftouter", "left_outer", "right", "rightouter", "right_outer",
    "semi", "leftsemi", "left_semi", "anti", "leftanti", "left_anti",
}


# ---------------------------------------------------------------------------
# Layer A: explicit StructType / StructField literals
# ---------------------------------------------------------------------------

class _StructVisitor(ast.NodeVisitor):
    """Collects StructType([...]) literals, keyed by assigned variable name."""

    def __init__(self, const_ints: dict[str, int]):
        self.schemas: dict[str, list[dict]] = {}   # var_name -> fields
        self.anon: list[list[dict]] = []           # inline (unnamed) schemas
        self.const_ints = const_ints
        self._pending: list[dict] | None = None

    def _ctor_name(self, node) -> str | None:
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                return f.id
            if isinstance(f, ast.Attribute):
                return f.attr
        return None

    def _field_type(self, node) -> str:
        name = self._ctor_name(node)
        if name is None:
            return "string"
        if name == "DecimalType":
            args = []
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, int):
                    args.append(a.value)
                elif isinstance(a, ast.Name) and a.id in self.const_ints:
                    args.append(self.const_ints[a.id])
            return f"decimal({','.join(map(str, args))})" if args else "decimal"
        if name == "ArrayType":
            inner = self._field_type(node.args[0]) if node.args else "string"
            return f"array<{inner}>"
        if name == "MapType":
            k = self._field_type(node.args[0]) if len(node.args) > 0 else "string"
            v = self._field_type(node.args[1]) if len(node.args) > 1 else "string"
            return f"map<{k},{v}>"
        if name == "StructType":
            fields = self._parse_struct(node)
            inner = ",".join(f"{f['name']}:{f['type']}" for f in fields)
            return f"struct<{inner}>"
        return _STRUCT_TYPE_CTORS.get(name, "string")

    def _parse_struct(self, node) -> list[dict]:
        fields: list[dict] = []
        if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
            return fields
        for el in node.args[0].elts:
            if self._ctor_name(el) != "StructField":
                continue
            if not el.args or not isinstance(el.args[0], ast.Constant):
                continue
            fname = el.args[0].value
            ftype = self._field_type(el.args[1]) if len(el.args) > 1 else "string"
            nullable = True
            if len(el.args) > 2 and isinstance(el.args[2], ast.Constant):
                nullable = bool(el.args[2].value)
            for kw in el.keywords:
                if kw.arg == "nullable" and isinstance(kw.value, ast.Constant):
                    nullable = bool(kw.value.value)
            fields.append({"name": fname, "type": ftype, "nullable": nullable})
        return fields

    def visit_Assign(self, node):
        if self._ctor_name(node.value) == "StructType" and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            fields = self._parse_struct(node.value)
            if fields:
                self.schemas[node.targets[0].id] = fields
        self.generic_visit(node)

    def visit_Call(self, node):
        # inline StructType passed directly to .schema(StructType([...]))
        if self._ctor_name(node) == "StructType":
            fields = self._parse_struct(node)
            if fields and fields not in self.anon:
                self.anon.append(fields)
        self.generic_visit(node)


def _collect_const_ints(tree) -> dict[str, int]:
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
            out[node.targets[0].id] = node.value.value
    return out


# Regex for validating a string assignment as a proper dotted table reference
# (e.g. "db.schema.table" or "my_table"). Strings that don't match this pattern
# (spaces, paths, temp labels) are rejected as write-target names.
_VALID_TABLE_REF_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
)


def _scan_tbl_write_vars(tree) -> set:
    """Variable names used directly as the first arg to saveAsTable or insertInto.

    Used by _DataFrameMiner to gate write-target resolution: a string-variable
    is only accepted as a TABLE write target if both (a) its value is a valid
    dotted table ref AND (b) the variable was actually passed to saveAsTable /
    insertInto (not just assigned and used elsewhere)."""
    out: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("saveAsTable", "insertInto") \
                and node.args and isinstance(node.args[0], ast.Name):
            out.add(node.args[0].id)
    return out


# ---------------------------------------------------------------------------
# Layer C + read/schema binding: role-aware DataFrame miner
# ---------------------------------------------------------------------------

class _DataFrameMiner(ast.NodeVisitor):
    def __init__(self, struct_schemas: dict[str, list[dict]], read_helpers: set[str] | None = None,
                 helper_schemas: dict[str, str] | None = None,
                 write_helpers: set[str] | None = None,
                 helper_readers: dict[str, str] | None = None):
        self.struct_schemas = struct_schemas
        self.read_helpers = read_helpers or set()    # fn names that wrap spark.read
        self.write_helpers = write_helpers or set()  # fn names that wrap df.write...save
        self.helper_schemas = helper_schemas or {}   # helper fn -> StructType var it applies
        self.helper_readers = helper_readers or {}   # helper fn -> real reader method (table/parquet/..)
        self.column_helpers: dict = {}               # helper fn -> (df_param_idx, frozenset(cols))
        self.str_vars: dict[str, str] = {}           # var -> name derived from its string/path value
        self.str_consts: dict[str, str] = {}         # var -> literal string value (for f-string folding)
        self.value_sets: dict[str, list] = {}        # var -> finite set of literal str values (loop/collection)
        self.dict_literals: dict[str, dict] = {}     # name -> {k: v} for dict-literal assigns
        self.list_literals: dict[str, list] = {}     # name -> [..] for list/tuple/set-literal assigns
        self.path_fstrings: dict[str, ast.JoinedStr] = {}  # path var -> its f-string node (dynamic-read detection)
        self.var_src: dict[str, str] = {}          # df var -> source id
        self.src_meta: dict[str, dict] = {}        # source id -> {method, name, schema_var, format}
        self.src_cols: dict[str, set] = {}         # source id -> input col names
        self.strong_cols: dict[str, set] = {}      # sid -> cols with strong evidence
                                                   # (qualified `df.col` ref / join key)
        self.outputs: set[str] = set()             # withColumn/alias outputs
        self.casts: dict[str, str] = {}            # col -> cast type
        self.agg_types: dict[str, str] = {}        # output col -> agg numeric type
        self.col_values: dict[str, set] = {}       # col -> literal domain from
                                                   # isin()/== filter predicates
                                                   # (so mock data satisfies the filter)
        self.join_edges: list = []                 # [((sidA, col), (sidB, col))]
                                                   # join links -> datagen value pools
        self.range_join_edges: list = []           # [((psid,pcol),(lsid,lcol),(hsid,hcol))]
                                                   # range/between join predicates
                                                   # (point BETWEEN low..high) -> datagen
                                                   # generates the linked cols jointly
        self.struct_out_types: dict[str, str] = {} # col -> type from a bound output StructType
        self.tempviews: dict[str, str] = {}        # view name -> source id
        self.col_fn_names: set[str] = set()        # aliases for F.col (e.g. f, F)
        self.var_cols: dict[str, set] = {}         # df var -> propagated column set
        self.joined_vars: set = set()              # df vars produced by a join (their
                                                   # bare-string projections are ambiguous
                                                   # across legs, so NOT attributed to a source)
        self.sinks: dict[str, set] = {}            # sink target name -> columns
        self.display_sites: list[dict] = []          # [{file, line, arg_src, base_var}]
        self.open_ended_sids: set = set()          # sources consumed open-endedly
        self.sql_fragment_cols: set = set()        # column identifiers from SQL fragments
        self._parent: dict = {}                    # child AST node -> parent (for call-site checks)
        self._n = 0
        self._def_depth = 0                        # >0 when inside a function/method body
        self._tbl_write_vars: set = set()          # var names used directly in saveAsTable/insertInto
        self._consumed_reads: set = set()          # id() of read-call nodes already
                                                   # turned into a source (dedup between
                                                   # _detect_read and the visit_Call catch-all)

    def visit(self, node):
        for child in ast.iter_child_nodes(node):
            self._parent[child] = node
        super().visit(node)

    def visit_FunctionDef(self, node):
        # track whether a read/write sits inside a def: I/O buried in an imported
        # module's function body is a *capability*, not confirmed entrypoint I/O.
        self._def_depth += 1
        self.generic_visit(node)
        self._def_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    # ---- helpers -----------------------------------------------------------
    def _attr_is_callee(self, attr: ast.Attribute) -> bool:
        """True when ``attr`` is the callee in ``<df>.<attr>(...)`` (API, not a column)."""
        parent = self._parent.get(attr)
        if not isinstance(parent, ast.Call):
            return False
        fn = parent.func
        while isinstance(fn, ast.Attribute):
            if fn is attr:
                return True
            fn = fn.value
        return fn is attr

    def _ctor(self, node):
        if isinstance(node, ast.Call):
            f = node.func
            return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        return None

    def _base_var(self, node):
        c = node
        while True:
            if isinstance(c, ast.Call):
                c = c.func
            elif isinstance(c, ast.Attribute):
                c = c.value
            elif isinstance(c, ast.Subscript):
                c = c.value
            elif isinstance(c, ast.Name):
                return c.id
            else:
                return None

    _PATHWORD_RE = re.compile(r"[A-Za-z_][\w]*")

    def _path_basename(self, p: str) -> str | None:
        # strip query/partition suffixes + URI schemes; take last meaningful segment
        segs = [s for s in p.rstrip("/").split("/")
                if s and "=" not in s and "{" not in s and not s.endswith(":")]
        if not segs:
            return None
        base = segs[-1].split(".")[0]
        return base or None

    def _resolve_fstring(self, node) -> str | None:
        """Reconstruct an f-string, substituting ``{NAME}`` interpolations bound to
        known module-level string constants (collected in ``str_consts``). Returns
        the resolved string, or None if a non-constant interpolation remains
        (so the caller can fall back to the literal-only guess)."""
        if not isinstance(node, ast.JoinedStr):
            return None
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                inner = v.value
                if isinstance(inner, ast.Name) and inner.id in self.str_consts:
                    parts.append(self.str_consts[inner.id])
                elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    parts.append(inner.value)
                else:
                    return None  # unresolved interpolation -> caller falls back
            else:
                return None
        return "".join(parts)

    @staticmethod
    def _flatten_concat(node) -> list:
        """Flatten a left-associative string `+` BinOp tree into its operand list,
        left to right: ``a + b + c`` -> ``[a, b, c]``. A non-Add node -> ``[node]``."""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return (_DataFrameMiner._flatten_concat(node.left)
                    + _DataFrameMiner._flatten_concat(node.right))
        return [node]

    # name origins that pin a table identity exactly vs. ones that GUESS it from a
    # path. Anything not "certain" is surfaced for LLM confirmation.
    _CERTAIN_ORIGINS = {"replace_token", "literal_name", "helper_token", "schema_var", "table_ref"}

    # generic DataFrame/format variable tokens that are NOT a table identity. A
    # schema var like `df_schema`/`csv_schema` strips to one of these -- using it as
    # the source name yields a useless, cross-entrypoint-colliding label, so we fall
    # back to the per-entrypoint `srcN` placeholder instead.
    _GENERIC_NAMES = {"df", "sdf", "pdf", "data", "dataset", "dataframe", "frame",
                      "rdd", "csv", "json", "parquet", "orc", "avro", "delta",
                      "table", "tbl", "view", "result", "results", "output", "out",
                      "input", "inp", "tmp", "temp", "source", "src", "target",
                      "dest", "row", "rows", "record", "records"}

    def _schema_var_name(self, schema_var):
        """Derive a table name from a `.schema(VAR)` binding (LOAN_MASTER_SCHEMA ->
        loan_master), but reject generic tokens (df_schema -> df) that are not a
        real table identity."""
        nm = re.sub(r"_?schema$", "", schema_var, flags=re.I).lower() or None
        return None if (nm in self._GENERIC_NAMES) else nm


    def _name_from_arg(self, node) -> tuple[str | None, str | None]:
        """Return (name, origin). origin records HOW the name was derived so the
        caller can mark it certain vs heuristic. Nothing hardcoded — these are the
        general shapes a read target takes."""
        # a variable previously bound to a string/path (inherits that var's origin)
        if isinstance(node, ast.Name) and node.id in self.str_vars:
            return self.str_vars[node.id]            # (name, origin) tuple
        # .replace('placeholder', 'TABLE') -> exact token
        for nn in ast.walk(node):
            if isinstance(nn, ast.Call) and isinstance(nn.func, ast.Attribute) \
                    and nn.func.attr == "replace" and len(nn.args) >= 2 \
                    and isinstance(nn.args[1], ast.Constant):
                return str(nn.args[1].value), "replace_token"
        # literal string: a bare identifier is an exact table name; a path is a guess
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if "/" not in v and self._PATHWORD_RE.fullmatch(v.split(".")[-1]):
                return v.split(".")[-1], "literal_name"
            return self._path_basename(v), "path_basename"
        # f-string path -> fold any {CONST} interpolations bound to module-level
        # string constants (e.g. f'{CATALOG}.{L0_SCHEMA}.my_tbl'), then take the
        # bare table name. This turns the common constant-prefixed table path from
        # a "dynamic path" guess into an exact name.
        if isinstance(node, ast.JoinedStr):
            resolved = self._resolve_fstring(node)
            if resolved is not None and "/" not in resolved:
                tail = resolved.split(".")[-1].strip()
                if tail and self._PATHWORD_RE.fullmatch(tail):
                    return tail, "literal_name"
            # dotted table ref with unresolved {VAR} qualifiers (e.g.
            # f'{DB}.{SCHEMA}.BROKERS'): the trailing literal dot-segment is the
            # static table identity even though catalog/schema vary at runtime.
            # Only for true table refs (no '/' anywhere) -- a filesystem path that
            # ends in a literal extension (f's3://…/data_{d}.csv') is NOT a table.
            lit = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
            last = node.values[-1] if node.values else None
            if "/" not in lit and isinstance(last, ast.Constant) \
                    and isinstance(last.value, str):
                tail = last.value.split(".")[-1].strip()
                if tail and self._PATHWORD_RE.fullmatch(tail):
                    return tail, "literal_name"
            return self._path_basename(lit), "path_basename"
        # string concatenation: `PREFIX + '.tbl'` / `cat + '.' + name` -> the
        # trailing literal dotted segment is the static table identity (a runtime
        # schema/catalog prefix var is irrelevant for mocking). Mirrors the
        # JoinedStr `f'{DB}.{SCHEMA}.tbl'` handling above. This is what turns
        # `spark.table(_SCHEMA + '.my_table')` from an unresolved read into an
        # exact table name.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            parts = self._flatten_concat(node)
            lit = "".join(p.value for p in parts
                          if isinstance(p, ast.Constant) and isinstance(p.value, str))
            last = parts[-1] if parts else None
            if "/" not in lit and isinstance(last, ast.Constant) \
                    and isinstance(last.value, str):
                tail = last.value.split(".")[-1].strip()
                if tail and self._PATHWORD_RE.fullmatch(tail):
                    return tail, "literal_name"
            # otherwise the LAST str-var operand carries the name — in
            # `PREFIX + '.' + tbl` the trailing operand is the table, not the
            # leading schema/catalog prefix.
            for p in reversed(parts):
                if isinstance(p, ast.Name) and p.id in self.str_vars:
                    return self.str_vars[p.id]
        # os.environ.get('SCOS_INPUT_X', <default path>) -> use the default
        if isinstance(node, ast.Call):
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else None)
            if fn == "get" and len(node.args) >= 2:
                return self._name_from_arg(node.args[1])
            # helper fn: build_path("vehicle_pings", ...) -> first id-like string arg
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                        and self._PATHWORD_RE.fullmatch(a.value):
                    return a.value, "helper_token"
        return None, None

    def _name_hint(self, read_call, schema_var) -> tuple[str | None, str | None]:
        if read_call.args:
            n, origin = self._name_from_arg(read_call.args[0])
            if n:
                return n, origin
        if schema_var:  # e.g. LOAN_MASTER_SCHEMA -> loan_master
            nm = self._schema_var_name(schema_var)
            if nm:
                return nm, "schema_var"
        return None, None

    def _read_fanout(self, read_call):
        """(dynamic, values) for a read call's path arg -- resolves a path var to
        its f-string. dynamic=True means a parameterized/looping read."""
        if not getattr(read_call, "args", None):
            return (False, None)
        a = read_call.args[0]
        fs = a if isinstance(a, ast.JoinedStr) else (
            self.path_fstrings.get(a.id) if isinstance(a, ast.Name) else None)
        return self._fstring_fanout(fs) if fs is not None else (False, None)

    def _detect_read(self, node):
        # (1) helper-wrapped read: var = spark_read_parquet(spark, path) / load_x(...)
        if isinstance(node, ast.Call):
            fn = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if fn and fn in self.read_helpers:
                # the helper's REAL reader method (from its body) — e.g. a
                # `spark_read_parquet` patch that actually does `spark.read.table`.
                real_method = self.helper_readers.get(fn)
                name = None
                name_origin = None
                fmt = real_method or ("parquet" if "parquet" in fn.lower() else (
                    "csv" if "csv" in fn.lower() else (
                        "json" if "json" in fn.lower() else "parquet")))
                for a in node.args + [k.value for k in node.keywords]:
                    name, name_origin = self._name_from_arg(a)
                    if name:
                        break
                    if isinstance(a, ast.Name) and re.search(r"_(path|uri|dir|loc|location)$", a.id, re.I):
                        name = re.sub(r"_(path|uri|dir|loc|location)$", "", a.id, flags=re.I)
                        name_origin = "var_name"
                        break
                # inherit a StructType the helper applies internally (cross-file)
                schema_var = self.helper_schemas.get(fn)
                if not name and schema_var:
                    name = self._schema_var_name(schema_var)
                    name_origin = "schema_var" if name else None
                sid = f"src{self._n}"; self._n += 1
                self.src_meta[sid] = {"method": real_method or fn, "format": fmt,
                                      "name": name, "name_origin": name_origin,
                                      "schema_var": schema_var, "via_helper": fn,
                                      "in_def": self._def_depth > 0,
                                      "lineno": getattr(node, "lineno", None)}
                self.src_cols[sid] = set()
                return sid
        # (2) direct spark.read.* / spark.table — the first reader call reached in
        # this expression. Any ADDITIONAL reads in the same expression (e.g. both
        # legs of an inline `a.join(spark.table('x')).join(spark.table('y'))`) are
        # picked up by visit_Call's read catch-all, which skips whatever is already
        # consumed here.
        for nn in ast.walk(node):
            if isinstance(nn, ast.Call) and isinstance(nn.func, ast.Attribute) \
                    and nn.func.attr in _READ_METHODS:
                sid = self._src_from_read_call(nn)
                if sid:
                    return sid
        return None

    def _src_from_read_call(self, nn):
        """Build (once) a source-meta entry for a direct ``spark.read.*`` /
        ``spark.table`` call node and return its sid, or None when ``nn`` is not a
        genuine reader call (fails the receiver-chain guard) or was already consumed.
        Shared by ``_detect_read`` (assignment RHS) and ``visit_Call`` (subscript
        targets, extra join-chain legs, bare read statements)."""
        if id(nn) in self._consumed_reads:
            return None
        # walk the receiver chain through BOTH attributes and calls
        # (handles spark.read.schema(...).option(...).parquet(...))
        chain = []
        c = nn.func.value
        while True:
            if isinstance(c, ast.Attribute):
                chain.append(c.attr); c = c.value
            elif isinstance(c, ast.Call):
                c = c.func
            elif isinstance(c, ast.Subscript):
                c = c.value
            else:
                break
        if isinstance(c, ast.Name):
            chain.append(c.id)
        # `.read.*` reads require a spark/read receiver. A bare `.table(...)` must
        # also sit on a session-like receiver (spark / *session* / sqlContext /
        # hiveContext) — otherwise a non-Spark `ax.table(...)` / `obj.table(...)`
        # would register a spurious source now that the visit_Call catch-all runs
        # this on every `.table` call, not just assignment RHS.
        chain_l = [str(x).lower() for x in chain]
        if nn.func.attr == "table":
            # session-like receiver: a known token, a name ending in 'spark'
            # (spark / myspark / self.spark), or a *session* var — but NOT a loose
            # substring like 'sparkles' that merely contains 'spark'.
            sessionish = any(
                x in ("spark", "sparksession", "sqlcontext", "sqlctx",
                      "hivecontext", "hive_context")
                or x.endswith("spark") or "session" in x
                for x in chain_l
            )
            if not ("read" in chain_l or sessionish):
                return None
        elif not ("read" in chain_l or "spark" in chain_l):
            return None
        fmt = nn.func.attr
        schema_var = None
        reader_opts: dict = {}
        # scan the reader chain (nn's own subtree — not the wider expression, so a
        # sibling read in a join chain never leaks its format/options in here).
        for w in ast.walk(nn):
            if isinstance(w, ast.Call) and isinstance(w.func, ast.Attribute):
                if w.func.attr == "format" and w.args and isinstance(w.args[0], ast.Constant):
                    fmt = str(w.args[0].value)
                if w.func.attr == "schema" and w.args and isinstance(w.args[0], ast.Name):
                    schema_var = w.args[0].id
                # mine reader options (delimiter/header/encoding/…) so datagen
                # and Snowflake COPY INTO agree with the workload's reader.
                if w.func.attr == "option" and len(w.args) == 2 \
                        and isinstance(w.args[0], ast.Constant):
                    k = str(w.args[0].value).lower()
                    if k in _READER_OPT_KEYS and isinstance(w.args[1], ast.Constant):
                        reader_opts[k] = _opt_value(w.args[1].value)
                if w.func.attr == "options":
                    for kw in w.keywords:
                        if kw.arg and kw.arg.lower() in _READER_OPT_KEYS \
                                and isinstance(kw.value, ast.Constant):
                            reader_opts[kw.arg.lower()] = _opt_value(kw.value.value)
        sid = f"src{self._n}"; self._n += 1
        # a literal table identifier in spark.table("X") is an exact name
        hint, origin = self._name_hint(nn, schema_var)
        if nn.func.attr == "table" and origin == "literal_name":
            origin = "table_ref"
        # corroboration: a path-guessed name that AGREES with the bound
        # StructType var name (loan_master/ + LOAN_MASTER_SCHEMA) is certain.
        if origin == "path_basename" and schema_var and hint:
            svname = re.sub(r"_?schema$", "", schema_var, flags=re.I)
            if re.sub(r"[^a-z0-9]", "", hint.lower()) == re.sub(r"[^a-z0-9]", "", svname.lower()):
                origin = "schema_var"
        self.src_meta[sid] = {"method": nn.func.attr, "format": fmt,
                              "name": hint, "name_origin": origin,
                              "schema_var": schema_var,
                              "reader_options": reader_opts,
                              "in_def": self._def_depth > 0,
                              "lineno": getattr(nn, "lineno", None)}
        dyn, fan = self._read_fanout(nn)
        if dyn:
            self.src_meta[sid]["dynamic_read"] = True
            self.src_meta[sid]["fanout"] = fan
        self.src_cols[sid] = set()
        self._consumed_reads.add(id(nn))
        return sid

    def _file_basename_from_arg(self, node):
        """Best-effort file basename (extension stripped) from a read-path arg:
        a string literal, an f-string, or a ``dir + 'name.csv'`` concatenation."""
        if isinstance(node, ast.JoinedStr):
            resolved = self._resolve_fstring(node)
            if resolved:
                return self._path_basename(resolved)
        parts = self._flatten_concat(node)
        lit = "".join(p.value for p in parts
                      if isinstance(p, ast.Constant) and isinstance(p.value, str))
        return self._path_basename(lit) if lit else None

    def _src_from_pandas_read(self, node):
        """Register a file source for a pandas read (``pd.read_csv`` / ``read_parquet``
        / …). pandas reads never touch spark.read, so without this the file is invisible
        to the miner and surfaces as a runtime FileNotFound. Also fires for the read
        nested in ``spark.createDataFrame(pd.read_csv(...))``."""
        if id(node) in self._consumed_reads:
            return None
        fmt = _PANDAS_READ_METHODS.get(node.func.attr, "csv")
        name = self._file_basename_from_arg(node.args[0]) if node.args else None
        sid = f"src{self._n}"; self._n += 1
        self.src_meta[sid] = {"method": node.func.attr, "format": fmt,
                              "name": name,
                              "name_origin": "path_basename" if name else None,
                              "schema_var": None, "reader_options": {},
                              "in_def": self._def_depth > 0,
                              "lineno": getattr(node, "lineno", None)}
        self.src_cols[sid] = set()
        self._consumed_reads.add(id(node))
        return sid

    # ---- visitors ----------------------------------------------------------
    def visit_ImportFrom(self, node):
        # capture `from pyspark.sql import functions as f` so f.col() is found
        if node.module and node.module.endswith("functions"):
            for a in node.names:
                self.col_fn_names.add(a.asname or a.name)
        self.generic_visit(node)

    def _literal_str(self, node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def _iter_value_sets(self, it):
        """Resolve a for-iterable to (keys, values) literal lists where known.
        Handles dict-literal .items()/.keys()/.values(), a Name bound to a dict/
        list literal, and inline list/tuple/set literals. Loop SHAPE-agnostic:
        the same value-sets feed while/comprehension resolution too."""
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) \
                and isinstance(it.func.value, ast.Name):
            d = self.dict_literals.get(it.func.value.id)
            if d is not None:
                if it.func.attr == "items":
                    return list(d.keys()), list(d.values())
                if it.func.attr == "keys":
                    return list(d.keys()), None
                if it.func.attr == "values":
                    return list(d.values()), None
        if isinstance(it, ast.Name):
            if it.id in self.dict_literals:
                return list(self.dict_literals[it.id].keys()), None
            if it.id in self.list_literals:
                return list(self.list_literals[it.id]), None
        if isinstance(it, (ast.List, ast.Tuple, ast.Set)):
            vals = [self._literal_str(e) for e in it.elts]
            if vals and all(v is not None for v in vals):
                return vals, None
        return None, None

    def visit_For(self, node):
        keys, vals = self._iter_value_sets(node.iter)
        tgt = node.target
        if isinstance(tgt, (ast.Tuple, ast.List)) and keys is not None and vals is not None \
                and len(tgt.elts) >= 2:
            if isinstance(tgt.elts[0], ast.Name):
                self.value_sets[tgt.elts[0].id] = keys
            if isinstance(tgt.elts[1], ast.Name):
                self.value_sets[tgt.elts[1].id] = vals
        elif isinstance(tgt, ast.Name) and keys is not None:
            self.value_sets[tgt.id] = keys
        self.generic_visit(node)

    def _fstring_fanout(self, node):
        """For an f-string used as a read path, classify the TABLE-IDENTITY segment:
          (False, None)         -> table identity is a constant (e.g. a fixed table
                                   with only a runtime partition/date -> NOT a fan-out)
          (True, [v1, v2, ...]) -> table identity is a known value-set (fan-out)
          (True, None)          -> table identity is a runtime value (can't enumerate)
        Only the last meaningful path segment (the table) is considered; root and
        ``key=val`` partition segments are ignored. Control-flow-agnostic."""
        if not isinstance(node, ast.JoinedStr):
            return (False, None)

        def classify(fv):
            if isinstance(fv, ast.Name) and fv.id in self.value_sets:
                return ("set", self.value_sets[fv.id])
            if isinstance(fv, ast.Name) and fv.id in self.str_consts:
                return ("lit", self.str_consts[fv.id])
            if isinstance(fv, ast.Constant):
                return ("lit", str(fv.value))
            return ("unknown", None)

        # split the f-string into hierarchy segments, each a list of pieces. Paths use
        # '/'; dotted table refs (catalog.schema.table from spark.table) use '.' -- pick
        # '.' only when there are no slashes so file extensions ('file.parquet') aren't
        # mistaken for a table identity.
        whole_lit = "".join(str(v.value) for v in node.values
                            if isinstance(v, ast.Constant) and isinstance(v.value, str))
        sep = "/" if "/" in whole_lit else "."
        segments = [[]]
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts = str(v.value).split(sep)
                segments[-1].append(("lit", parts[0]))
                for p in parts[1:]:
                    segments.append([("lit", p)])
            elif isinstance(v, ast.FormattedValue):
                segments[-1].append(classify(v.value))

        # find the last MEANINGFUL segment = the table identity (skip empty, glob,
        # and key=val partition segments)
        def seg_littext(seg):
            return "".join(p[1] for p in seg if p[0] == "lit" and p[1])

        table_seg = None
        for seg in reversed(segments):
            lit = seg_littext(seg)
            has_interp = any(p[0] in ("set", "unknown") for p in seg)
            if "=" in lit:
                continue                      # key=val partition segment
            if not has_interp:
                if not lit.strip() or lit.strip("*") == "":
                    continue                  # empty / glob literal-only segment
                table_seg = seg               # constant table identity
                break
            table_seg = seg                   # segment carries the table interp
            break
        if table_seg is None:
            return (False, None)

        sets = [p[1] for p in table_seg if p[0] == "set"]
        if sets:
            import itertools as _it
            names = set()
            for combo in _it.product(*sets):
                for v in combo:
                    names.add(self._path_basename(v) or v)
            return (True, sorted(names))
        if any(p[0] == "unknown" for p in table_seg):
            return (True, None)
        return (False, None)                  # constant table identity

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tgt = node.targets[0].id
            # remember plain string constants so f-string table paths that
            # interpolate them (f'{CATALOG}.{SCHEMA}.tbl') resolve to real names.
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.str_consts[tgt] = node.value.value
            # remember dict / list / set literals of strings, so a loop over them
            # gives a known finite value-set for the iterated variable.
            if isinstance(node.value, ast.Dict):
                kv = {}
                for k, v in zip(node.value.keys, node.value.values):
                    ks, vs = self._literal_str(k), self._literal_str(v)
                    if ks is not None and vs is not None:
                        kv[ks] = vs
                if kv:
                    self.dict_literals[tgt] = kv
            elif isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                vals = [self._literal_str(e) for e in node.value.elts]
                if vals and all(v is not None for v in vals):
                    self.list_literals[tgt] = vals
            # remember the f-string node behind a path var, for dynamic-read detection.
            if isinstance(node.value, ast.JoinedStr):
                self.path_fstrings[tgt] = node.value
            # remember string/path variables so a later read(<var>) can be named
            # from the path value (e.g. input_path = f"s3://.../daily_fact/dt=...").
            # Also a path var built by a helper call -- `target_path =
            # stagingPathFor(cat, sch, "grp", "trip_start_events")` -- so a later
            # `.save(target_path)` / read(target_path) resolves a name; gated on a
            # path-like target name so DataFrame vars are never mis-tagged.
            path_like_call = (isinstance(node.value, ast.Call)
                              and re.search(r"(path|uri|dir|_loc|location|target|dest|output|file)$",
                                            tgt, re.I) is not None)
            if isinstance(node.value, (ast.Constant, ast.JoinedStr)) or path_like_call or (
                    isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr in ("format", "replace")):
                nm, origin = self._name_from_arg(node.value)
                if nm:
                    self.str_vars[tgt] = (nm, origin)
            sid = self._detect_read(node.value)
            if sid:
                self.var_src[tgt] = sid
                self.var_cols[tgt] = set()
                # seed propagated cols from a bound StructType
                sv = self.src_meta[sid].get("schema_var")
                if sv and sv in self.struct_schemas:
                    self.var_cols[tgt] = {f["name"] for f in self.struct_schemas[sv]}
            else:
                b = self._base_var(node.value)
                if b in self.var_src:
                    self.var_src[tgt] = self.var_src[b]
                    self._harvest_cols(node.value, self.var_src[b])
                # propagate column set through the transformation chain
                self.var_cols[tgt] = self._propagate_cols(node.value)
                # Track join provenance: once a var is the result of a join (or is
                # derived from one), its bare-string projections are ambiguous across
                # the joined legs, so they must NOT be attributed back to a source
                # table (that is the root cause of mock-schema column over-inclusion).
                if self._expr_has_join(node.value) or b in self.joined_vars:
                    self.joined_vars.add(tgt)
        self.generic_visit(node)

    def _expr_has_join(self, node) -> bool:
        """True iff the transformation chain of ``node`` applies a ``.join`` to the
        base var (walks only the receiver chain, not into join arguments)."""
        c = node
        while isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute):
            if c.func.attr == "join":
                return True
            c = c.func.value
        return False


    def _propagate_cols(self, node) -> set:
        """Best-effort column set of the DataFrame expression `node`."""
        cols: set = set()
        b = self._base_var(node)
        if b in self.var_cols:
            cols = set(self.var_cols[b])
        # apply chained ops outermost->in (walk calls on this expression)
        ops = []
        c = node
        while isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute):
            ops.append(c)
            c = c.func.value
        group_keys: set = set()
        for call in reversed(ops):
            fn = call.func.attr
            if fn in ("groupBy", "groupby", "rollup", "cube"):
                group_keys = set()
                for a in call.args:
                    group_keys |= self._strings(a)
            elif fn == "agg":
                # the result of groupBy().agg() is the group keys + the aliased
                # aggregate outputs -- NOT the input columns.
                aliases = set()
                for a in list(call.args) + [k.value for k in call.keywords]:
                    for n in ast.walk(a):
                        if isinstance(n, ast.Call) and self._ctor(n) in ("alias", "name") \
                                and n.args and isinstance(n.args[0], ast.Constant):
                            aliases.add(str(n.args[0].value))
                cols = set(group_keys) | aliases
            elif fn in ("applyInPandas", "mapInPandas", "applyInPandasWithState"):
                # output schema is an explicit StructType arg (positional or schema=)
                fields = self._schema_fields_of(call)
                if fields:
                    cols = set(fields)
            elif fn in ("select", "selectExpr"):
                sel = set()
                for a in call.args:
                    sel |= self._strings(a)
                    # alias outputs inside select
                    for n in ast.walk(a):
                        if isinstance(n, ast.Call) and self._ctor(n) in ("alias", "name") \
                                and n.args and isinstance(n.args[0], ast.Constant):
                            sel.add(str(n.args[0].value))
                if sel:
                    cols = sel
            elif fn == "withColumn" and call.args and isinstance(call.args[0], ast.Constant):
                cols.add(str(call.args[0].value))
            elif fn == "withColumnRenamed" and len(call.args) >= 2 \
                    and isinstance(call.args[1], ast.Constant):
                cols.discard(call.args[0].value if isinstance(call.args[0], ast.Constant) else None)
                cols.add(str(call.args[1].value))
            elif fn == "drop":
                for a in call.args:
                    if isinstance(a, ast.Constant):
                        cols.discard(a.value)
            elif fn in ("join", "union", "unionByName", "unionAll"):
                other = None
                if call.args:
                    other = self._base_var(call.args[0])
                if other in self.var_cols:
                    cols |= self.var_cols[other]
        return cols

    def _schema_fields_of(self, call) -> list[str]:
        """Field names of a StructType passed to applyInPandas/createDataFrame --
        either a Name bound to a StructType literal (in struct_schemas) or an
        inline ``StructType([StructField('x', ...), ...])``."""
        cand = []
        if len(call.args) >= 2:
            cand.append(call.args[-1])
        for kw in call.keywords:
            if kw.arg == "schema":
                cand.append(kw.value)
        for node in cand:
            if isinstance(node, ast.Name) and node.id in self.struct_schemas:
                fields = self.struct_schemas[node.id]
                for f in fields:
                    if f.get("type"):
                        self.struct_out_types[f["name"]] = f["type"]
                return [f["name"] for f in fields]
            if isinstance(node, ast.Call):
                names = []
                for n in ast.walk(node):
                    if isinstance(n, ast.Call) and self._ctor(n) == "StructField" \
                            and n.args and isinstance(n.args[0], ast.Constant):
                        names.append(str(n.args[0].value))
                if names:
                    return names
        return []

    def _is_write_chain(self, node) -> bool:
        c = node
        while isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute):
            if c.func.attr == "write":
                return True
            c = c.func.value
            while isinstance(c, ast.Attribute):
                if c.attr == "write":
                    return True
                c = c.value
        return False

    def _write_base_var(self, node):
        # leftmost Name in a `<var>.write....` chain
        return self._base_var(node)

    def _pre_write_expr(self, node):
        """Given a write call (``<df>.write[.mode(..)..].saveAsTable(..)``), return
        the DataFrame expression node that ``.write`` is attached to, so its column
        set can be propagated. Returns None if no ``.write`` is found (e.g. a writer
        method invoked directly on a df var)."""
        c = node
        while isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute):
            inner = c.func.value
            # walk attribute/`.write` chain between the call and the df expr
            n = inner
            while isinstance(n, (ast.Attribute, ast.Call)):
                if isinstance(n, ast.Attribute) and n.attr == "write":
                    return n.value
                n = n.func.value if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) else (
                    n.value if isinstance(n, ast.Attribute) else None)
                if n is None:
                    break
            c = inner
        return None

    def _harvest_cols(self, node, sid):
        if sid not in self.src_cols:
            return
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                fn = self._ctor(n)
                if fn in ("col", "column") and n.args and isinstance(n.args[0], ast.Constant):
                    name = str(n.args[0].value)
                    # only the bare column name (strip any .field access handled elsewhere)
                    if re.fullmatch(r"[A-Za-z_][\w]*", name) and name not in self.outputs:
                        self.src_cols[sid].add(name)

    def _first_col_in(self, node):
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                fn = self._ctor(n)
                if fn in ("col", "column") and n.args and isinstance(n.args[0], ast.Constant):
                    return str(n.args[0].value)
        return None

    def _col_name_of(self, node):
        """Best-effort column name for a predicate receiver: ``col('X')`` /
        ``F.col('X')`` (found anywhere in the subtree), ``df['X']`` subscript,
        or a bare attribute ``df.X``. Used to attribute filter literals to the
        column they constrain."""
        cn = self._first_col_in(node)
        if cn:
            return cn
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            return node.slice.value
        if isinstance(node, ast.Attribute) and not node.attr.startswith("_") \
                and node.attr not in _DF_PROPERTY_ATTRS and isinstance(node.value, ast.Name):
            return node.attr
        return None

    def _is_negated(self, node) -> bool:
        """True when ``node`` sits directly under a ``~`` (Invert) -- e.g.
        ``~col('X').isin(...)`` -- so a membership test is actually an EXCLUSION."""
        p = self._parent.get(node)
        return isinstance(p, ast.UnaryOp) and isinstance(p.op, ast.Invert)

    def _literal_values(self, nodes) -> list:
        """Collect scalar literals (str/int/float/bool) from predicate args:
        ``isin('a','b')``, ``isin(['a','b'])``, ``isin(*var)``, ``isin(var)``.
        ``ast.Starred`` and bare ``ast.Name`` resolve against ``list_literals`` /
        ``value_sets`` mined elsewhere. Returns [] when nothing is statically known."""
        out: list = []

        def scalar(n):
            if isinstance(n, ast.Constant) and isinstance(n.value, (str, int, float, bool)):
                out.append(n.value)
                return True
            return False

        def from_name(name: str):
            for store in (self.list_literals, self.value_sets):
                if name in store and store[name]:
                    out.extend(v for v in store[name] if v is not None)
                    return True
            return False

        for n in nodes:
            if scalar(n):
                continue
            if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
                for e in n.elts:
                    if not scalar(e) and isinstance(e, ast.Name):
                        from_name(e.id)
            elif isinstance(n, ast.Starred) and isinstance(n.value, ast.Name):
                from_name(n.value.id)
            elif isinstance(n, ast.Name):
                from_name(n.id)
        # de-dup, preserve order
        return list(dict.fromkeys(out))

    def _literal_of(self, node):
        """Scalar literal from a predicate operand, resolving the forms the bare
        ``ast.Constant`` check misses: ``lit('X')`` / ``F.lit('X')`` wrappers and a
        ``Name`` bound to a known string constant (``x = 'AAA'; col('c') == x``).
        Returns None when nothing is statically known (V2)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
            return node.value
        if isinstance(node, ast.Call) and self._ctor(node) == "lit" and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, (str, int, float, bool)):
                return a0.value
        if isinstance(node, ast.Name) and node.id in self.str_consts:
            return self.str_consts[node.id]
        return None

    def _seed_string_predicate_values(self, text: str) -> set:
        """Harvest pinned filter literals from a STRING-form predicate passed to
        ``.filter("col == 'X'")`` / ``.where("col = 'X'")`` / ``col IN ('a','b')``
        and seed them as the column's ``values`` domain, mirroring the AST isin/==
        miner. Only positive constraints (``=``/``==``/``IN``) are seeded; ``!=``,
        ``<>``, ``<=``, ``>=`` are ignored so the mock keeps rows. Returns the set of
        column names seeded so the caller can attribute them to the source (V2)."""
        seeded: set = set()
        # equality: col == 'X' / col = 'X' / col == 5. Guard against <=, >=, !=, <>
        # by requiring the '='/'==' operator to not be adjacent to <, >, !.
        for m in re.finditer(
                r"""(?<![<>!=])\b([A-Za-z_]\w*)\s*={1,2}(?!=)\s*"""
                r"""('([^']*)'|"([^"]*)"|(-?\d+(?:\.\d+)?))""", text):
            col = m.group(1)
            if col.lower() in _TYPE_KEYWORDS:
                continue
            if m.group(3) is not None:
                val: object = m.group(3)
            elif m.group(4) is not None:
                val = m.group(4)
            else:
                num = m.group(5)
                val = float(num) if "." in num else int(num)
            self.col_values.setdefault(col, set()).add(val)
            seeded.add(col)
        # membership: col IN ('a', 'b', 3). Skip NOT IN (negation).
        for m in re.finditer(r"(?i)\b([A-Za-z_]\w*)\s+(not\s+)?in\s*\(([^)]*)\)", text):
            if m.group(2):                        # NOT IN -> exclusion, skip
                continue
            col = m.group(1)
            if col.lower() in _TYPE_KEYWORDS:
                continue
            for lit in re.finditer(r"'([^']*)'|\"([^\"]*)\"|(-?\d+(?:\.\d+)?)", m.group(3)):
                if lit.group(1) is not None:
                    self.col_values.setdefault(col, set()).add(lit.group(1))
                elif lit.group(2) is not None:
                    self.col_values.setdefault(col, set()).add(lit.group(2))
                else:
                    n = lit.group(3)
                    self.col_values.setdefault(col, set()).add(float(n) if "." in n else int(n))
                seeded.add(col)
        return seeded

    def _ref_of(self, node):
        """(sid, col) for a column operand of a range predicate, else None (V1)."""
        col = self._col_name_of(node)
        if not col:
            return None
        sid = self.var_src.get(self._base_var(node))
        if sid is None:
            return None
        return (sid, col)

    def _collect_range_edges(self, expr) -> None:
        """Record range-join predicates as (point, low, high) ref triples: a
        ``col.between(low, high)`` call, or a paired inequality where one column P
        is bounded on both sides (``low <= P`` and ``P <= high``). Bounds must live
        on a different source than the point (a genuine range JOIN), else skipped
        (V1)."""
        # between(low, high)
        for n in ast.walk(expr):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "between" and len(n.args) == 2:
                point = self._ref_of(n.func.value)
                low = self._ref_of(n.args[0])
                high = self._ref_of(n.args[1])
                if point and low and high and point[0] != low[0]:
                    self.range_join_edges.append((point, low, high))
        # paired inequalities normalized to (lower_ref, upper_ref)
        ineqs: list = []
        for n in ast.walk(expr):
            if isinstance(n, ast.Compare) and len(n.ops) == 1 \
                    and isinstance(n.ops[0], (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                l = self._ref_of(n.left)
                r = self._ref_of(n.comparators[0])
                if not l or not r:
                    continue
                if isinstance(n.ops[0], (ast.Lt, ast.LtE)):
                    ineqs.append((l, r))          # l <= r
                else:
                    ineqs.append((r, l))          # l >= r  =>  r <= l
        for (lo1, hi1) in ineqs:
            for (lo2, hi2) in ineqs:
                # P = hi1 = lo2 is bounded below by lo1 and above by hi2
                if hi1 == lo2 and hi1 != lo1 and hi1 != hi2:
                    point, low, high = hi1, lo1, hi2
                    if point[0] != low[0] and point[0] != high[0]:
                        self.range_join_edges.append((point, low, high))

    def _strings(self, node):
        """Bare string column names referenced by `node`, EXCLUDING string args
        that are values/patterns/types rather than column names (lit, isin, when,
        cast, like, ...). Recurses with subtree pruning -- ``ast.walk`` would still
        descend into a ``lit('X')`` and harvest 'X' as a phantom column, which is
        exactly the category-value-as-column bug."""
        out = set()

        def rec(n):
            if isinstance(n, ast.Call) and self._ctor(n) in _NON_INPUT_CALLS:
                return  # prune the whole subtree of a value/IO call
            if isinstance(n, ast.Compare):
                # `col('x') == 'B2C'` -> 'B2C' is a VALUE, not a column. Recurse
                # into expression operands (col()/nested calls) but skip bare
                # string-literal operands. Real columns still arrive via col().
                for operand in [n.left, *n.comparators]:
                    if not isinstance(operand, ast.Constant):
                        rec(operand)
                return
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                s = n.value
                if re.fullmatch(r"[A-Za-z_][\w]*", s) and s.lower() not in _TYPE_KEYWORDS \
                        and s.lower() not in _JOIN_TYPES:
                    out.add(s)
            for child in ast.iter_child_nodes(n):
                rec(child)

        rec(node)
        return out

    def _parse_sql_fragment_columns(self, sql_text: str) -> set[str]:
        """Column identifiers referenced in a SQL fragment, via sqlglot.

        For ``filter``/``where``/``selectExpr``/``expr`` string arguments that hold
        SQL expressions rather than bare column names. Empty when sqlglot is
        absent or the fragment does not parse."""
        try:
            import sqlglot
            from sqlglot import exp
            from sqlglot.errors import SqlglotError
        except ImportError:
            return set()

        try:
            tree = sqlglot.parse_one(f"SELECT {sql_text}", dialect="spark")
        except SqlglotError:
            try:
                tree = sqlglot.parse_one(sql_text, dialect="spark")
            except SqlglotError:
                return set()
        if tree is None:
            return set()
        return {col.name for col in tree.find_all(exp.Column) if col.name}

    def visit_Call(self, node):
        fn = self._ctor(node)
        base = self._base_var(node)
        sid = self.var_src.get(base)

        # Read catch-all: a direct spark.table / spark.read.* call that visit_Assign's
        # _detect_read did NOT consume — a read assigned to a subscript target
        # (`dfMap['k'] = spark.table(...)`), an extra leg of an inline join chain
        # (`a.join(spark.table('x')).join(spark.table('y'))`, where _detect_read
        # returns only the first read), or a bare read expression statement.
        # Registering the source here makes the table appear in the mined schema and
        # get mocked, instead of surfacing as a runtime TABLE_OR_VIEW_NOT_FOUND. The
        # helper is idempotent (guards on _consumed_reads) and returns None for
        # writes / non-reader chains, so this is safe to call unconditionally.
        if isinstance(node.func, ast.Attribute) and node.func.attr in _READ_METHODS \
                and id(node) not in self._consumed_reads:
            self._src_from_read_call(node)

        # pandas file read: pd.read_csv(path) / pandas.read_parquet(...). Registers a
        # file source (see _src_from_pandas_read) so the CSV/parquet the workload reads
        # via pandas is mocked instead of surfacing as a runtime FileNotFound.
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PANDAS_READ_METHODS \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in _PANDAS_ALIASES:
            self._src_from_pandas_read(node)

        # outputs
        if fn == "withColumn" and node.args and isinstance(node.args[0], ast.Constant):
            self.outputs.add(str(node.args[0].value))
        if fn in ("alias", "name") and node.args and isinstance(node.args[0], ast.Constant):
            self.outputs.add(str(node.args[0].value))
            # type of an aliased aggregate (count(..).alias("n") -> n is long;
            # sum(..).alias("s") -> double) so groupBy().agg() sink columns are
            # typed, not left as string.
            alias_name = str(node.args[0].value)
            for inner in ast.walk(node.func.value if isinstance(node.func, ast.Attribute) else node):
                if isinstance(inner, ast.Call):
                    ic = self._ctor(inner)
                    if ic in _AGG_FUNCS_LONG:
                        self.agg_types[alias_name] = _AGG_FUNCS_LONG[ic]; break
                    if ic in _AGG_FUNCS_NUMERIC:
                        self.agg_types[alias_name] = _AGG_FUNCS_NUMERIC[ic]; break
        if fn == "withColumnRenamed" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            self.outputs.add(str(node.args[1].value))
        # Track selectExpr "col AS alias" outputs so join-edge alias warnings fire correctly.
        if fn == "selectExpr":
            for _a in node.args:
                _s = self._literal_str(_a)
                if _s:
                    _m = re.match(r"(?i).*\bas\s+(\w+)\s*$", _s.strip())
                    if _m:
                        self.outputs.add(_m.group(1))

        # cast type inference
        if fn == "cast" and node.args and isinstance(node.args[0], ast.Constant):
            bc = self._first_col_in(node.func.value)
            if bc:
                self.casts[bc] = _norm_type(str(node.args[0].value))

        # filter-predicate literal domain: `col('X').isin('a','b')` (or .isin([...]),
        # .isin(*var), .isin(var)) constrains X to those values. Seed them as X's
        # `values` so a `.filter(col('X').isin(...))` actually KEEPS rows in the mock
        # (an empty filter result silently collapses every downstream join/output).
        # Captures both standalone filters and `when(col('X').isin(...), ...)`.
        # Skip negated membership (`~col('X').isin(...)`) -- those values are
        # EXCLUDED, so seeding them as the domain would empty the mock.
        if fn == "isin" and isinstance(node.func, ast.Attribute) \
                and not self._is_negated(node):
            cn = self._col_name_of(node.func.value)
            vals = self._literal_values(node.args)
            if cn and vals:
                self.col_values.setdefault(cn, set()).update(vals)
                # For df['col'].isin(...): the subscript form is not harvested by
                # _harvest_cols or visit_Attribute, so the column would be absent from
                # src_cols and _enrich would never fire. Seed it here when the
                # receiver's base var is a known DataFrame source.
                _isin_sid = self.var_src.get(self._base_var(node.func.value))
                if _isin_sid and _isin_sid in self.src_cols:
                    self.src_cols[_isin_sid].add(cn)

        # string-form filter predicate: `.filter("col == 'X'")` / `.where("col='X'")`
        # / `.filter("col IN ('a','b')")`. The predicate is a SQL-ish STRING, so the
        # AST col()/== miners never see it -- parse the literals out and seed them as
        # the column's `values` domain so the mock keeps rows (V2).
        if fn in ("filter", "where") and node.args:
            _pred = self._literal_str(node.args[0])
            if _pred:
                _seeded = self._seed_string_predicate_values(_pred)
                # a filtered column is a real input column; attribute it to the
                # source so _enrich attaches the seeded `values`.
                if _seeded and sid is not None and sid in self.src_cols:
                    self.src_cols[sid].update(_seeded)
                # collect all referenced columns from the predicate
                sql_cols = self._parse_sql_fragment_columns(_pred)
                self.sql_fragment_cols.update(sql_cols)

        # agg numeric type for the aliased output
        if fn in _AGG_FUNCS_NUMERIC or fn in _AGG_FUNCS_LONG:
            # the alias of this agg, if any (parent handles alias separately)
            pass

        # temp view binding: df.createOrReplaceTempView("name")
        if fn in _TEMP_VIEW_METHODS and node.args \
                and isinstance(node.args[0], ast.Constant) and sid:
            self.tempviews[str(node.args[0].value)] = sid

        # sink detection: <var>.write...saveAsTable("t") / .save(path) / .parquet(path).
        # Format-style terminals (parquet/csv/…) also appear on spark.read.* — gate them
        # on _is_write_chain; save/saveAsTable/insertInto only exist on the writer.
        if fn in _TABLE_WRITE_METHODS | _FILE_WRITE_METHODS or (
                fn in _WRITE_TERMINAL and self._is_write_chain(node)):
            written = self._write_base_var(node)
            tname = None
            if node.args and isinstance(node.args[0], ast.Constant):
                tname = self._path_basename(str(node.args[0].value)) \
                    if "/" in str(node.args[0].value) else str(node.args[0].value)
            elif node.args:
                _arg0 = node.args[0]
                tname, _ = self._name_from_arg(_arg0)
                # Fix 3 (condition a): for TABLE write methods, a string-variable arg
                # must contain a valid dotted table ref (not a temp label / path).
                if tname and fn in _TABLE_WRITE_METHODS and isinstance(_arg0, ast.Name):
                    _raw = self.str_consts.get(_arg0.id)
                    if _raw is not None and not _VALID_TABLE_REF_RE.fullmatch(_raw):
                        tname = None
            kind = "table" if fn in _TABLE_WRITE_METHODS else "file"
            name_unresolved = False
            if not tname:
                # genuine write but the target path is an unresolvable variable/param
                # (e.g. `events_df.write...parquet(out_path)` where out_path is a fn
                # arg). Emit the sink ANYWAY with a placeholder name from the written
                # df var + an llm_todo -- a dropped sink is worse than a flagged one
                # (the LLM never learns the sink exists, and Phase B has no baseline).
                base = re.sub(r"_?(df|sdf|out|result|final|output)$", "", written or "", flags=re.I)
                tname = base or written or ("sink%d" % self._n)
                self._n += 1
                name_unresolved = True
            if tname:
                # columns of the DataFrame being written: propagate the chain that
                # precedes `.write` (handles inline select()/groupBy().agg()/
                # applyInPandas(schema=...) that never hit a named var). Fall back
                # to the base var's tracked columns.
                pre = self._pre_write_expr(node)
                cols = self._propagate_cols(pre) if pre is not None else set()
                if not cols and written and written in self.var_cols:
                    cols = set(self.var_cols[written])
                rec = {"cols": set(cols), "kind": kind, "in_def": self._def_depth > 0,
                       "lineno": getattr(node, "lineno", None)}
                if name_unresolved:
                    rec["name_unresolved"] = True
                self.sinks[tname] = rec

        # helper-wrapped write: spark_write_parquet(df, path) — first DataFrame arg
        # is the written frame, a path-like arg names the sink.
        if fn in self.write_helpers:
            written = None
            tname = None
            for a in node.args + [k.value for k in node.keywords]:
                if isinstance(a, ast.Name) and a.id in self.var_cols and written is None:
                    written = a.id
                elif tname is None:
                    nm, _ = self._name_from_arg(a)
                    # Fix 3: for write-helper sink names from a string variable, require
                    # (a) the string is a valid dotted table ref AND (b) the variable was
                    # used directly in a saveAsTable/insertInto call somewhere in this tree.
                    if nm and isinstance(a, ast.Name) and a.id in self.str_consts:
                        _raw = self.str_consts[a.id]
                        if not _VALID_TABLE_REF_RE.fullmatch(_raw) \
                                or a.id not in self._tbl_write_vars:
                            nm = None
                    if nm:
                        tname = nm
            if tname and written:
                self.sinks[tname] = {"cols": set(self.var_cols.get(written, set())),
                                     "kind": "file", "in_def": self._def_depth > 0,
                                     "lineno": getattr(node, "lineno", None)}

        # col("c") attributed to the enclosing df var
        if fn in ("col", "column") and node.args and isinstance(node.args[0], ast.Constant):
            if sid:
                self.src_cols[sid].add(str(node.args[0].value))

        # column-helper call: `address_cleaning(src_df)` references columns by name
        # inside its body -- attribute them to the source passed in (columns hidden
        # behind a function-param indirection are otherwise invisible).
        if fn in self.column_helpers:
            df_idx, hcols = self.column_helpers[fn]
            if df_idx < len(node.args):
                argbase = self._base_var(node.args[df_idx])
                hsid = self.var_src.get(argbase)
                if hsid is not None:
                    self.src_cols.setdefault(hsid, set()).update(hcols)
                    self.strong_cols.setdefault(hsid, set()).update(hcols)

        # input-method string args
        if fn in _INPUT_METHODS and sid is not None:
            # open-ended consumption: select("*") / selectExpr("...*...") means the
            # workload reads ALL columns -> we cannot enumerate the full set.
            if fn in ("select", "selectExpr"):
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) and "*" in a.value:
                        self.open_ended_sids.add(sid)
            # A bare string column in a projection AFTER a join is ambiguous between
            # the joined legs, so we do NOT attribute it to any source (that is the
            # root cause of mock-schema column over-inclusion: a column that arrives
            # via the join gets wrongly stamped onto the base leg's source table).
            # We only attribute bare-string projections referenced on a PRE-JOIN
            # (single-source) var. For a `join` call itself we also skip the positional
            # args entirely: arg[0] is the RIGHT frame (whose nested projection columns
            # would otherwise leak onto the LEFT source), and the real join KEYS are
            # attributed to both legs by the dedicated join block below.
            if base not in self.joined_vars and fn != "join":
                for a in node.args:
                    for s in self._strings(a):
                        self.src_cols[sid].add(s)
                    # parse SQL fragments (not just bare identifiers) for filter/where/selectExpr/expr
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) and fn in ("filter", "where", "selectExpr", "expr"):
                        sql_cols = self._parse_sql_fragment_columns(a.value)
                        # Exclude names already in outputs (alias/computed outputs)
                        sql_cols -= self.outputs
                        self.src_cols[sid].update(sql_cols)
                        self.sql_fragment_cols.update(sql_cols)
            for kw in node.keywords:
                if kw.arg in ("on", "cols", "subset", "by"):
                    for s in self._strings(kw.value):
                        self.src_cols[sid].add(s)
            # a join key belongs to BOTH joined frames. Attribute the `on`
            # column(s) to the OTHER side's source too, so a key that is only
            # written as `right.join(left, on=[k])` is not dropped from `left`.
            # Join keys are STRONG evidence (survive output-name subtraction).
            if fn == "join" and node.args:
                on_cols = set()
                on_exprs = []
                if len(node.args) >= 2:
                    on_cols |= self._strings(node.args[1])
                    on_exprs.append(node.args[1])
                for kw in node.keywords:
                    if kw.arg == "on":
                        on_cols |= self._strings(kw.value)
                        on_exprs.append(kw.value)
                # Attribute join KEYS to a leg's source only when that leg is a
                # direct (non-joined) frame. If the leg is itself a join result,
                # var_src points at the BASE source, but the key may have arrived
                # via the upstream join (e.g. a lookup column used as a key in a
                # later join) and is NOT native to that source — attributing it
                # re-introduces the post-join over-inclusion (a phantom duplicate).
                if base not in self.joined_vars:
                    self.src_cols[sid] |= on_cols
                    self.strong_cols.setdefault(sid, set()).update(on_cols)
                other_base = self._base_var(node.args[0])
                other_sid = self.var_src.get(other_base)
                if other_sid is not None and other_sid in self.src_cols \
                        and other_base not in self.joined_vars:
                    self.src_cols[other_sid] |= on_cols
                    self.strong_cols.setdefault(other_sid, set()).update(on_cols)
                # Record join EDGES so datagen can pool the joined columns (values
                # overlap across mocks). Same-named `on=` keys link this frame and
                # the joined frame on each key; equality conditions (`a.k1 == b.k2`)
                # link the two columns even when their NAMES differ.
                if sid is not None and other_sid is not None:
                    for k in on_cols:
                        self.join_edges.append(((sid, k), (other_sid, k)))
                for ex in on_exprs:
                    for cmp in [n for n in ast.walk(ex) if isinstance(n, ast.Compare)]:
                        if len(cmp.ops) == 1 and isinstance(cmp.ops[0], ast.Eq):
                            lo, ro = cmp.left, cmp.comparators[0]
                            lcol, rcol = self._col_name_of(lo), self._col_name_of(ro)
                            lsid = self.var_src.get(self._base_var(lo))
                            rsid = self.var_src.get(self._base_var(ro))
                            if lcol and rcol and lsid is not None and rsid is not None \
                                    and (lsid, lcol) != (rsid, rcol):
                                self.join_edges.append(((lsid, lcol), (rsid, rcol)))
                # range-join predicates (`A.lo <= B.x <= A.hi`, `.between`)
                # are recorded separately so datagen generates the linked columns
                # JOINTLY (>=1 row per group satisfies the inequality) -- independent
                # random dates almost never do (V1).
                for ex in on_exprs:
                    self._collect_range_edges(ex)

        # display() / <expr>.display() detection
        if fn == "display" and isinstance(node.func, ast.Name) and node.args:
            arg_src = ast.unparse(node.args[0])
            bv = self._base_var(node.args[0])
            self.display_sites.append({
                "line": getattr(node, "lineno", None),
                "arg_src": arg_src,
                "base_var": bv if bv and bv in self.var_cols else None,
            })
        elif fn == "display" and isinstance(node.func, ast.Attribute):
            arg_src = ast.unparse(node.func.value)
            bv = self._base_var(node.func.value)
            self.display_sites.append({
                "line": getattr(node, "lineno", None),
                "arg_src": arg_src,
                "base_var": bv if bv and bv in self.var_cols else None,
            })

        self.generic_visit(node)

    def visit_Attribute(self, node):
        # `<df>.columns` on a source df -> the workload enumerates ALL columns
        # dynamically; the referenced set is NOT the complete set.
        if node.attr == "columns" and isinstance(node.value, ast.Name):
            sid = self.var_src.get(node.value.id)
            if sid:
                self.open_ended_sids.add(sid)
        # qualified column reference `<srcvar>.<col>` (e.g. `res_relate_df.src`,
        # `step_one_df.res_ent_id`) -- STRONG evidence of source ownership.
        # Call-site: ``df.select(...)`` is an API call, not a column named select.
        # Property: ``df.write`` / ``df.schema`` are chain heads, not columns.
        if isinstance(node.value, ast.Name) and not node.attr.startswith("_") \
                and not self._attr_is_callee(node) and node.attr not in _DF_PROPERTY_ATTRS:
            sid = self.var_src.get(node.value.id)
            if sid is not None and re.fullmatch(r"[A-Za-z_]\w*", node.attr) \
                    and node.attr.lower() not in _TYPE_KEYWORDS:
                self.src_cols.setdefault(sid, set()).add(node.attr)
                self.strong_cols.setdefault(sid, set()).add(node.attr)
        self.generic_visit(node)

    def visit_Compare(self, node):
        # `col('X') == 'V'` / `'V' == col('X')` constrains X to V. Seed V as part of
        # X's `values` domain so an equality filter keeps rows in the mock. Only `==`
        # (a positive constraint); `!=` excludes V, so seeding it would empty the mock.
        # V value may be a bare literal, a `lit('V')` wrapper, or a Name bound to a
        # known string constant (`x = 'AAA'; col('c') == x`) (V2).
        if len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            left, right = node.left, node.comparators[0]
            for col_side, val_side in ((left, right), (right, left)):
                v = self._literal_of(val_side)
                if v is not None:
                    cn = self._col_name_of(col_side)
                    if cn:
                        self.col_values.setdefault(cn, set()).add(v)
                        break
        self.generic_visit(node)



# ---------------------------------------------------------------------------
# Layer B: embedded spark.sql() lineage
# ---------------------------------------------------------------------------

def _extract_sql_strings(tree) -> list[str]:
    """Return SQL bodies passed to *.sql(...) (handles f-strings/.format())."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "sql" and node.args:
            a0 = node.args[0]
            text = _const_str(a0)
            if text and re.search(r"\bselect\b|\bfrom\b", text, re.I):
                out.append(text)
    return out


def _const_str(node) -> str | None:
    """Reconstruct a (possibly f-string / .format) SQL string with placeholders."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            else:
                parts.append("_ph_")  # interpolation placeholder identifier
        return "".join(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        inner = _const_str(node.func.value)
        if inner is not None:
            return re.sub(r"\{[^}]*\}", "_ph_", inner)
    return None


def _sql_lineage(sql_bodies: list[str]) -> dict[str, dict]:
    """Per-table column sets mined from SQL via sqlglot. table ->
    {columns:set, values:{col:[literals]}}. ``values`` carries the literal domain
    from ``WHERE col IN (...)`` / ``col = 'lit'`` predicates so a filtered mock
    keeps rows (mirrors the DataFrame isin/== extraction)."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return {}
    tables: dict[str, set] = {}
    col_values: dict[str, set] = {}        # col name -> literal domain (by name)
    for body in sql_bodies:
        try:
            tree = sqlglot.parse_one(body, dialect="spark")
        except Exception:
            continue
        if tree is None:
            continue
        # alias -> table name
        alias2tbl = {}
        for t in tree.find_all(exp.Table):
            alias2tbl[t.alias_or_name] = t.name
            tables.setdefault(t.name, set())
        for c in tree.find_all(exp.Column):
            tname = alias2tbl.get(c.table) if c.table else None
            if tname:
                tables.setdefault(tname, set()).add(c.name)
            elif len(tables) == 1:
                # single-table query: attribute unqualified cols to it
                only = next(iter(tables))
                tables[only].add(c.name)

        # `col IN ('a','b')` -> col's domain includes a,b
        for in_expr in tree.find_all(exp.In):
            col = in_expr.this
            if isinstance(col, exp.Column) and in_expr.expressions:
                vals = [e.this for e in in_expr.expressions if isinstance(e, exp.Literal)]
                if vals:
                    col_values.setdefault(col.name, set()).update(vals)
        # `col = 'lit'` (positive equality only)
        for eq in tree.find_all(exp.EQ):
            for a, b in ((eq.this, eq.expression), (eq.expression, eq.this)):
                if isinstance(a, exp.Column) and isinstance(b, exp.Literal):
                    col_values.setdefault(a.name, set()).add(b.this)
                    break
    out = {t: {"columns": sorted(cs)} for t, cs in tables.items() if cs}
    if col_values:
        out["__col_values__"] = {c: sorted(v) for c, v in col_values.items()}
    return out


def _normalize_sql_placeholders(sql: str) -> str:
    """Make a template .sql file parseable by sqlglot. Workloads template table
    names with shell/format vars (``${DATABASE_NAME}.${SCHEMA_STAGING}.TBL`` or
    ``{db}.{schema}.tbl``); drop the qualifier prefixes so the table resolves to
    its bare last segment (``TBL``), and turn any lone remaining placeholder into
    a harmless identifier."""
    s = re.sub(r"\$?\{[^}]+\}\.", "", sql)   # drop `${VAR}.` / `{var}.` qualifiers
    s = re.sub(r"\$?\{[^}]+\}", "_ph_", s)   # any lone placeholder -> identifier
    return s


_SQL_FILE_LINK_TODO = (
    "Link to every entrypoint that executes this file (search for open(...), "
    "*_SQL_FILE_PATH, or string literals containing .sql). Merge each table's "
    "columns into those entrypoints' sources (read) or sinks (write); confirm "
    "sources/sinks and column types; delete this todo when done. Keep this "
    "sql_files row — only delete the entire row if no entrypoint uses the file "
    "(dead/orphan SQL)."
)

def _dedupe_snowflake_column_names(columns: set[str]) -> list[str]:
    """Collapse mixed-case duplicates (ITEM + item) to one canonical spelling."""
    from datagen import _snowflake_ddl_key
    _ident = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
    seen: set[str] = set()
    out: list[str] = []
    for col in sorted(columns):
        key = _snowflake_ddl_key(col)
        if key in seen:
            continue
        seen.add(key)
        out.append(key if _ident.fullmatch(col) else col)
    return out


def _catalog_sql_files(root: str) -> list[dict]:
    """Walk all ``*.sql`` under *root* and mine table/column lineage per file.

    Returns a path-keyed catalog (not entrypoint-keyed). The data-synthesizer LLM links
    each file to the entrypoint(s) that execute it and merges table data into
    per-entrypoint sources/sinks."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return []
    _ident = re.compile(r"[A-Za-z_]\w*")
    catalog: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".sql"):
                continue
            abs_p = os.path.join(dirpath, fn)
            try:
                rel = os.path.relpath(abs_p, root)
            except ValueError:
                rel = abs_p
            try:
                with open(abs_p, encoding="utf-8") as fh:
                    sql = _normalize_sql_placeholders(fh.read())
            except Exception:
                continue
            try:
                stmts = sqlglot.parse(sql, dialect="spark")   # multi-statement
            except Exception:
                continue
            tables: dict[str, dict] = {}
            for st in stmts:
                if st is None:
                    continue
                cte_names = {c.alias.lower() for c in st.find_all(exp.CTE) if c.alias}
                write_targets: set[str] = set()
                for node in st.find_all(exp.Insert):
                    if isinstance(node.this, exp.Table) and node.this.name.lower() not in cte_names:
                        write_targets.add(node.this.name)
                for node in st.find_all(exp.Delete):
                    if isinstance(node.this, exp.Table) and node.this.name.lower() not in cte_names:
                        write_targets.add(node.this.name)
                for node in st.find_all(exp.Merge):
                    if isinstance(node.this, exp.Table) and node.this.name.lower() not in cte_names:
                        write_targets.add(node.this.name)
                alias2tbl, phys = {}, []
                for t in st.find_all(exp.Table):
                    if t.name.lower() in cte_names or t.name == "_ph_":
                        continue
                    alias2tbl[t.alias_or_name] = t.name
                    entry = tables.setdefault(t.name, {"columns": set(), "roles": set()})
                    phys.append(t.name)
                    if t.name in write_targets:
                        entry["roles"].add("write")
                    else:
                        entry["roles"].add("read")
                uniq = set(phys)
                for c in st.find_all(exp.Column):
                    col = c.name
                    if not col or not _ident.fullmatch(col) or col.lower() in _TYPE_KEYWORDS:
                        continue
                    tname = alias2tbl.get(c.table) if c.table else None
                    if tname:
                        tables.setdefault(tname, {"columns": set(), "roles": set()})
                        tables[tname]["columns"].add(col)
                    elif len(uniq) == 1:
                        only = next(iter(uniq))
                        tables.setdefault(only, {"columns": set(), "roles": set()})
                        tables[only]["columns"].add(col)
            tables_out: dict[str, dict] = {}
            for t, info in tables.items():
                k = t.lower()
                tables_out[k] = {
                    "name": t,
                    "columns": _dedupe_snowflake_column_names(info.get("columns", set())),
                    "roles": sorted(info.get("roles", set())),
                }
            catalog.append({
                "path": rel,
                "tables": tables_out,
                "llm_todo": _SQL_FILE_LINK_TODO,
            })
    catalog.sort(key=lambda e: e["path"])
    return catalog


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _seed_entrypoint_kwargs(entry_abs: str, import_roots: list[str]) -> dict:
    """Seed entrypoint_kwargs with names imported from local modules that don't
    resolve to a file in the workload (i.e. modules the harness must synthesize).

    Example: ``from definition_variables import date_partition, annee_etude`` where
    ``definition_variables.py`` is absent -> ``{"date_partition": null, "annee_etude": null}``.
    The data-synthesizer then fills in appropriate default values. Standard library and
    third-party modules (``os``, ``sys``, ``pyspark``, ``pandas``, ...) are ignored.
    Resolvable local modules (their .py exists in an import root) are also ignored
    — those names refer to real functions/classes, not runtime parameters.
    """
    try:
        tree = ast.parse(_read_source(entry_abs))
    except Exception:
        return {}
    kwargs: dict[str, None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module or node.level:
            continue
        mod = node.module
        # Skip anything that looks like a stdlib/third-party root
        top = mod.split(".", 1)[0]
        if top in _KNOWN_NON_LOCAL_MODULES:
            continue
        # If the module resolves to a file inside an import root, its symbols are
        # real code, not runtime parameters.
        rel = mod.replace(".", os.sep) + ".py"
        resolved = any(os.path.isfile(os.path.join(r, rel)) for r in import_roots)
        if resolved:
            continue
        for a in node.names:
            name = a.asname or a.name
            if name and name != "*":
                kwargs.setdefault(name, None)
    return kwargs


# Modules that should NEVER be treated as workload-local (stdlib + common third-party
# roots seen in Spark migrations). If it's here, `from X import Y` doesn't produce
# runtime kwargs even if X.py is not present.
_KNOWN_NON_LOCAL_MODULES = frozenset({
    # stdlib
    "os", "sys", "re", "json", "math", "time", "datetime", "collections", "itertools",
    "functools", "pathlib", "typing", "logging", "argparse", "subprocess", "shutil",
    "tempfile", "io", "csv", "hashlib", "uuid", "copy", "warnings", "contextlib",
    "operator", "string", "urllib", "http", "socket", "threading", "multiprocessing",
    "concurrent", "asyncio", "abc", "enum", "dataclasses", "types", "inspect",
    "traceback", "pickle", "base64", "struct", "zlib", "gzip", "bz2", "zipfile",
    # numeric / scientific
    "numpy", "pandas", "scipy", "sklearn", "statsmodels",
    # spark / snowflake / databricks
    "pyspark", "databricks", "snowflake", "snowpark_connect", "delta", "sqlalchemy",
    # cloud
    "boto3", "s3fs", "gcsfs", "azure",
    # misc common
    "requests", "yaml", "toml", "click", "pytest", "faker", "dateutil", "pytz",
    "tzlocal", "cryptography", "jwt", "loguru", "rich", "attrs", "pydantic",
})


def _resolve_imports(path: str, import_roots: list[str] | None) -> list[str]:
    """Find local module files imported by the entrypoint (for StructType reuse)."""
    files = [path]
    # Own directory first (sys.path[0]) so bare sibling imports resolve.
    roots = [os.path.dirname(path), *(import_roots or [])]
    try:
        tree = ast.parse(_read_source(path))
    except Exception:
        return files
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
    for m in mods:
        rel = m.replace(".", os.sep) + ".py"
        for r in roots:
            cand = os.path.join(r, rel)
            if os.path.isfile(cand) and cand not in files:
                files.append(cand)
    return files


# ---------------------------------------------------------------------------
# Third-party import scan → pip package names (used to pre-seed the phase venv
# so entrypoints don't fail per-trial with "No module named X" for implicit /
# transitive imports that were never declared in requirements.txt).
# ---------------------------------------------------------------------------

# Import-name -> pip distribution name, ONLY for the cases where they differ.
# Keep this table SMALL and well-commented: well-known name mismatches plus the
# implicit deps repeatedly seen missing in nightly eval runs. Any module NOT
# listed here defaults to its own import name (see ``pip_name_for_import``).
_IMPORT_TO_PIP_NAME: dict[str, str] = {
    # classic import-name != distribution-name mismatches
    "yaml": "PyYAML",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "OpenSSL": "pyOpenSSL",
    "attr": "attrs",
    # deps observed missing in the PySpark→SCOS nightly eval
    "mysql": "mysql-connector-python",
    "airflow": "apache-airflow",
    "snowflake": "snowflake-connector-python",
    # NOTE: findspark, pytz, requests, pyodbc, pykalman, openpyxl, graphframes
    # all use import-name == pip-name, so they need no entry here.
}


def pip_name_for_import(mod: str) -> str:
    """Map a top-level import name to the pip distribution name that provides it.

    Falls back to the import name itself when no alias is known (the common
    case). Pure/deterministic — no network, no imports."""
    return _IMPORT_TO_PIP_NAME.get(mod, mod)


def _module_is_local(top: str, roots: list[str]) -> bool:
    """True if a top-level module name resolves to a workload-local file/package
    (``<top>.py`` or ``<top>/__init__.py``) inside any of ``roots``."""
    for r in roots:
        if os.path.isfile(os.path.join(r, top + ".py")):
            return True
        if os.path.isfile(os.path.join(r, top, "__init__.py")):
            return True
    return False


def scan_third_party_imports(files: list[str], import_roots: list[str] | None = None) -> set[str]:
    """Statically scan ``files`` and return the set of THIRD-PARTY top-level
    module names they import.

    Excludes (a) the Python standard library and (b) any module that resolves to
    a workload-local ``.py`` file / package inside a file's own directory or
    ``import_roots``. Relative imports (``from . import x``) are always local and
    skipped. Purely static: files are parsed with ``ast`` and never imported, so
    this is safe to call before the venv is populated."""
    roots = list(import_roots or [])
    thirdparty: set[str] = set()
    for path in files:
        try:
            tree = ast.parse(_read_source(path))
        except Exception:
            continue
        file_roots = [os.path.dirname(path), *roots]
        tops: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name:
                        tops.add(a.name.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom):
                # relative import (level > 0) is always workload-local
                if node.level:
                    continue
                if node.module:
                    tops.add(node.module.split(".", 1)[0])
        for top in tops:
            if not top or top.startswith("_"):
                continue
            if top in sys.stdlib_module_names:
                continue
            if _module_is_local(top, file_roots):
                continue
            thirdparty.add(top)
    return thirdparty


def pip_requirements_for_imports(files: list[str], import_roots: list[str] | None = None) -> set[str]:
    """Third-party top-level imports across ``files`` mapped to pip distribution
    names. Convenience wrapper: ``scan_third_party_imports`` + ``pip_name_for_import``."""
    return {pip_name_for_import(m) for m in scan_third_party_imports(files, import_roots)}


def _find_read_helpers(files: list[str]) -> tuple[set, dict, dict]:
    """Functions (in the entrypoint or imported modules) whose body calls
    spark.read.* and returns it — i.e. thin wrappers around a read.

    Returns (helper_names, helper_schema_var, helper_reader) where:
      - helper_schema_var maps a helper -> the StructType var it applies via
        ``.schema(VAR)`` inside its body (so a call site inherits the schema), and
      - helper_reader maps a helper -> the ACTUAL reader method it calls on
        spark.read (``table`` / ``parquet`` / ``csv`` / ...), so the call site
        reports the real method rather than guessing from the helper's name (e.g.
        a ``spark_read_parquet`` patch that actually does ``spark.read.table``)."""
    helpers: set = set()
    helper_schema: dict = {}
    helper_reader: dict = {}
    for f in files:
        try:
            tree = ast.parse(_read_source(f))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_helper = False
            schema_var = None
            reader_method = None
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr in _READ_METHODS:
                    chain = []
                    c = inner.value
                    while True:
                        if isinstance(c, ast.Attribute):
                            chain.append(c.attr); c = c.value
                        elif isinstance(c, ast.Call):
                            c = c.func
                        else:
                            break
                    if isinstance(c, ast.Name):
                        chain.append(c.id)
                    if "read" in chain:
                        is_helper = True
                        reader_method = inner.attr      # the real reader (table/parquet/...)
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                        and inner.func.attr == "schema" and inner.args \
                        and isinstance(inner.args[0], ast.Name):
                    schema_var = inner.args[0].id
            if is_helper:
                helpers.add(node.name)
                if schema_var:
                    helper_schema[node.name] = schema_var
                if reader_method:
                    helper_reader[node.name] = reader_method
    return helpers, helper_schema, helper_reader


def _find_write_helpers(files: list[str]) -> set:
    """Function names whose body writes a DataFrame (``<param>.write...save`` /
    ``.saveAsTable`` / writer ``.parquet``/``.csv``/...). A call to one of these
    from an entrypoint is treated as a sink, mirroring read-helper handling.

    Transitive: a function that calls a direct write helper is also marked as a
    write helper (e.g. writeToSchema -> fullLoad -> .write.saveAsTable)."""
    direct: set = set()
    # fn_name -> set of function names called in its body
    callers: dict[str, set] = {}
    for f in files:
        try:
            tree = ast.parse(_read_source(f))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_write = saves = False
            called_fns: set = set()
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr == "write":
                    has_write = True
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                        and inner.func.attr in _WRITE_TERMINAL:
                    saves = True
                if isinstance(inner, ast.Call):
                    fn = inner.func
                    if isinstance(fn, ast.Name):
                        called_fns.add(fn.id)
                    elif isinstance(fn, ast.Attribute):
                        called_fns.add(fn.attr)
            if has_write and saves:
                direct.add(node.name)
            callers[node.name] = called_fns
    # transitive pass: functions that call a direct write helper
    helpers = set(direct)
    for fn_name, called in callers.items():
        if fn_name not in helpers and called & direct:
            helpers.add(fn_name)
    return helpers


# column-transform F functions whose identifier-like string args are INPUT columns
_COL_FUNCS = {
    "coalesce", "concat", "concat_ws", "substring", "substr", "length", "lower",
    "upper", "trim", "ltrim", "rtrim", "initcap", "reverse", "regexp_replace",
    "regexp_extract", "split", "greatest", "least", "nvl", "nvl2", "ifnull",
    "to_date", "to_timestamp", "date_format", "datediff", "year", "month",
    "dayofmonth", "round", "abs", "ceil", "floor", "sqrt", "sum", "avg", "mean",
    "max", "min", "collect_list", "collect_set",
}
# attribute first-arg names that are OUTPUTS, not inputs (subset of the DF API)
_OUTPUT_FIRST_ARG = frozenset(
    {"withColumn", "withColumnRenamed", "alias", "name"} & _DF_METHODS
) | {"name"}  # Column.name, not on DataFrame


def _find_column_helpers(files: list[str]) -> dict:
    """Helper functions that take a DataFrame param and reference specific columns
    by name (e.g. `address_cleaning(df)` -> street_number/street_name). Returns
    {fn_name: (df_param_index, frozenset(columns))} so a call site `helper(src_df)`
    can attribute those columns to the source -- columns referenced only behind a
    function-param indirection are otherwise invisible to mining."""
    out: dict = {}
    for f in files:
        try:
            tree = ast.parse(_read_source(f))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in node.args.args]
            if not params:
                continue
            # which param is the DataFrame? the one used with a DF op / subscript.
            df_param, df_idx = None, 0
            for inner in ast.walk(node):
                if isinstance(inner, ast.Subscript) and isinstance(inner.value, ast.Name) \
                        and inner.value.id in params:
                    df_param = inner.value.id; break
                if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) \
                        and inner.value.id in params and inner.attr in _DF_METHODS:
                    df_param = inner.value.id; break
            if df_param is None:
                continue
            df_idx = params.index(df_param)
            cols: set = set()
            outputs: set = set()
            for inner in ast.walk(node):
                # df['col'] subscript
                if isinstance(inner, ast.Subscript) and isinstance(inner.value, ast.Name) \
                        and inner.value.id == df_param:
                    k = inner.slice
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        cols.add(k.value)
                # df.col qualified attribute (not a method)
                if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) \
                        and inner.value.id == df_param and inner.attr not in _DF_METHODS \
                        and not inner.attr.startswith("_"):
                    cols.add(inner.attr)
                if isinstance(inner, ast.Call):
                    fn = inner.func.attr if isinstance(inner.func, ast.Attribute) else (
                        inner.func.id if isinstance(inner.func, ast.Name) else None)
                    # col("x") / column("x")
                    if fn in ("col", "column") and inner.args and isinstance(inner.args[0], ast.Constant) \
                            and isinstance(inner.args[0].value, str):
                        cols.add(inner.args[0].value)
                    # identifier-like string args to column-transform functions
                    if fn in _COL_FUNCS:
                        for a in inner.args:
                            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                                cols.add(a.value)
                    # withColumn/withColumnRenamed/alias OUTPUT name (1st arg) -> not input
                    if fn in _OUTPUT_FIRST_ARG and inner.args and isinstance(inner.args[0], ast.Constant) \
                            and isinstance(inner.args[0].value, str):
                        outputs.add(inner.args[0].value)
            cols = {c for c in cols
                    if re.fullmatch(r"[A-Za-z_]\w*", c) and c.lower() not in _TYPE_KEYWORDS}
            cols -= outputs
            if cols:
                out[node.name] = (df_idx, frozenset(cols))
    return out


def _classify_source(name: str, s: dict, dynamic_cols: bool, validated_clean: bool) -> dict:
    """Derive the overall verdict from the explicit, independent sub-signals the
    miner already computed — so the boundary between what we KNOW and what needs
    an LLM is exhaustive and auditable:

      column_completeness: exact   (StructType-bound — every column known)
                           closed  (usage-mined; no open-ended consumption, so the
                                    referenced set is everything the workload touches)
                           open    (select('*') / df.columns / dynamic — the column
                                    set CANNOT be enumerated statically)
      name_confidence:     certain (table literal / .replace token / schema var)
                           heuristic (guessed from a path's last segment)
                           unresolved (no name could be derived)

    Rules (conservative — anything we cannot prove is flagged):
      open columns OR unresolved name  -> llm_required
      exact columns + certain name     -> deterministic
      otherwise                        -> high/medium_confidence (verify name/types)
    """
    fmt = (s.get("format") or "").lower()
    method = (s.get("reader_method") or "").lower()
    prov = s.get("provenance", [])
    ncols = len(s.get("columns", []))
    col_complete = s.get("column_completeness", "closed")
    name_conf = s.get("name_confidence", "heuristic")

    # external connector reads: schema lives in a live DB we cannot see
    if (fmt in _CONNECTOR_FMTS or method in _CONNECTOR_FMTS or
            (method == "table" and "sql_lineage" not in prov
             and "intermediate_sink" not in prov and ncols == 0)):
        return {"completeness": "llm_required",
                "llm_reason": "connector/catalog read (%s); column schema lives in an "
                              "external system not visible in code." % (fmt or method)}
    if col_complete == "open" or ncols == 0:
        return {"completeness": "llm_required",
                "llm_reason": "column set is OPEN (select('*') / df.columns / fully "
                              "dynamic read) — the complete column list cannot be "
                              "enumerated statically; LLM or real schema required."}
    if name_conf == "unresolved":
        return {"completeness": "llm_required",
                "llm_reason": "read target name could not be derived (dynamic path); "
                              "LLM should name this source."}
    if col_complete == "exact":
        if name_conf == "certain":
            return {"completeness": "deterministic", "llm_reason": None}
        return {"completeness": "high_confidence",
                "llm_reason": "columns are EXACT (StructType) but the source name was "
                              "guessed from a path — LLM should confirm the table name."}
    # closed column set (complete for what the workload touches), name may be a guess
    reasons = []
    if name_conf == "heuristic":
        reasons.append("source name guessed from a path segment — confirm it")
    if not validated_clean:
        reasons.append("types default to string where no cast/StructType — confirm "
                       "(and if read via a connector/JDBC SELECT-with-aliases query, add "
                       "the underlying WHERE/JOIN source columns, not just the aliases)")
    return {"completeness": "high_confidence" if validated_clean and name_conf == "certain"
            else "medium_confidence",
            "llm_reason": ("; ".join(reasons) + "."
                           if reasons else None) or
                          ("columns the workload touches all resolve; types may need "
                           "confirmation." if validated_clean else None)}


# ---------------------------------------------------------------------------
# Entrypoint detection (deterministic): markers + import graph + closure
# ---------------------------------------------------------------------------

def _package_roots(root: str) -> list[str]:
    """sys.path roots so the project's own absolute imports resolve: the root
    plus the parent of every top-level package (dir with __init__.py whose
    parent has none)."""
    roots = {os.path.abspath(root)}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        if "__init__.py" in fns:
            parent = os.path.dirname(dp)
            if not os.path.isfile(os.path.join(parent, "__init__.py")):
                roots.add(os.path.abspath(parent))
    return sorted(roots)


def _module_to_file(mod: str, roots: list[str]) -> str | None:
    rel = mod.replace(".", os.sep)
    for r in roots:
        for cand in (os.path.join(r, rel + ".py"), os.path.join(r, rel, "__init__.py")):
            if os.path.isfile(cand):
                return os.path.abspath(cand)
    return None


def _file_facts(path: str, roots: list[str], by_stem: dict) -> dict:
    """markers + resolved import/%run edges + whether the file uses Spark."""
    try:
        src = _read_source(path)
        tree = ast.parse(src)
    except Exception:
        return {"markers": [], "edges": set(), "uses_spark": False}

    markers = []
    if path.endswith(".ipynb"):
        markers.append("ipynb")          # native Jupyter notebook (runs top-to-bottom)
    if "# Databricks notebook source" in src or "# COMMAND ----------" in src:
        markers.append("databricks_notebook")

    edges: set[str] = set()
    # A file's own directory is on sys.path[0] at runtime, so a bare
    # ``import sibling`` resolves to ``<this dir>/sibling.py`` before any package
    # root. Resolve own-dir first (mirrors Python) — without it, sibling helpers
    # in a subfolder (EMR/Airflow/Databricks task dirs) never enter the closure.
    local_roots = [os.path.dirname(path), *roots]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                f = _module_to_file(a.name, local_roots)
                if f:
                    edges.add(f)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            base = _module_to_file(node.module, local_roots)
            if base:
                edges.add(base)
            for a in node.names:                       # from pkg import submodule
                f = _module_to_file(node.module + "." + a.name, local_roots)
                if f:
                    edges.add(f)

    # Databricks %run targets
    for pat in (r"#\s*MAGIC\s+%run\s+(\S+)", r"""dbutils\.notebook\.run\(\s*["']([^"']+)["']"""):
        for m in re.finditer(pat, src):
            stem = os.path.basename(m.group(1)).strip().split(".")[0]
            if stem in by_stem:
                edges.add(by_stem[stem])

    has_main = has_session = has_mod_io = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            has_main = True
        if isinstance(node, ast.Call):
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else None)
            if fn in _SESSION_MARKERS or fn == "SparkSession":
                has_session = True
    for node in tree.body:                              # module-level spark IO only
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and inner.attr in (_READ_METHODS | _WRITE_ATTRS | {"sql"}):
                has_mod_io = True
                break
    if has_main:
        markers.append("main_guard")
    if has_session:
        markers.append("creates_sparksession")
    if has_mod_io:
        markers.append("module_spark_io")

    uses_spark = bool(re.search(r"\b(pyspark|snowpark_connect|SparkSession)\b", src)) \
        or "module_spark_io" in markers or "creates_sparksession" in markers \
        or "databricks_notebook" in markers
    return {"markers": markers, "edges": edges, "uses_spark": uses_spark}


def _closure(start: str, facts: dict) -> set[str]:
    """BFS the import graph from `start` (inclusive) -> all reachable files."""
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for tgt in facts.get(cur, {}).get("edges", ()):
            if tgt not in seen:
                stack.append(tgt)
    return seen


def _slug(path: str, root: str) -> str:
    s = re.sub(r"\.(py|ipynb)$", "", os.path.relpath(path, root))
    return re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()


# ---------------------------------------------------------------------------
# Symbol-level (function) reachability: does the entrypoint EVER touch Spark?
# Traces the exact names the entrypoint imports/calls, resolves each to its
# defining function/class across modules, and BFS-walks transitively. Precise
# where calls resolve statically (virtually all ETL code); flags `unresolved`
# for dynamic dispatch so the caller can fall back conservatively.
# ---------------------------------------------------------------------------

_SPARK_IMPORT_MODS = ("pyspark", "snowpark_connect")


def _file_index(abs_path: str, roots: list[str], cache: dict) -> dict:
    if abs_path in cache:
        return cache[abs_path]
    idx = {"ok": True, "defs": {}, "bind": {}, "spark_names": {"spark"},
           "module_body": []}
    cache[abs_path] = idx
    try:
        tree = ast.parse(_read_source(abs_path))
    except Exception:
        idx["ok"] = False
        return idx
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            idx["defs"][node.name] = node
        else:
            idx["module_body"].append(node)
    # Own directory first (sys.path[0]) so bare sibling imports resolve — matches
    # the edge resolver in _file_facts.
    roots = [os.path.dirname(abs_path), *roots]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                local = _module_to_file(a.name, roots)
                if top in _SPARK_IMPORT_MODS:
                    idx["spark_names"].add(a.asname or top)
                elif local:
                    idx["bind"][a.asname or a.name.split(".")[0]] = ("module", local)
        elif isinstance(node, ast.ImportFrom) and node.module:
            spark_mod = node.module.split(".")[0] in _SPARK_IMPORT_MODS
            local = _module_to_file(node.module, roots)
            for a in node.names:
                nm = a.asname or a.name
                if spark_mod:
                    idx["spark_names"].add(nm)
                elif local:
                    subf = _module_to_file(node.module + "." + a.name, roots)
                    idx["bind"][nm] = ("module", subf) if subf else ("symbol", local, a.name)
                # external non-Spark imports (os, boto3, pandas, pyodbc, ...) ignored
    return idx


def _touches_spark_local(node, spark_names: set) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in spark_names:
            return True
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in ("getOrCreate", "init_spark_session"):
                return True
            if isinstance(f, ast.Name) and f.id == "SparkSession":
                return True
    return False


def _spark_reachable(entry_abs: str, roots: list[str]) -> tuple[bool, bool]:
    """Return (touches_spark, unresolved). Walks the call graph from the
    entrypoint's executed code through every statically-resolvable function/class
    it reaches across modules."""
    cache: dict = {}
    visited: set = set()
    stack = [(entry_abs, "")]            # "" = module-level body
    unresolved = False
    while stack:
        file, sym = stack.pop()
        if (file, sym) in visited:
            continue
        visited.add((file, sym))
        idx = _file_index(file, roots, cache)
        if not idx["ok"]:
            unresolved = True
            continue
        nodes = idx["module_body"] if sym == "" else (
            [idx["defs"][sym]] if sym in idx["defs"] else None)
        if nodes is None:
            unresolved = True
            continue
        for node in nodes:
            if _touches_spark_local(node, idx["spark_names"]):
                return True, unresolved
            for cnode in ast.walk(node):
                if not isinstance(cnode, ast.Call):
                    continue
                callee = cnode.func
                if isinstance(callee, ast.Name):
                    b = idx["bind"].get(callee.id)
                    if b and b[0] == "symbol":
                        stack.append((b[1], b[2]))
                    elif b and b[0] == "module":
                        stack.append((b[1], callee.id))
                    elif callee.id in idx["defs"]:
                        stack.append((file, callee.id))
                    elif callee.id in idx["spark_names"]:
                        return True, unresolved
                elif isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
                    b = idx["bind"].get(callee.value.id)
                    if b and b[0] == "module":
                        stack.append((b[1], callee.attr))
                    elif callee.value.id in idx["spark_names"]:
                        return True, unresolved
    return False, unresolved


def detect_entrypoints(root: str) -> tuple[list[dict], dict]:
    """Deterministic entrypoints: a file with an entrypoint marker that nothing
    else imports/%run's AND whose import closure touches Spark. Returns
    (entrypoints, facts) where each entrypoint has id/path/reasons/closure."""
    root = os.path.abspath(root)
    roots = _package_roots(root)
    files = _iter_py_files(root)
    by_stem = {}
    for f in files:
        by_stem.setdefault(os.path.splitext(os.path.basename(f))[0], f)
    facts = {f: _file_facts(f, roots, by_stem) for f in files}
    referenced = {t for f in files for t in facts[f]["edges"] if t != f}

    eps = []
    for f in files:
        if not (facts[f]["markers"] and f not in referenced):
            continue
        # Spark gate via SYMBOL-LEVEL reachability: does the entrypoint's executed
        # code path ever reach Spark, tracing the exact functions it imports/calls
        # across modules (not just file membership)? This correctly excludes a
        # non-Spark job that merely shares a common/ package containing Spark
        # helpers it never calls (e.g. ingest/channel_catalog.py).
        closure = _closure(f, facts)
        reach_spark, unresolved = _spark_reachable(f, roots)
        markers = set(facts[f]["markers"])
        if reach_spark:
            pass
        elif "module_spark_io" in markers or markers >= {"creates_sparksession", "main_guard"}:
            pass                                         # the file EXECUTES Spark in its
            # own body: module-level read/write/sql, or a __main__ script that builds a
            # SparkSession. (A `common/` helper that merely DEFINES a session-builder has
            # `creates_sparksession` but no main_guard / module IO -> not an entrypoint,
            # so it is not picked up here.) Handles class-method / importlib dispatch that
            # symbol-tracing can't follow.
        elif unresolved and any(facts.get(c, {}).get("uses_spark") for c in closure):
            pass                                         # conservative: dynamic dispatch + Spark in closure
        else:
            continue                                     # confidently non-Spark -> skip
        eps.append({"id": _slug(f, root), "path": os.path.relpath(f, root),
                    "abs": f, "reasons": facts[f]["markers"],
                    "closure": sorted(os.path.relpath(c, root) for c in closure),
                    "import_roots": sorted({os.path.relpath(r, root) or "." for r in roots})})
    return eps, facts


# ---------------------------------------------------------------------------
# Unified analysis: workload -> entrypoints -> sources/sinks + schemas
# ---------------------------------------------------------------------------

def _bare(name: str) -> str:
    """Canonical key for matching source/sink names (last dot segment, lowercased).
    Must stay identical to datagen._canon so data-synthesizer names group the same way."""
    return name.split(".")[-1].strip().lower()


def _is_garbled_table_key(key: str) -> bool:
    """True for a table key that will not survive downstream and must be renamed
    by the data-synthesizer before datagen. Two failure modes seen in real workloads:
      - a SQL fragment captured as a table name (e.g. a `spark.sql("\\nselect *
        from dm_ops")` body) → carries a leading newline/underscore and internal
        whitespace, yielding garbage `SCOS_INPUT_*` env-var names;
      - a leading-underscore key → datagen derives the mock parquet filename from
        the key, and Spark's hidden-file filter rejects files whose name starts
        with `_`, so the mock is unreadable.
    Detected by leading `_`, embedded whitespace, or emptiness — these cover the
    observed cases without false-positiving on legitimate identifiers that merely
    contain words like `select`/`from` (e.g. `select_from_options`). We do NOT
    rename here (the key is the canonical match id shared with datagen._canon); we
    flag it so the data-synthesizer renames it and datagen validation stays red until it does."""
    if not key or not key.strip():
        return True
    k = key.strip()
    if k.startswith("_"):
        return True
    if re.search(r"\s", k):  # whitespace never belongs in a table key
        return True
    return False


def _case_dedupe_tables(tables: dict) -> dict:
    """Merge table entries whose fully-qualified names differ only by case.

    When the same physical table is referenced as e.g. ``MY_DB.T_EVENTS`` and
    ``my_db.t_events`` in the same entrypoint, the miner produces two entries.
    This collapses them: keeps the first-seen canonical casing, unions columns
    (deduped by name), and merges the ``_role`` (read+write → readwrite)."""
    by_lower: dict[str, str] = {}   # lowercase key -> canonical (first-seen) key
    out: dict = {}
    for name, entry in tables.items():
        k = name.lower()
        if k in by_lower:
            canon = by_lower[k]
            d = out[canon]
            have = {c["name"] for c in d.get("columns", [])}
            for c in entry.get("columns", []):
                if c["name"] not in have:
                    d.setdefault("columns", []).append(c)
                    have.add(c["name"])
                else:
                    # merge richer metadata from duplicate into existing column
                    for ec in d.get("columns", []):
                        if ec["name"] == c["name"]:
                            if not ec.get("values") and c.get("values"):
                                ec["values"] = c["values"]
                            if not ec.get("type") and c.get("type"):
                                ec["type"] = c["type"]
                            break
            cur_role = d.get("_role", "read")
            new_role = entry.get("_role", "read")
            if cur_role != new_role:
                d["_role"] = "readwrite"
        else:
            by_lower[k] = name
            out[name] = entry
    return out


def _source_category(s: dict) -> str:
    """Provisioning category derived from the mined reader: 'table' (catalog read
    -> CREATE TABLE + load), 'connector' (jdbc/snowflake/... -> materialize as a
    table), or 'file' (path read / non-relational document -> stage the file)."""
    if not s.get("relational", True):
        return "file"
    fmt = (s.get("format") or "").lower()
    rm = (s.get("reader_method") or "").lower()
    if fmt in _CONNECTOR_FMTS or rm in _CONNECTOR_FMTS:
        return "connector"
    if rm in ("table", "saveastable", "insertinto") or fmt == "table":
        return "table"
    return "file"


def _invocation_mode(entry_abs: str, reasons: list) -> tuple[str, str | None]:
    """Decide how the harness runs this entrypoint:
      - 'script'  -> runpy.run_path(run_name='__main__') runs it (a __main__ guard,
                     a Databricks notebook, or module-level Spark I/O fires on run).
      - 'callable'-> Spark work lives only inside a function the file never auto-
                     invokes; return that function name when exactly one top-level
                     def contains Spark I/O (else None -> data-synthesizer fills it)."""
    if {"main_guard", "databricks_notebook", "module_spark_io", "ipynb"} & set(reasons or []):
        return "script", None
    try:
        tree = ast.parse(_read_source(entry_abs))
    except Exception:
        return "callable", None
    spark_fns = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and \
                        inner.attr in (_READ_METHODS | _WRITE_ATTRS | {"sql"}):
                    spark_fns.append(node.name)
                    break
    return "callable", (spark_fns[0] if len(spark_fns) == 1 else None)


# Lines of source code per +1 weight unit. LOC is a coarse complexity proxy on
# top of the table-dependency terms; dividing keeps it commensurate with them
# (a ~150-line entrypoint adds ~3, not ~150). Tunable.
LOC_WEIGHT_DIVISOR = 50


def _count_sloc(path: str) -> int:
    """Source lines of code: non-blank lines that aren't pure comments. A coarse
    but cheap complexity proxy; returns 0 if the file can't be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for line in fh
                       if line.strip() and not line.lstrip().startswith("#"))
    except OSError:
        return 0


def _compute_ep_weight(ep: dict, root: str = None) -> dict:
    tables = ep.get("tables") or {}
    n_read = sum(1 for t in tables.values() if t.get("access", "read") in ("read", "readwrite"))
    n_write = sum(1 for t in tables.values() if t.get("access") in ("write", "readwrite"))
    loc = _count_sloc(os.path.join(root, ep["path"])) if root and ep.get("path") else 0
    loc_weight = loc // LOC_WEIGHT_DIVISOR
    weight = 1 + 2 * n_read + n_write + loc_weight
    return {"weight": weight,
            "weight_breakdown": {"n_read_tables": n_read, "n_write_tables": n_write,
                                 "loc": loc, "loc_weight": loc_weight}}


_TEMPORAL_TYPES = ("timestamp", "date")
_DATE_SHAPE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")


def _looks_temporal(values) -> bool:
    """True only when every sampled string value is date/timestamp-shaped. Empty /
    non-string value sets are NOT temporal-shaped (caller handles the empty case)."""
    if not values:
        return False
    strs = [v for v in values if isinstance(v, str)]
    if not strs:
        return False
    return all(_DATE_SHAPE_RE.match(v) for v in strs)


def _reconcile_temporal_types(tables: dict) -> None:
    """Reconcile temporal types when the same logical column name appears in multiple
    mined tables with conflicting types AND at least one occurrence is temporally typed.
    Reconcile the non-temporal occurrences to that temporal type -- but ONLY when a
    given column's own sampled values are date/timestamp-shaped. This fixes inconsistent
    MOCK typing (e.g. ``ts_col`` string in one source, timestamp in its siblings) that
    manufactures false ``TIMESTAMP <= STRING`` failures.

    HARD RULE: never promote a type solely to make validation pass. Promotion requires
    POSITIVE date-shaped evidence: a column with concrete non-date values is left alone,
    and a column with NO sampled values is ALSO left as-is -- an explicitly declared
    STRING type (e.g. from a read ``StructType``) must be preserved so the genuine SCOS
    string/temporal coercion gap (migration rule 29) surfaces rather than being masked."""
    by_name: dict[str, list] = {}
    for t in tables.values():
        if not isinstance(t, dict):
            continue
        for c in t.get("columns", []):
            by_name.setdefault(c["name"], []).append(c)

    for _name, cols in by_name.items():
        types = {c.get("type") for c in cols}
        temporal = [t for t in types if t in _TEMPORAL_TYPES]
        if len(types) <= 1 or not temporal:
            continue                          # consistent, or no temporal evidence
        target = "timestamp" if "timestamp" in temporal else "date"
        for c in cols:
            if c.get("type") in _TEMPORAL_TYPES:
                continue
            vals = c.get("values")
            # promote ONLY on positive date-shaped evidence. A column with no sampled
            # values is left as-is (preserving the STRING->TIMESTAMP coercion signal);
            # concrete non-date values are also left untouched.
            if _looks_temporal(vals):
                c["type"] = target


def synthesize(root: str, entrypoints: list[str] | None = None) -> dict:
    """Take a workload directory and produce a deterministic analysis:
    every Spark entrypoint, and within each, its sources and sinks with mined
    schema + completeness/LLM-todo. Entrypoints are auto-detected unless an
    explicit list of paths (abs or rel to root) is supplied.

    StructType defs and read-helpers are pooled across the whole workload so
    cross-file reads/schemas resolve. Intermediate sources (one entrypoint's
    sink re-read by another) are filled from sibling sinks."""
    root = os.path.abspath(root)
    pkg_roots = _package_roots(root)
    detected, _facts = detect_entrypoints(root)
    if entrypoints:
        want = {os.path.abspath(e if os.path.isabs(e) else os.path.join(root, e)) for e in entrypoints}
        ep_list = [e for e in detected if e["abs"] in want]
        # include explicitly-requested files even if not auto-detected
        have = {e["abs"] for e in ep_list}
        for e in want - have:
            if os.path.isfile(e):
                ep_list.append({"id": _slug(e, root), "path": os.path.relpath(e, root),
                                "abs": e, "reasons": ["explicit"],
                                "import_roots": [os.path.relpath(r, root) or "." for r in pkg_roots]})
    else:
        ep_list = detected

    # pool StructTypes + read-helpers across the whole workload
    all_files = _iter_py_files(root)
    pooled_structs: dict = {}
    for f in all_files:
        try:
            sv = _StructVisitor(_collect_const_ints(ast.parse(_read_source(f))))
            sv.visit(ast.parse(_read_source(f)))
            for k, v in sv.schemas.items():
                pooled_structs.setdefault(k, v)
        except Exception:
            pass
    pooled_helpers, pooled_helper_schemas, pooled_helper_readers = _find_read_helpers(all_files)
    pooled_write_helpers = _find_write_helpers(all_files)
    # catalog every .sql template file in the project (path-keyed, not entrypoint-keyed).
    sql_files = _catalog_sql_files(root)

    out_eps, sink_owner = [], {}
    _RANK = {"deterministic": 5, "deterministic_intermediate": 5, "high_confidence": 4,
             "medium_confidence": 3, "partial": 2, "llm_required": 1}

    def _merge(dst: dict, src: dict):
        """Union sources/sinks by name across closure files (merge columns;
        keep the higher-confidence completeness)."""
        for name, s in src.items():
            if name not in dst:
                dst[name] = s
                continue
            d = dst[name]
            have = {c["name"] for c in d["columns"]}
            for c in s["columns"]:
                if c["name"] not in have:
                    d["columns"].append(c); have.add(c["name"])
            if _RANK.get(s.get("completeness"), 0) > _RANK.get(d.get("completeness"), 0):
                d["completeness"] = s["completeness"]; d["llm_reason"] = s.get("llm_reason")
            d["provenance"] = sorted(set(d.get("provenance", [])) | set(s.get("provenance", [])))

    for ep in ep_list:
        roots = [os.path.join(root, r) for r in ep.get("import_roots", ["."])]
        # mine the entrypoint AND every file in its import closure, then union —
        # reads/writes often live in imported reader/transformer/writer modules.
        closure = ep.get("closure") or [ep["path"]]
        closure_abs = [os.path.join(root, c) for c in closure]
        if ep["abs"] not in closure_abs:
            closure_abs.insert(0, ep["abs"])
        sources, sinks = {}, {}
        validation = {}
        join_edges: list = []
        range_join_edges: list = []
        sql_fragment_cols: set = set()  # union of column identifiers from SQL fragments
        ep_display_only = False
        ep_display_sinks: list = []
        err = None
        for cf in closure_abs:
            try:
                c = mine(cf, roots, extra_struct_schemas=pooled_structs,
                        extra_read_helpers=pooled_helpers,
                        extra_helper_schemas=pooled_helper_schemas,
                        extra_write_helpers=pooled_write_helpers,
                        extra_helper_readers=pooled_helper_readers)
            except Exception as exc:
                err = "%s: %s" % (type(exc).__name__, exc)
                continue
            is_ep = os.path.abspath(cf) == os.path.abspath(ep["abs"])
            csrc, csink = c.get("_sources", {}), c.get("_sinks", {})
            if not is_ep:
                csrc = {k: v for k, v in csrc.items() if not v.get("in_def")}
                csink = {k: v for k, v in csink.items() if not v.get("in_def")}
            _merge(sources, csrc)
            _merge(sinks, csink)
            join_edges.extend(c.get("_joins", []) or [])
            sql_fragment_cols.update(c.get("_sql_fragment_cols", []) or [])
            range_join_edges.extend(c.get("_range_joins", []) or [])
            if is_ep:
                validation = c.get("validation", {})
                if c.get("display_only"):
                    ep_display_only = True
                    ep_display_sinks = c.get("display_sinks", [])
        if not sources and not sinks and err:
            out_eps.append({"id": ep["id"], "path": ep["path"], "error": err})
            continue
        # rewrite `defined_at` provenance to be relative to workload root.
        for obj in list(sources.values()) + list(sinks.values()):
            da = obj.get("defined_at")
            if da and ":" in da:
                fp, _, ln = da.rpartition(":")
                try:
                    obj["defined_at"] = "%s:%s" % (os.path.relpath(fp, root), ln)
                except ValueError:
                    pass
        # §9: merge sources + sinks into unified `tables` dict with `access`
        tables = {}
        for name, s in sources.items():
            tables[name] = {**s, "_role": "read"}
        for name, sk in sinks.items():
            if name in tables:
                # Both read and written — merge into readwrite
                tables[name]["_role"] = "readwrite"
                # Merge sink columns into existing entry
                have = {c["name"] for c in tables[name].get("columns", [])}
                for c in sk.get("columns", []):
                    if c["name"] not in have:
                        tables[name].setdefault("columns", []).append(c)
                        have.add(c["name"])
                # Carry sink metadata
                if sk.get("defined_at") and not tables[name].get("defined_at"):
                    tables[name]["defined_at"] = sk["defined_at"]
                if sk.get("natural_keys"):
                    tables[name]["natural_keys"] = sk["natural_keys"]
            else:
                tables[name] = {**sk, "_role": "write"}
        # Merge entries that differ only by case (same physical table referenced
        # with different casing in the same script or its imports).
        tables = _case_dedupe_tables(tables)
        # reconcile the same logical column across tables to a temporal type
        # when there is a genuine date-shaped inconsistency (never merely to pass).
        _reconcile_temporal_types(tables)
        # Flag garbled keys (SQL fragments / leading-underscore) with a mandatory
        # llm_todo so the data-synthesizer renames them before datagen — datagen derives
        # mock filenames and SCOS_INPUT_* env vars from the key, and both break on
        # these shapes. Does not rename (preserves the _bare/_canon match id).
        for _k, _tbl in tables.items():
            if (isinstance(_tbl, dict) and _is_garbled_table_key(_k)
                    and "llm_todo" not in _tbl):
                _tbl["llm_todo"] = (
                    f"rename garbled table key {_k!r} to a clean identifier "
                    "(no whitespace, SQL fragments, or leading underscore) — "
                    "datagen mock filenames / SCOS_INPUT_* env vars derive from it")
        # keep only join edges whose BOTH endpoint tables survived into `tables`
        # (dangling edges from filtered-out def-local reads would never resolve).
        ep_joins: list = []
        seen_j: set = set()
        for e in join_edges:
            lt = e.get("left", "").rpartition(".")[0]
            rt = e.get("right", "").rpartition(".")[0]
            if lt in tables and rt in tables:
                key = tuple(sorted((e["left"], e["right"])))
                if key not in seen_j:
                    seen_j.add(key)
                    ep_joins.append({"left": e["left"], "right": e["right"]})
        # keep only range-join edges whose point/low/high tables all survived.
        ep_range_joins: list = []
        seen_rj: set = set()
        for e in range_join_edges:
            pt = e.get("point", "").rpartition(".")[0]
            lt = e.get("low", "").rpartition(".")[0]
            ht = e.get("high", "").rpartition(".")[0]
            if pt in tables and lt in tables and ht in tables:
                key = (e["point"], e["low"], e["high"])
                if key not in seen_rj:
                    seen_rj.add(key)
                    ep_range_joins.append({"point": e["point"], "low": e["low"],
                                           "high": e["high"]})
        rec = {"id": ep["id"], "path": ep["path"],
                "import_roots": ep.get("import_roots"),
                "closure": sorted({os.path.relpath(c, root) for c in closure_abs}),
                "tables": tables,
                "joins": ep_joins,
                "range_join_edges": ep_range_joins,
                "sql_validation": validation,
                "referenced_columns": sorted(sql_fragment_cols)}
        if ep_display_only:
            rec["display_only"] = True
            rec["display_sinks"] = ep_display_sinks
        run_mode, callable_name = _invocation_mode(ep["abs"], ep.get("reasons", []))
        rec["run_mode"] = run_mode
        # Detect Databricks-native workload from static markers + source scan
        _reasons = set(ep.get("reasons") or [])
        _src_text = _read_source(ep["abs"])
        rec["source_runtime"] = (
            "databricks"
            if ("databricks_notebook" in _reasons or bool(re.search(r"\bdbutils\b", _src_text)))
            else "spark"
        )
        # entrypoint_kwargs: JSON-schema-required for every EP. Seed with names
        # imported from unresolvable local modules — those are runtime parameters
        # the harness must inject (data-synthesizer fills in default values). Callable
        # entrypoints also get their callable name recorded.
        rec["entrypoint_kwargs"] = _seed_entrypoint_kwargs(
            ep["abs"], [os.path.join(root, r) for r in ep.get("import_roots", ["."])]
        )
        if run_mode == "callable":
            rec["entrypoint_callable"] = callable_name        # may be None -> data-synthesizer fills
        out_eps.append(rec)
        for tname, tbl in rec["tables"].items():
            if tbl.get("_role") in ("write", "readwrite"):
                key = _bare(tname)
                if tbl.get("columns"):
                    sink_owner.setdefault(key, (ep["id"], tname, tbl["columns"]))

    # Cross-entrypoint schema inference: a table that is written by one
    # entrypoint and read by another inherits the producer's columns.
    for rec in out_eps:
        for tname, tbl in rec.get("tables", {}).items():
            if tbl.get("_role") != "read":
                continue
            key = _bare(tname)
            owner = sink_owner.get(key)
            if owner:
                _writer_ep, _sink_name, cols = owner
                have = {col["name"] for col in tbl.get("columns", [])}
                for col in cols:
                    if col["name"] not in have:
                        tbl.setdefault("columns", []).append({**col, "origin": "intermediate_sink"})
                if tbl.get("completeness") in ("llm_required", "medium_confidence"):
                    tbl["completeness"] = "deterministic_intermediate"
                    types_uncertain = any(c.get("type", "string") == "string"
                                          for c in tbl.get("columns", []))
                    tbl["llm_reason"] = (
                        "column names inherited from the producer of this intermediate "
                        "table; column TYPES are defaulted to string and could not be "
                        "inferred statically -- confirm/fill the real types."
                        if types_uncertain else None)

    # --- simplify to a lean, completable contract: each table entry carries
    # access + columns; anything incomplete carries an ``llm_todo``. ---
    def _lean_cols(cols):
        out = []
        for c in cols:
            d = {"name": c["name"], "type": c.get("type", "string"),
                 "nullable": c.get("nullable", True)}
            if c.get("values"):
                d["values"] = c["values"]
            if c.get("unique"):
                d["unique"] = c["unique"]
            out.append(d)
        return out

    n_tables = 0
    for rec in out_eps:
        tables = rec.get("tables", {})
        for name, t in list(tables.items()):
            role = t.pop("_role", "read")
            access = role  # read / write / readwrite
            reason = t.get("llm_reason")

            if role in ("read", "readwrite"):
                # This was a source (possibly also a sink for readwrite)
                if not t.get("relational", True):
                    keep = {"access": access, "relational": False, "category": "file",
                            "format": t.get("format"),
                            "original_path": t.get("path") or name,
                            "defined_at": t.get("defined_at"),
                            "document_schema": t.get("document_schema"),
                            "columns": _lean_cols(t.get("columns", []))}
                    if t.get("path"):
                        keep["path"] = t["path"]
                else:
                    keep = {"access": access, "relational": True,
                            "category": _source_category(t),
                            "reader_method": t.get("reader_method"),
                            "format": t.get("format"),
                            "original_path": t.get("path") or name,
                            "defined_at": t.get("defined_at"),
                            "columns": _lean_cols(t.get("columns", []))}
                    if t.get("reader_options"):
                        keep["reader_options"] = t["reader_options"]
                    if t.get("dynamic_read"):
                        keep["dynamic_read"] = True
                        keep["fanout"] = t.get("fanout")
                if t.get("natural_keys"):
                    keep["natural_keys"] = t["natural_keys"]
            else:
                # write-only (was a sink)
                keep = {"access": "write",
                        "category": t.get("kind", "table"),
                        "original_path": name,
                        "defined_at": t.get("defined_at"),
                        "columns": _lean_cols(t.get("columns", []))}
                if t.get("natural_keys"):
                    keep["natural_keys"] = t["natural_keys"]

            if reason:
                keep["llm_todo"] = reason
            elif t.get("llm_todo"):
                keep["llm_todo"] = t["llm_todo"]
            elif access in ("write", "readwrite") and not keep.get("columns"):
                keep["llm_todo"] = "table schema could not be mined; LLM should supply it."
            elif keep.get("columns") and all(
                c.get("type", "string") == "string" for c in keep["columns"]
            ):
                # Names were mined but every TYPE defaulted to 'string' — these came
                # from the AST/SQL layer, not an exact StructType, so they are a guess
                # the LLM must confirm rather than silently trust. (Real schemas are
                # rarely all-string.) For a connector/JDBC source read via a
                # SELECT-with-aliases query, the mined columns are the projected
                # aliases only — the underlying WHERE/JOIN source columns are missing.
                keep["llm_todo"] = (
                    "column NAMES mined but every TYPE defaulted to 'string' (AST/SQL "
                    "layer, not an explicit StructType): confirm each type against the "
                    "source (StructField / cast / numeric use). If this is a "
                    "connector/JDBC source whose query SELECTs aliased columns, also "
                    "add the underlying WHERE/JOIN source columns, not just the aliases."
                )
            t.clear(); t.update(keep)
            n_tables += 1

    open_todos = sum(1 for rec in out_eps
                     for it in rec.get("tables", {}).values() if "llm_todo" in it)
    open_todos += sum(1 for sf in sql_files if sf.get("llm_todo"))
    n_nonrel = sum(1 for rec in out_eps for t in rec.get("tables", {}).values()
                   if not t.get("relational", True))
    return {"root": root, "complete": open_todos == 0, "entrypoints": out_eps,
            "sql_files": sql_files,
            "summary": {"n_entrypoints": len(out_eps), "n_tables": n_tables,
                        "n_non_relational": n_nonrel,
                        "n_sql_files": len(sql_files),
                        "open_todos": open_todos,
                        "n_databricks_entrypoints": sum(
                            1 for ep in out_eps if ep.get("source_runtime") == "databricks"
                        )},
            "weights": {ep["id"]: _compute_ep_weight(ep, root) for ep in out_eps if "id" in ep}}




_SKIP_DIRS = {"__pycache__", ".git", "tests", "test", ".venv", "venv", "build", "dist",
              ".ipynb_checkpoints"}


def _iter_py_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if (fn.endswith(".py") and not fn.endswith("_scos.py")) or fn.endswith(".ipynb"):
                out.append(os.path.join(dirpath, fn))
    return out



# ---------------------------------------------------------------------------
# Dynamic-import site detection (opt-in via mine(detect_dynamic_imports=True))
# ---------------------------------------------------------------------------
#
# When a workload's orchestrator selects its reader / transformer / writer
# modules from a config file (Kipawa-style pipeline manifests, or any bespoke
# vocabulary a customer invented), the module name arrives at
# ``importlib.import_module(...)`` as an expression that traces back to a
# ``cfg["KEY"]`` subscript. The caller (the assessment scanner) uses this to
# discover the config key name from the code itself instead of relying on a
# hardcoded list. Behaviour is guarded by a parameter and off by default so
# validation callers see no change.


def _collect_assignments_for_dynamic_imports(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """Map name / attribute-name → list of value expressions assigned to it.

    Tracks ``x = ...``, ``self.x = ...``, and ``x: T = ...`` so a name reached
    from an ``importlib.import_module`` argument can be traced back to the
    subscript (``cfg["KEY"]``) or literal that originally provided it. Purely
    AST-based; no runtime import needed.

    NOTE: This is the FLAT (unscoped) name→assignments view passed to
    :func:`_find_dynamic_import_sites`. That function also uses the scoped
    variant :func:`_collect_scoped_assignments` so per-function lookups don't
    cross function boundaries — a name like ``n`` reassigned inside multiple
    methods must resolve to its own local assignment, not the first one seen
    anywhere in the file."""
    out: dict[str, list[ast.AST]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    out.setdefault(tgt.id, []).append(n.value)
                elif isinstance(tgt, ast.Attribute):
                    out.setdefault(tgt.attr, []).append(n.value)
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            tgt = n.target
            if isinstance(tgt, ast.Name):
                out.setdefault(tgt.id, []).append(n.value)
            elif isinstance(tgt, ast.Attribute):
                out.setdefault(tgt.attr, []).append(n.value)
    return out


def _scope_of_nodes(tree: ast.AST) -> dict[int, int]:
    """Map ``id(node) → id(enclosing_function_or_module)`` for every AST node.

    The enclosing scope is the innermost ``FunctionDef`` / ``AsyncFunctionDef``
    / ``Lambda`` containing the node, falling back to ``id(tree)`` (the module)
    for top-level nodes. Used by :func:`_collect_scoped_assignments` and
    :func:`_find_dynamic_import_sites` so an assignment inside one method
    doesn't leak into another method that reuses the same local variable
    name (e.g. Kipawa's ``PipelineImpl._reader`` and ``._writer`` both
    binding a local ``n``)."""
    scope: dict[int, int] = {}

    def _walk(node: ast.AST, current: int) -> None:
        scope[id(node)] = current
        next_scope = current
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            next_scope = id(node)
        for child in ast.iter_child_nodes(node):
            _walk(child, next_scope)

    _walk(tree, id(tree))
    return scope


def _collect_scoped_assignments(
    tree: ast.AST, scope_of: dict[int, int]
) -> dict[tuple[int, str], list[ast.AST]]:
    """Same as :func:`_collect_assignments_for_dynamic_imports` but keyed by
    ``(scope_id, name)``. Assignments in one function don't leak into
    lookups from another function; module-level assignments live under
    the module's scope id."""
    out: dict[tuple[int, str], list[ast.AST]] = {}
    for n in ast.walk(tree):
        scope_id = scope_of.get(id(n), 0)
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                name: str | None = None
                if isinstance(tgt, ast.Name):
                    name = tgt.id
                elif isinstance(tgt, ast.Attribute):
                    name = tgt.attr
                if name is not None:
                    out.setdefault((scope_id, name), []).append(n.value)
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            tgt = n.target
            name = None
            if isinstance(tgt, ast.Name):
                name = tgt.id
            elif isinstance(tgt, ast.Attribute):
                name = tgt.attr
            if name is not None:
                out.setdefault((scope_id, name), []).append(n.value)
    return out


def _find_dynamic_import_sites(
    tree: ast.AST,
    assignments: dict[str, list[ast.AST]],
) -> list[dict]:
    """Detect dynamic-import call sites and describe the pattern each uses.

    Each returned dict has the shape::

        {
          "line": int,                      # source line of the call
          "kind": str,                      # detection kind — see below
          "config_key": str | None,         # for import_module / __import__
          "container_key": str | None,      # for import_module / __import__
          "path_arg": str | None,           # for spec_from_file / imp_load_source
          "path_arg_raw": str | None,       # for spec_from_file / imp_load_source
          "entry_point_group": str | None,  # for entry_point
          "entry_point_name": str | None,   # for entry_point
          "dict_var_name": str | None,      # for factory_dict
          "candidate_classes": list[str],   # for factory_dict (empty otherwise)
          "dispatch_key": str | None,       # for factory_dict
          "raw_expr": str,                  # ast.unparse of the argument
        }

    Every returned entry carries ALL fields (filled with ``None`` / ``[]`` where
    not applicable) so callers don't need per-kind branching to read the shape.

    Detection kinds:
      * ``import_module`` — ``importlib.import_module(EXPR)`` OR bare
        ``import_module(EXPR)``. Traces EXPR back to ``cfg["KEY"]`` /
        ``cfg.get("KEY")`` / for-loop containers, as before.
      * ``__import__`` — the builtin ``__import__(EXPR)`` call. Same tracing
        as ``import_module``.
      * ``spec_from_file`` — ``importlib.util.spec_from_file_location(NAME, PATH)``.
        The path argument is captured via ``path_arg`` (traced) and
        ``path_arg_raw`` (unparsed expression).
      * ``imp_load_source`` — ``imp.load_source(NAME, PATH)``. Same as
        ``spec_from_file``.
      * ``entry_point`` — three subforms:
          (a) ``pkg_resources.load_entry_point(DIST, GROUP, NAME)``
          (b) ``importlib.metadata.entry_points(group=GROUP)`` followed by
              ``.load()``
          (c) ``entry_points()[GROUP]`` variants
        Populates ``entry_point_group`` and ``entry_point_name`` (either
        literal string or ``None`` when not statically known).
      * ``factory_dict`` — module-level (or function-level) assignments of
        form ``X = {LITERAL_KEY: NAME_REF, ...}`` where at least half the
        values are ``ast.Name`` references, coupled with a call site of form
        ``X[KEY_EXPR](...)``. Emits one factory_dict site per call site with
        ``dict_var_name=X``, ``candidate_classes`` populated from the dict
        values, and ``dispatch_key`` traced from ``KEY_EXPR`` if resolvable.
    """
    # Precompute for-loop scopes so a site can be tied back to its container.
    # For each variable bound by ``for X in EXPR``, capture EXPR for lookup.
    # We build a stack of (target_var, iter_expr) pairs and, at each
    # import_module call, we consult the loop annotation attached below.
    loop_targets_by_node: dict[int, tuple[str, ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            tgt = node.target
            tgt_name: str | None = None
            if isinstance(tgt, ast.Name):
                tgt_name = tgt.id
            elif isinstance(tgt, ast.Attribute):
                tgt_name = tgt.attr
            if tgt_name:
                # Walk the loop body to find every descendant node; mark them
                # as being inside this loop. We prefer the INNERMOST loop when
                # nested — the last update wins because the iterator we care
                # about is the closest enclosing one.
                for descendant in ast.walk(node):
                    loop_targets_by_node[id(descendant)] = (tgt_name, node.iter)

    # Scope-aware assignment lookup. The flat ``assignments`` argument mixes
    # every method's locals into one bucket, which mis-resolves a name like
    # ``n`` when reused across methods (e.g. Kipawa's ``_reader`` / ``_writer``
    # each bind ``n = cfg[...]`` to a different key). Build a scoped view
    # here and walk outward — current function → outer function → module —
    # so a name binds to its innermost visible definition, matching Python
    # scoping semantics.
    scope_of = _scope_of_nodes(tree)
    scoped_assignments = _collect_scoped_assignments(tree, scope_of)
    module_scope_id = id(tree)

    def _scope_chain(scope_id: int) -> list[int]:
        """Innermost → outermost list of enclosing function scope ids,
        terminating at the module scope."""
        # We don't have a direct child→parent scope map; instead, look up
        # each scope's own enclosing scope by finding the scope of the
        # scope-defining node itself. ``scope_of[node_id]`` returns the
        # ENCLOSING scope, so a function's outer scope is
        # ``scope_of[function_node_id]``. We iterate until we hit the module.
        chain = [scope_id]
        # Reverse the id→scope map lazily by scanning; small overhead
        # bounded by nesting depth (usually 1-3).
        while scope_id != module_scope_id:
            outer = scope_of.get(scope_id, module_scope_id)
            if outer == scope_id:
                break
            chain.append(outer)
            scope_id = outer
        if module_scope_id not in chain:
            chain.append(module_scope_id)
        return chain

    def _lookup_assignments(name: str, at_scope: int) -> list[ast.AST]:
        """Return assignment values for ``name`` visible from ``at_scope``,
        walking outward from the innermost enclosing function to the module."""
        vals: list[ast.AST] = []
        for scope_id in _scope_chain(at_scope):
            vals.extend(scoped_assignments.get((scope_id, name), []))
            if vals:
                # First scope with a match wins — matches Python's
                # LEGB-ish resolution for our narrow use case.
                return vals
        return vals

    def _lookup_attr_assignments(attr: str) -> list[ast.AST]:
        """Return ALL assignment values for an attribute name across every
        scope in the file.

        Attributes on ``self`` are shared instance state — a value set in
        ``__init__`` is meant to be read from every other method. Unlike
        plain local names, they are not scoped by function; searching only
        the current function would miss the ``self.mod = cfg['KEY']`` set
        in ``__init__`` when the ``import_module`` call lives in ``load``.
        """
        vals: list[ast.AST] = []
        for (_scope_id, name), values in scoped_assignments.items():
            if name == attr:
                vals.extend(values)
        return vals

    def _subscript_key(expr: ast.AST) -> str | None:
        """``cfg["KEY"]`` → 'KEY'; anything else → None."""
        if isinstance(expr, ast.Subscript):
            sl = expr.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                return sl.value
        return None

    def _get_call_key(expr: ast.AST) -> str | None:
        """``obj.get("KEY", ...)`` → 'KEY'; anything else → None."""
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            if expr.func.attr == "get" and expr.args:
                arg0 = expr.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    return arg0.value
        return None

    def _resolve_expr(expr: ast.AST, depth: int = 0, at_scope: int | None = None) -> str | None:
        """Try to resolve ``expr`` to a config-key name. Follows names /
        attributes back through ``assignments`` up to a shallow depth.

        ``at_scope`` — the scope id of the call site invoking this lookup.
        Assignments are searched within that scope first and then walked
        outward, mirroring Python's lexical scoping. Defaults to the
        module scope so callers that don't know a scope still get a
        best-effort lookup."""
        if depth > 6:
            return None
        k = _subscript_key(expr)
        if k:
            return k
        k = _get_call_key(expr)
        if k:
            return k
        lookup_scope = at_scope if at_scope is not None else module_scope_id
        if isinstance(expr, ast.Name):
            for av in _lookup_assignments(expr.id, lookup_scope):
                r = _resolve_expr(av, depth + 1, at_scope=lookup_scope)
                if r:
                    return r
            return None
        if isinstance(expr, ast.Attribute):
            for av in _lookup_attr_assignments(expr.attr):
                r = _resolve_expr(av, depth + 1, at_scope=lookup_scope)
                if r:
                    return r
            return None
        return None

    def _unparse(expr: ast.AST) -> str:
        try:
            return ast.unparse(expr)
        except Exception:
            return ""

    def _empty_site() -> dict:
        return {
            "line": 0,
            "kind": "",
            "config_key": None,
            "container_key": None,
            "path_arg": None,
            "path_arg_raw": None,
            "entry_point_group": None,
            "entry_point_name": None,
            "dict_var_name": None,
            "candidate_classes": [],
            "dispatch_key": None,
            "raw_expr": "",
        }

    def _literal_str(expr: ast.AST) -> str | None:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        return None

    def _resolve_to_string(expr: ast.AST, depth: int = 0, at_scope: int | None = None) -> str | None:
        """Best-effort trace of ``expr`` to a string literal, following name/
        attribute assignments up to a shallow depth. Scope-aware — see
        :func:`_resolve_expr`."""
        if depth > 6:
            return None
        s = _literal_str(expr)
        if s is not None:
            return s
        lookup_scope = at_scope if at_scope is not None else module_scope_id
        if isinstance(expr, ast.Name):
            for av in _lookup_assignments(expr.id, lookup_scope):
                r = _resolve_to_string(av, depth + 1, at_scope=lookup_scope)
                if r is not None:
                    return r
            return None
        if isinstance(expr, ast.Attribute):
            for av in _lookup_attr_assignments(expr.attr):
                r = _resolve_to_string(av, depth + 1, at_scope=lookup_scope)
                if r is not None:
                    return r
            return None
        return None

    # ---- factory_dict prep -------------------------------------------------
    # Discover module/function-level assignments X = {LITERAL: NAME_REF, ...}
    # where at least half of the values are ast.Name references.
    factory_dicts: dict[str, list[str]] = {}
    for name, values in assignments.items():
        for v in values:
            if not isinstance(v, ast.Dict):
                continue
            if not v.keys:
                continue
            # Every key must be a literal string constant (skip otherwise —
            # non-literal keys can't reasonably be dispatch keys).
            literal_keys = all(
                isinstance(k, ast.Constant) and isinstance(k.value, str)
                for k in v.keys
            )
            if not literal_keys:
                continue
            name_values = [val for val in v.values if isinstance(val, ast.Name)]
            if len(name_values) * 2 < len(v.values):
                continue
            candidate_classes = [nv.id for nv in name_values]
            if not candidate_classes:
                continue
            factory_dicts[name] = candidate_classes
            break

    sites: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Scope of THIS call site — used so name-resolution inside its
        # argument tree respects the enclosing function's locals rather
        # than picking up an unrelated same-named binding from another
        # method in the same file.
        _call_scope = scope_of.get(id(node), module_scope_id)

        # --- import_module / bare import_module -----------------------------
        is_import_module = False
        if isinstance(func, ast.Attribute) and func.attr == "import_module":
            if isinstance(func.value, ast.Name) and func.value.id == "importlib":
                is_import_module = True
        elif isinstance(func, ast.Name) and func.id == "import_module":
            is_import_module = True
        if is_import_module and node.args:
            arg = node.args[0]
            raw_expr = _unparse(arg)
            site = _empty_site()
            site["line"] = getattr(node, "lineno", 0)
            site["kind"] = "import_module"
            site["raw_expr"] = raw_expr
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sites.append(site)
                continue
            site["config_key"] = _resolve_expr(arg, at_scope=_call_scope)
            loop_info = loop_targets_by_node.get(id(node))
            if loop_info is not None:
                _target_name, iter_expr = loop_info
                site["container_key"] = _resolve_expr(iter_expr, at_scope=_call_scope)
            sites.append(site)
            continue

        # --- __import__ ------------------------------------------------------
        if isinstance(func, ast.Name) and func.id == "__import__" and node.args:
            arg = node.args[0]
            raw_expr = _unparse(arg)
            site = _empty_site()
            site["line"] = getattr(node, "lineno", 0)
            site["kind"] = "__import__"
            site["raw_expr"] = raw_expr
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sites.append(site)
                continue
            site["config_key"] = _resolve_expr(arg, at_scope=_call_scope)
            loop_info = loop_targets_by_node.get(id(node))
            if loop_info is not None:
                _target_name, iter_expr = loop_info
                site["container_key"] = _resolve_expr(iter_expr, at_scope=_call_scope)
            sites.append(site)
            continue

        # --- spec_from_file_location ----------------------------------------
        is_spec_from_file = False
        if isinstance(func, ast.Attribute) and func.attr == "spec_from_file_location":
            # importlib.util.spec_from_file_location(...) OR util.spec_from_file_location(...)
            is_spec_from_file = True
        elif isinstance(func, ast.Name) and func.id == "spec_from_file_location":
            is_spec_from_file = True
        if is_spec_from_file and len(node.args) >= 2:
            path_arg = node.args[1]
            site = _empty_site()
            site["line"] = getattr(node, "lineno", 0)
            site["kind"] = "spec_from_file"
            site["raw_expr"] = _unparse(path_arg)
            site["path_arg"] = _resolve_to_string(path_arg, at_scope=_call_scope)
            site["path_arg_raw"] = _unparse(path_arg)
            sites.append(site)
            continue

        # --- imp.load_source ------------------------------------------------
        is_imp_load_source = False
        if isinstance(func, ast.Attribute) and func.attr == "load_source":
            if isinstance(func.value, ast.Name) and func.value.id == "imp":
                is_imp_load_source = True
        if is_imp_load_source and len(node.args) >= 2:
            path_arg = node.args[1]
            site = _empty_site()
            site["line"] = getattr(node, "lineno", 0)
            site["kind"] = "imp_load_source"
            site["raw_expr"] = _unparse(path_arg)
            site["path_arg"] = _resolve_to_string(path_arg, at_scope=_call_scope)
            site["path_arg_raw"] = _unparse(path_arg)
            sites.append(site)
            continue

        # --- pkg_resources.load_entry_point(DIST, GROUP, NAME) --------------
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "load_entry_point"
            and len(node.args) >= 3
        ):
            group_arg = node.args[1]
            name_arg = node.args[2]
            site = _empty_site()
            site["line"] = getattr(node, "lineno", 0)
            site["kind"] = "entry_point"
            site["raw_expr"] = _unparse(node)
            site["entry_point_group"] = _resolve_to_string(group_arg, at_scope=_call_scope)
            site["entry_point_name"] = _resolve_to_string(name_arg, at_scope=_call_scope)
            sites.append(site)
            continue

        # --- importlib.metadata.entry_points(group=GROUP).load() ------------
        # Detect the pattern: attr.load() where the .value is a call to
        # entry_points(...) with a ``group=...`` kwarg. The outer .load()
        # is what triggers detection so we get the "select then load" call.
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "load"
            and isinstance(func.value, ast.Call)
        ):
            inner = func.value
            inner_func = inner.func
            is_ep_call = False
            if isinstance(inner_func, ast.Attribute) and inner_func.attr == "entry_points":
                is_ep_call = True
            elif isinstance(inner_func, ast.Name) and inner_func.id == "entry_points":
                is_ep_call = True
            if is_ep_call:
                group_val: str | None = None
                name_val: str | None = None
                for kw in inner.keywords:
                    if kw.arg == "group":
                        group_val = _resolve_to_string(kw.value, at_scope=_call_scope)
                    elif kw.arg == "name":
                        name_val = _resolve_to_string(kw.value, at_scope=_call_scope)
                site = _empty_site()
                site["line"] = getattr(node, "lineno", 0)
                site["kind"] = "entry_point"
                site["raw_expr"] = _unparse(node)
                site["entry_point_group"] = group_val
                site["entry_point_name"] = name_val
                sites.append(site)
                continue

        # --- entry_points()[GROUP] / entry_points()[GROUP][NAME] ------------
        # Detect subscript on the result of entry_points() with a literal-string
        # subscript key (the group). Emit even when this isn't followed by
        # .load() — the assessment side still wants a chain lead.
        # We recognize the raw call node as the parent of a Subscript by
        # walking the tree separately below (this branch triggers only for
        # entry_points() calls without a following .load()).
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "entry_points"
        ) or (isinstance(func, ast.Name) and func.id == "entry_points"):
            # Skip when this entry_points() is the inner of a .load() chain —
            # the .load() branch above already emitted the site.
            parent_load = False
            # We don't have parent pointers, but the .load() branch consumes
            # `func.value` matches. Duplicate-emission is avoided by
            # de-duping later on `(line, kind, entry_point_group, entry_point_name)`.
            group_val = None
            name_val = None
            for kw in node.keywords:
                if kw.arg == "group":
                    group_val = _resolve_to_string(kw.value, at_scope=_call_scope)
                elif kw.arg == "name":
                    name_val = _resolve_to_string(kw.value, at_scope=_call_scope)
            if group_val or name_val:
                site = _empty_site()
                site["line"] = getattr(node, "lineno", 0)
                site["kind"] = "entry_point"
                site["raw_expr"] = _unparse(node)
                site["entry_point_group"] = group_val
                site["entry_point_name"] = name_val
                sites.append(site)
                continue

        # --- factory_dict call site: X[KEY_EXPR](...) -----------------------
        if isinstance(func, ast.Subscript) and isinstance(func.value, ast.Name):
            dict_name = func.value.id
            if dict_name in factory_dicts:
                site = _empty_site()
                site["line"] = getattr(node, "lineno", 0)
                site["kind"] = "factory_dict"
                site["raw_expr"] = _unparse(node)
                site["dict_var_name"] = dict_name
                site["candidate_classes"] = list(factory_dicts[dict_name])
                key_expr = func.slice
                site["dispatch_key"] = _resolve_expr(key_expr, at_scope=_call_scope)
                sites.append(site)
                continue

    # Deduplicate: entry_point sites can be emitted by both the .load() branch
    # and the raw entry_points() branch. Keep the entry with a non-None
    # entry_point_name when duplicates share (line, kind, group).
    dedup: dict[tuple, dict] = {}
    for s in sites:
        key = (s["line"], s["kind"], s.get("entry_point_group"), s.get("entry_point_name"), s.get("raw_expr"))
        if key not in dedup:
            dedup[key] = s
    sites = list(dedup.values())

    # Deterministic ordering: by source line, then raw_expr.
    sites.sort(key=lambda s: (s.get("line") or 0, s.get("raw_expr") or ""))
    return sites


def mine(entrypoint_path: str, import_roots: list[str] | None = None,
         extra_struct_schemas: dict | None = None,
         extra_read_helpers: set | None = None,
         extra_helper_schemas: dict | None = None,
         extra_write_helpers: set | None = None,
         extra_helper_readers: dict | None = None,
         detect_dynamic_imports: bool = False) -> dict:
    src = _read_source(entrypoint_path)
    tree = ast.parse(src)
    mod_files = _resolve_imports(entrypoint_path, import_roots)

    # --- Layer A (this file + imported local modules) ---
    const_ints = _collect_const_ints(tree)
    sv = _StructVisitor(const_ints)
    sv.visit(tree)
    struct_schemas = dict(extra_struct_schemas or {})
    struct_schemas.update(sv.schemas)          # local defs win over pooled
    anon_structs = list(sv.anon)
    for mod in mod_files[1:]:
        try:
            mtree = ast.parse(_read_source(mod))
            mci = _collect_const_ints(mtree)
            msv = _StructVisitor({**const_ints, **mci})
            msv.visit(mtree)
            for k, v in msv.schemas.items():
                struct_schemas.setdefault(k, v)
        except Exception:
            pass

    # --- read-helper discovery (this file + imported modules + pooled) ---
    local_helpers, local_helper_schemas, local_helper_readers = _find_read_helpers(mod_files)
    read_helpers = set(extra_read_helpers or set()) | local_helpers
    helper_schemas = {**(extra_helper_schemas or {}), **local_helper_schemas}
    helper_readers = {**(extra_helper_readers or {}), **local_helper_readers}
    write_helpers = set(extra_write_helpers or set()) | _find_write_helpers(mod_files)

    # --- Layer C + read binding ---
    dm = _DataFrameMiner(struct_schemas, read_helpers, helper_schemas, write_helpers,
                         helper_readers)
    dm.column_helpers = _find_column_helpers(mod_files)
    dm._tbl_write_vars = _scan_tbl_write_vars(tree)
    dm.visit(tree)

    # --- Layer B ---
    sql_tables = _sql_lineage(_extract_sql_strings(tree))
    # filter-literal domains mined from SQL `IN`/`=` predicates (by column name);
    # split off the sentinel so the table iterations below stay clean.
    sql_col_values = sql_tables.pop("__col_values__", {})

    # union of all mined filter-literal domains (DataFrame isin/== + SQL IN/=),
    # keyed by column name. Attached as a column's ``values`` so a filtered mock
    # keeps rows instead of collapsing to empty (the silent empty-join failure).
    filter_values: dict[str, list] = {}
    for cn, vals in dm.col_values.items():
        filter_values[cn] = sorted(vals, key=lambda v: (str(type(v)), str(v)))
    for cn, vals in sql_col_values.items():
        merged = set(filter_values.get(cn, [])) | set(vals)
        filter_values[cn] = sorted(merged, key=lambda v: (str(type(v)), str(v)))

    def _enrich(col: dict) -> dict:
        """Attach the mined ``values`` filter domain to a column dict so datagen
        seeds data that survives the workload's filter. (Join overlap is handled
        separately via the entrypoint-level ``joins`` edge list, not per column.)"""
        nm = col.get("name")
        if nm in filter_values and not col.get("values"):
            col["values"] = list(filter_values[nm])
        return col

    # --- Merge into per-source contracts ---
    sources: dict[str, dict] = {}
    used_struct_vars = set()
    # map a temp-view name -> the source name backing it (view = df.createOrReplaceTempView)
    sid_to_name = {sid: (meta.get("name") or sid) for sid, meta in dm.src_meta.items()}
    view_to_src = {view: sid_to_name[sid] for view, sid in dm.tempviews.items()
                   if sid in sid_to_name}

    # strong owners of each column (qualified `df.col` refs / join keys). Used to
    # remove a WEAKLY-attributed column (a bare post-join select string) from a
    # source when another source owns it with strong evidence.
    strong_owners: dict[str, set] = {}
    for s2, cset in dm.strong_cols.items():
        for c in cset:
            strong_owners.setdefault(c, set()).add(s2)

    for sid, meta in dm.src_meta.items():
        name = meta.get("name") or sid
        # strong-evidence cols (qualified `df.col` refs, join keys) are real inputs
        # even when the same NAME is produced as a renamed output elsewhere
        # (e.g. `withColumnRenamed("src","res_ent_id")` must not delete the genuine
        # `res_ent_id` join key from a source that joins on it).
        strong = dm.strong_cols.get(sid, set())
        raw = (dm.src_cols.get(sid, set()) - dm.outputs) | strong
        # drop a weakly-attributed column if another source owns it with strong
        # evidence and this source does NOT (corrects post-join projections like
        # `a.join(b,..).select("b_col")` that weakly tag b_col onto a).
        cols = sorted(c for c in raw
                      if c in strong
                      or not (strong_owners.get(c, set()) - {sid}))
        prov = ["ast_dataframe"]
        col_objs = []

        # bind explicit StructType via .schema(VAR)
        schema_var = meta.get("schema_var")
        if schema_var and schema_var in struct_schemas:
            used_struct_vars.add(schema_var)
            prov.insert(0, "structtype")
            for f in struct_schemas[schema_var]:
                col_objs.append(_enrich({**f, "origin": "structtype"}))
        else:
            for c in cols:
                t = dm.casts.get(c) or dm.agg_types.get(c) or dm.struct_out_types.get(c) or "string"
                origin = "cast" if c in dm.casts else ("agg" if c in dm.agg_types else "reference")
                col_objs.append(_enrich({"name": c, "type": t, "nullable": True, "origin": origin}))

        # enrich from SQL lineage: columns of any temp view backed by this source
        # (e.g. df.createOrReplaceTempView("x_v") then spark.sql("... FROM x_v"))
        # plus a direct name match.
        sql_cols = []
        for view, info in sql_tables.items():
            if view == name or view_to_src.get(view) == name:
                sql_cols += info["columns"]
        if sql_cols:
            prov.append("sql_lineage")
            have = {c["name"] for c in col_objs}
            for c in sql_cols:
                if c not in have and c.lower() not in _TYPE_KEYWORDS:
                    col_objs.append(_enrich({"name": c, "type": "string", "nullable": True, "origin": "sql"}))
                    have.add(c)

        conf = "high" if "structtype" in prov else ("medium" if "sql_lineage" in prov else "low")
        origin = meta.get("name_origin")
        name_conf = ("certain" if origin in _DataFrameMiner._CERTAIN_ORIGINS
                     else ("heuristic" if meta.get("name") else "unresolved"))
        col_complete = ("exact" if "structtype" in prov
                        else ("open" if sid in dm.open_ended_sids else "closed"))
        sources[name] = {
            "reader_method": meta.get("method"),
            "format": meta.get("format"),
            "defined_at": _loc(entrypoint_path, meta.get("lineno")),
            "reader_options": meta.get("reader_options") or {},
            "in_def": meta.get("in_def", False),
            "provenance": prov,
            "confidence": conf,
            "name_origin": origin,
            "name_confidence": name_conf,
            "column_completeness": col_complete,
            "columns": col_objs,
        }
        # parameterized / looping read: ONE logical source (a fan-in of N tables
        # that almost always share a schema), NOT N materialized tables. Flag it
        # so datagen seeds a single mock and the patch redirects every iteration
        # to it; the LLM confirms the shared schema (or splits only if the tables
        # are distinct schemas that get joined).
        if meta.get("dynamic_read"):
            fan = meta.get("fanout")
            sources[name]["dynamic_read"] = True
            sources[name]["column_completeness"] = "open"
            if fan:
                sources[name]["fanout"] = {"count": len(fan), "values": fan}
                detail = ("reads %d tables %s" % (len(fan), fan[:8]))
            else:
                sources[name]["fanout"] = {"count": None, "values": None}
                detail = "reads a runtime-determined set of tables (path built from a variable)"
            if fan and len(fan) >= 2:
                # Multi-element constant fan-out: a single collapsed entry is wrong
                # when these tables have distinct schemas that get joined. Lead with
                # the split-review prompt so the LLM doesn't silently keep one entry.
                sources[name]["llm_reason"] = (
                    "PARAMETERIZED/LOOPING read: this one read call %s. REVIEW WHETHER "
                    "TO SPLIT: if these tables have DISTINCT schemas that are joined "
                    "together, split into one source per fanout value (%s) and "
                    "reconstruct each table's own columns from the source -- the "
                    "collapsed single entry has no per-table columns. Keep as ONE "
                    "source ONLY in the fan-in/union case where they share a schema "
                    "(datagen seeds a single mock and the patch redirects every "
                    "iteration to it); then fill the shared column schema." % (detail, fan[:8]))
            else:
                sources[name]["llm_reason"] = (
                    "PARAMETERIZED/LOOPING read: this one read call %s. Keep it as ONE "
                    "source if they share a schema (the common fan-in/union case) -- datagen "
                    "seeds a single mock and the patch redirects every iteration to it. Fill "
                    "the shared column schema. ONLY split into one source per table if they "
                    "are DISTINCT schemas that get joined together." % detail)
            sources[name]["_dyn_reason"] = sources[name]["llm_reason"]

    # temp-view-only sources discovered purely from SQL (skip views already folded
    # into their backing source above)
    for tname, info in sql_tables.items():
        if tname in sources or tname == "_ph_" or tname in view_to_src:
            continue
        # only surface if it's not just an alias of a known source
        sources[tname] = {
            "reader_method": "table",
            "format": "table",
            "provenance": ["sql_lineage"],
            "confidence": "medium",
            "name_origin": "table_ref",
            "name_confidence": "certain",
            "column_completeness": "closed",
            "columns": [_enrich({"name": c, "type": "string", "nullable": True, "origin": "sql"})
                        for c in info["columns"] if c.lower() not in _TYPE_KEYWORDS],
        }

    # standalone StructTypes never bound to a read (likely sinks or unresolved reads)
    unbound_structs = {k: v for k, v in struct_schemas.items() if k not in used_struct_vars}

    # build sinks dict (target name -> column objs) for cross-file intermediate resolution
    sinks = {}
    for tname, meta in dm.sinks.items():
        cols, kind = meta["cols"], meta.get("kind", "table")
        col_objs = [_enrich({"name": c,
                     "type": dm.casts.get(c) or dm.agg_types.get(c) or dm.struct_out_types.get(c) or "string",
                     "nullable": True, "origin": "sink"}) for c in sorted(cols)]
        rec = {"kind": kind, "defined_at": _loc(entrypoint_path, meta.get("lineno")),
               "in_def": meta.get("in_def", False),
               "columns": col_objs}
        if meta.get("name_unresolved"):
            rec["llm_todo"] = ("sink target path is a runtime variable; '%s' is a "
                               "placeholder derived from the written DataFrame -- "
                               "confirm the real target name/path." % tname)
        sinks[tname] = rec

    # embedded-SQL validation (Layer D): replay spark.sql() bodies against the
    # mined catalog; an unresolved column means our schema missed something.
    dynamic_cols = bool(re.search(r"for\s+\w+\s+in\s+[\w.]+\.columns\b", src))
    validation = _validate_with_sqlframe(sources, _extract_sql_strings(tree))
    val_clean = validation.get("status") == "ran" and validation.get("queries_failed", 1) == 0
    for name, s in sources.items():
        vc = val_clean and ("ast_dataframe" in s.get("provenance", [])
                            or "sql_lineage" in s.get("provenance", []))
        s.update(_classify_source(name, s, dynamic_cols, vc))
    # _classify_source may rewrite llm_reason/completeness; the parameterized-read
    # guidance is more specific, so restore it for dynamic_read sources.
    for name, s in sources.items():
        if s.get("dynamic_read") and s.get("_dyn_reason"):
            s["llm_reason"] = s.pop("_dyn_reason")
            s["column_completeness"] = "open"

    # non-relational reads (json.load / yaml / open(config)) become sources too,
    # flagged relational:False so the LLM knows which inputs it must supply.
    for nm, rec in _find_nonrelational_reads([entrypoint_path]).items():
        sources.setdefault(nm, rec)

    # join edges -> "<table>.<col>" references (datagen pools the linked columns
    # so their mocks overlap). Resolve sids to source names; drop unresolved/self.
    joins: list[dict] = []
    seen_edges: set = set()
    for (lsid, lcol), (rsid, rcol) in dm.join_edges:
        lname, rname = sid_to_name.get(lsid), sid_to_name.get(rsid)
        if not lname or not rname:
            continue
        left, right = "%s.%s" % (lname, lcol), "%s.%s" % (rname, rcol)
        if left == right:
            continue
        key = tuple(sorted((left, right)))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        joins.append({"left": left, "right": right})

    # range-join edges (V1) -> "<table>.<col>" refs, parallel to `joins`. Each edge
    # links a point column bounded between a low and high column so datagen can
    # generate the three columns jointly (>=1 row per group inside the range).
    range_joins: list[dict] = []
    seen_rj: set = set()
    for (psid, pcol), (lsid, lcol), (hsid, hcol) in dm.range_join_edges:
        pn, ln, hn = sid_to_name.get(psid), sid_to_name.get(lsid), sid_to_name.get(hsid)
        if not (pn and ln and hn):
            continue
        edge = {"point": "%s.%s" % (pn, pcol),
                "low": "%s.%s" % (ln, lcol),
                "high": "%s.%s" % (hn, hcol)}
        rk = (edge["point"], edge["low"], edge["high"])
        if rk in seen_rj:
            continue
        seen_rj.add(rk)
        range_joins.append(edge)

    # Fix 2: warn about join edges whose column looks like it may be an alias
    # (appears in the outputs set, which includes withColumn/alias/withColumnRenamed
    # outputs AND selectExpr "col AS alias" patterns). The caller should verify
    # the physical source column manually.
    _alias_cols = dm.outputs
    for _edge in joins:
        _lcol = _edge["left"].rpartition(".")[-1]
        _rcol = _edge["right"].rpartition(".")[-1]
        if _lcol in _alias_cols or _rcol in _alias_cols:
            print(
                "[schema_mine] WARN: join edge %s \u2194 %s may reference an alias, "
                "verify manually" % (_edge["left"], _edge["right"]),
                file=sys.stderr,
            )

    # Display-only synthesis: if zero write sinks AND display sites exist,
    # synthesize display_<n> sinks so the entrypoint can produce a baseline.
    display_only = False
    display_sinks_prov: list[dict] = []
    has_write_sinks = bool(sinks)
    if not has_write_sinks and dm.display_sites:
        display_only = True
        for i, ds in enumerate(dm.display_sites):
            sink_id = f"display_{i}"
            cols = set()
            bv = ds.get("base_var")
            if bv and bv in dm.var_cols:
                cols = set(dm.var_cols[bv])
            col_objs = [_enrich({"name": c, "type": "string", "nullable": True,
                                 "origin": "display"}) for c in sorted(cols)]
            sinks[sink_id] = {"kind": "file",
                              "defined_at": _loc(entrypoint_path, ds.get("line")),
                              "in_def": False, "columns": col_objs}
            display_sinks_prov.append({
                "id": sink_id,
                "file": entrypoint_path,
                "line": ds.get("line"),
                "arg_src": ds.get("arg_src", ""),
            })

    contract = {
        "entrypoint": entrypoint_path,
        "_sources": sources,
        "_sinks": sinks,
        "_joins": joins,
        "_range_joins": range_joins,
        "unbound_structtypes": unbound_structs,
        "anonymous_structtypes": anon_structs,
        "validation": validation,
        "_sql_fragment_cols": sorted(dm.sql_fragment_cols),
    }
    if display_only:
        contract["display_only"] = True
        contract["display_sinks"] = display_sinks_prov
    if detect_dynamic_imports:
        # Purely additive: assessment scanners consume this to discover
        # customer-specific config-key names driving importlib chains
        # (readerModule/writerModule/etc.). Validation code paths never set
        # the flag so the contract shape they see is byte-identical to before.
        contract["_dynamic_imports"] = _find_dynamic_import_sites(
            tree, _collect_assignments_for_dynamic_imports(tree)
        )
    return contract




# ---------------------------------------------------------------------------
# Layer D: sqlframe validation (replay SQL vs the mined catalog)
# ---------------------------------------------------------------------------

def _validate_with_sqlframe(sources: dict, sql_bodies: list[str]) -> dict:
    if not sql_bodies:
        return {"status": "skipped", "reason": "no embedded SQL"}
    try:
        from sqlframe.standalone import StandaloneSession
    except ImportError:
        return {"status": "skipped", "reason": "sqlframe not installed"}
    spark = StandaloneSession.builder.getOrCreate()
    for name, s in sources.items():
        cols = {c["name"]: _sf_type(c["type"]) for c in s["columns"]}
        if cols:
            try:
                spark.catalog.add_table(name, cols)
            except Exception:
                pass
    missing, ok = [], 0
    for body in sql_bodies:
        try:
            df = spark.sql(body)
            _ = df.columns
            ok += 1
        except Exception as e:
            msg = str(e)
            m = re.search(r"[Uu]nknown column:?\s*([\w.]+)", msg) or \
                re.search(r"[Cc]olumn '([\w.]+)' could not be resolved", msg)
            missing.append({"column": m.group(1) if m else None, "error": msg[:160]})
    return {"status": "ran", "queries_ok": ok, "queries_failed": len(missing),
            "missing_columns": missing}


# ---------------------------------------------------------------------------
# Non-relational / "aux" file detection (config & document blobs, NOT Spark IO)
# ---------------------------------------------------------------------------

# config/document extensions whose shape is NOT a relational schema; tabular
# formats (csv/parquet/orc/avro/json-lines via Spark) are handled as sources.
_DOC_EXTS = {".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
             ".ini": "ini", ".cfg": "ini", ".conf": "conf", ".xml": "xml",
             ".properties": "properties", ".txt": "text"}
_AUX_WRITE_MODES = ("w", "a", "x")


def _find_nonrelational_reads(files: list[str]) -> dict:
    """Detect NON-relational reads that happen OUTSIDE Spark -- config & document
    blobs: ``json.load`` / ``yaml.safe_load`` / ``toml(llib).load``, or ``open()``
    of a config-ish extension in read mode. These are returned as SOURCE records
    flagged ``relational: False`` (no tabular schema exists), so the analysis lists
    them alongside relational sources and the LLM knows exactly which inputs it must
    supply a schema for / generate the file for. Deterministic detection.
    """
    def _fname(fn):
        if isinstance(fn, ast.Attribute):
            return fn.attr
        if isinstance(fn, ast.Name):
            return fn.id
        return None

    def _qual(fn):
        parts, n = [], fn
        while isinstance(n, ast.Attribute):
            parts.append(n.attr); n = n.value
        if isinstance(n, ast.Name):
            parts.append(n.id)
        return ".".join(reversed(parts))

    def _path_of(n, binds):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
        if isinstance(n, ast.Name):
            return binds.get(n.id)                       # var bound to open(<path>)
        if isinstance(n, ast.Call):
            f = _fname(n.func)
            if f == "open" and n.args:
                return _path_of(n.args[0], binds)
            if isinstance(n.func, ast.Attribute) and f in ("read", "readlines"):
                return _path_of(n.func.value, binds)     # unwrap open(<path>).read()
        return None

    def _bindings(tree):
        """Map simple `v = open(p)` / `with open(p) as v` to their path literal."""
        b: dict = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Call) and _fname(node.value.func) == "open" \
                    and node.value.args:
                p = _path_of(node.value.args[0], b)
                if p:
                    b[node.targets[0].id] = p
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for it in node.items:
                    if isinstance(it.optional_vars, ast.Name) \
                            and isinstance(it.context_expr, ast.Call) \
                            and _fname(it.context_expr.func) == "open" and it.context_expr.args:
                        p = _path_of(it.context_expr.args[0], b)
                        if p:
                            b[it.optional_vars.id] = p
        return b

    found: dict = {}
    for f in files:
        try:
            tree = ast.parse(_read_source(f))
        except Exception:
            continue
        binds = _bindings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn, qual = _fname(node.func), _qual(node.func)
            fmt = path = None
            # NOTE: ``loads`` (json.loads / yaml...) parses an in-memory STRING, not
            # a file -- it is NOT an input source (a common false positive is
            # json.loads on a DB column value). Only the file-reading ``load``
            # family counts, and only when a real file path resolves.
            if fn in ("load", "safe_load", "full_load"):
                if "json" in qual:
                    fmt = "json"
                elif "yaml" in qual:
                    fmt = "yaml"
                elif "toml" in qual:
                    fmt = "toml"
                if fmt:
                    path = _path_of(node.args[0], binds)
                    if path is None:
                        fmt = None  # not a resolvable file read -> don't invent a source
            elif fn == "open":
                mode = ""
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                if any(w in mode for w in _AUX_WRITE_MODES):
                    continue
                p = _path_of(node.args[0], binds)
                if p:
                    ext = os.path.splitext(p)[1].lower()
                    if ext in _DOC_EXTS:
                        fmt, path = _DOC_EXTS[ext], p
            if fmt:
                key = (path, fmt)
                found.setdefault(key, {
                    "path": path, "format": fmt, "access": "read",
                    "llm_reason": ("non-relational %s file read outside Spark; LLM should "
                                   "generate the file or a usable schema (content/shape is "
                                   "not statically inferable)." % fmt)})
    # drop redundant unnamed (None-path) entries when a concrete path of the same
    # format was already captured -- it's the same physical read seen two ways.
    known_fmts = {fmt for (p, fmt) in found if p}
    entries = [v for (p, fmt), v in found.items() if p or fmt not in known_fmts]

    # convert to SOURCE records keyed by a dot-free name (dots would break the
    # db.schema.table canonicalization used elsewhere).
    out: dict = {}
    for e in entries:
        p, fmt = e["path"], e["format"]
        base = (os.path.splitext(os.path.basename(p))[0] if p else "") or ("%s_document" % fmt)
        name, i = base, 2
        while name in out:
            name = "%s_%d" % (base, i); i += 1
        out[name] = {
            "relational": False,
            "format": fmt,
            "reader_method": None,
            "path": p,
            "columns": [],
            "document_schema": None,
            "llm_reason": ("non-relational %s input (config/document, not tabular); fill "
                           "document_schema with the shape (nested type string or dict) and "
                           "datagen will generate the file." % fmt),
        }
    return out


def _sf_type(t: str) -> str:
    base = t.split("<")[0].split("(")[0].lower()
    return {"long": "bigint", "int": "int", "double": "double", "float": "float",
            "boolean": "boolean", "timestamp": "timestamp", "date": "date",
            "string": "string", "decimal": "decimal"}.get(base, "string")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Deterministic workload synthesizer: detects Spark entrypoints, and "
                    "for each mines its sources + sinks with schema info. Non-Spark "
                    "files are skipped. Output is keyed by entrypoint.")
    p.add_argument("workload", help="workload / source directory")
    p.add_argument("--entrypoints", nargs="*", default=None,
                   help="pin specific entrypoint paths (abs or rel to workload); "
                        "auto-detected when omitted")
    p.add_argument("--json", action="store_true", help="emit the full analysis JSON to stdout")
    p.add_argument("--out", metavar="SCHEMAS_DIR",
                   help="write split schemas layout (manifest.json + entrypoints/)")
    args = p.parse_args()

    result = synthesize(args.workload, args.entrypoints)
    if args.out:
        from datagen import write_schemas_dir
        write_schemas_dir(args.out, result)
        s = result["summary"]
        print("wrote schemas to %s (%d entrypoints, %d open TODOs)"
              % (args.out, s["n_entrypoints"], s.get("open_todos", 0)))
        return
    if args.json:
        print(json.dumps(result, indent=2, default=list))
        return

    s = result["summary"]
    print("workload: %s" % result["root"])
    print("summary : %d entrypoints, %d tables (%d non-relational), %d open TODOs%s\n"
          % (s["n_entrypoints"], s.get("n_tables", 0), s.get("n_non_relational", 0),
             s.get("open_todos", 0), "  [COMPLETE]" if result.get("complete") else ""))
    for ep in result["entrypoints"]:
        if "error" in ep:
            print("\u25a0 %s  -- ERROR: %s" % (ep["path"], ep["error"])); continue
        print("\u25a0 %s" % ep["path"])
        for name, tbl in ep.get("tables", {}).items():
            todo = tbl.get("llm_todo")
            access = tbl.get("access", "read")
            if not tbl.get("relational", True):
                print("    %-5s %-24s [non-relational %s]  TODO" %
                      (access, name, tbl.get("format", "?")))
                print("           \u21b3 %s" % todo)
                continue
            cols = ", ".join(c["name"] for c in tbl.get("columns", [])[:8])
            more = "  (+%d)" % (len(tbl.get("columns", [])) - 8) if len(tbl.get("columns", [])) > 8 else ""
            print("    %-5s %-24s %s%s%s" %
                  (access, name, cols, more, "  TODO" if todo else ""))
            if todo:
                print("           \u21b3 %s" % todo)


if __name__ == "__main__":
    main()
