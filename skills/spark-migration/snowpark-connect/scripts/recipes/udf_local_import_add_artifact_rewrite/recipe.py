"""Inject ``spark.addArtifact("<local_module>.py", pyfile=True)`` for UDFs that
import a *local sibling module* (the Snowpark Submit "Option 1" workaround).

What it does
------------

In Spark Classic a UDF can freely import another script that lives next to the
workload on the local filesystem::

    # src/library.py defines example_function
    @udf(returnType=StringType())
    def example_udf(x: int) -> str:
        from library import example_function   # <- local sibling module
        return example_function(x)

    spark.range(1).select(example_udf("id")).show()

In Spark Connect / Snowpark Connect this fails: the server-side worker executes
the UDF closure and never sees ``library.py``, so the import raises an
unresolved-module error. The supported workaround (Snowpark Submit design doc,
"Option 1: keep existing workaround") is the Spark Connect ``addArtifact`` API,
which Snowpark Connect supports::

    spark = SparkSession.builder.getOrCreate()
    spark.addArtifact("library.py", pyfile=True)   # <- injected by this recipe

    @udf(returnType=StringType())
    def example_udf(x: int) -> str:
        from library import example_function
        return example_function(x)

The recipe scans every UDF closure for imports of a local module and, once per
distinct module, injects a single ``<session>.addArtifact("<path>.py",
pyfile=True)`` call immediately after the ``SparkSession`` is created. The
artifact path is resolved against the directory of the workload file being
migrated, so a UDF in ``src/main.py`` importing ``library`` yields
``spark.addArtifact("src/library.py", pyfile=True)``.

Trigger
-------

A ``FunctionDef`` that is a UDF -- either decorated with ``@udf`` /
``@pandas_udf`` (optionally dotted, e.g. ``@F.udf``) or referenced by name as
the first argument of a ``udf(...)`` / ``pandas_udf(...)`` factory call or a
server-side apply method (``applyInPandas`` / ``mapInPandas`` /
``mapPartitions`` / ``foreach`` / ``foreachPartition``) -- that pulls in a
*local* module either by

  (a) an ``import`` / ``from ... import`` statement **inside** the UDF body, or
  (b) a **closed-over reference** to a name that a *module-level* ``from <local>
      import name`` / ``import <local>`` brought into scope.

Case (b) is the dominant real-world shape: the workload imports its helper
classes once at the top of the file and the UDF closure simply references them
(``CqsQualityScorer.score(...)``). cloudpickle serializes that reference as a
``cqs_engine`` module import, which the Snowpark Connect server cannot resolve.

A module is treated as local when its top-level segment is neither in the
Python standard library (``sys.stdlib_module_names``) nor in a curated set of
common third-party packages. Third-party packages that are genuinely missing
server-side are a *different* problem (package availability) handled elsewhere.

Limitation: ``addArtifact`` ships a single ``.py`` file, so a referenced module
that itself imports *other* local sibling modules (a package) is only partially
covered -- the directly referenced modules are added, but their own transitive
local imports are not. Those cases still need UDF isolation (inlining).

Behaviour
---------

* SparkSession found at module top level -> inject
  ``<var>.addArtifact("<module/path>.py", pyfile=True)`` right after the
  session-creation statement, one line per distinct local module.
* SparkSession **not** found at module top level -> fall back to prepending a
  ``# SCOS-TODO`` comment on each affected UDF describing the exact call to add
  (never inject a call against an unknown session variable).

Negative cases (must NOT trigger)
---------------------------------

* Stdlib imports inside a UDF (``import json``, ``from datetime import date``).
* Known third-party imports (``import numpy as np``, ``from pandas import ...``).
* Top-level local imports that are **not** referenced by any UDF (a top-level
  import used only by driver-side code needs no artifact).
* A name shadowed by a UDF parameter or a local binding of the same spelling
  (the closed-over check excludes locally bound names).
* A module that already has a matching ``addArtifact(...)`` call (idempotency).

Idempotency
-----------

Re-running is a no-op: an injected ``addArtifact("library.py", ...)`` is
detected as already present, and the fallback ``# SCOS-TODO`` comment is not
duplicated.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import _recipe_base  # noqa: E402
import libcst as cst  # noqa: E402
import libcst.matchers as m  # noqa: E402

RECIPE_ID = "udf_local_import_add_artifact_rewrite"
MIN_SCOS_VERSION = "0.4.0"

# Decorators / factory callables that mark a function as a UDF closure.
_UDF_CALLABLES = frozenset({"udf", "pandas_udf"})
# DataFrame methods that ship a Python function to the server-side worker.
_UDF_APPLY_METHODS = frozenset(
    {"applyInPandas", "mapInPandas", "mapPartitions", "foreach", "foreachPartition"}
)
# Attribute names of the addArtifact API (alias pair).
_ADD_ARTIFACT_NAMES = frozenset({"addArtifact", "addArtifacts"})

# Common third-party top-level module names that are NOT local sibling files.
# ``sys.stdlib_module_names`` covers the standard library; this set covers the
# packages we routinely see inside PySpark UDFs so we never mistake them for a
# local script. Missing server-side packages are a separate concern (package
# availability), so being conservative here only means we skip an injection.
_KNOWN_THIRD_PARTY = frozenset(
    {
        "pyspark",
        "snowflake",
        "snowpark",
        "numpy",
        "pandas",
        "scipy",
        "sklearn",
        "scikit_learn",
        "torch",
        "tensorflow",
        "keras",
        "xgboost",
        "lightgbm",
        "statsmodels",
        "pyarrow",
        "matplotlib",
        "seaborn",
        "plotly",
        "requests",
        "urllib3",
        "boto3",
        "botocore",
        "s3fs",
        "fsspec",
        "google",
        "azure",
        "dateutil",
        "pytz",
        "tzlocal",
        "six",
        "cloudpickle",
        "dill",
        "joblib",
        "sqlalchemy",
        "psycopg2",
        "pymysql",
        "pydantic",
        "attr",
        "attrs",
        "yaml",
        "ujson",
        "orjson",
        "simplejson",
        "lxml",
        "bs4",
        "openpyxl",
        "xlrd",
        "pillow",
        "PIL",
        "cv2",
        "nltk",
        "spacy",
        "transformers",
        "delta",
        "pydeequ",
        "deequ",
        "setuptools",
        "pkg_resources",
        "typing_extensions",
        "dotenv",
        "click",
        "tqdm",
    }
)


# ---------------------------------------------------------------------------
# Dotted-name helpers
# ---------------------------------------------------------------------------


def _flatten_dotted(node: cst.BaseExpression) -> Optional[str]:
    """Return the dotted name of a ``Name``/``Attribute`` chain, else None."""
    parts: list[str] = []
    cur: cst.BaseExpression | None = node
    while isinstance(cur, cst.Attribute):
        if not isinstance(cur.attr, cst.Name):
            return None
        parts.append(cur.attr.value)
        cur = cur.value
    if isinstance(cur, cst.Name):
        parts.append(cur.value)
        return ".".join(reversed(parts))
    return None


def _is_local_module(top_segment: str) -> bool:
    """Heuristic: ``top_segment`` names a local sibling module (not stdlib and
    not a known third-party package)."""
    if not top_segment or top_segment.startswith("_"):
        return False
    if top_segment in sys.stdlib_module_names:
        return False
    if top_segment in _KNOWN_THIRD_PARTY:
        return False
    return True


def _module_to_artifact_path(dotted: str) -> str:
    """``pkg.mod`` -> ``pkg/mod.py``; ``library`` -> ``library.py``."""
    return dotted.replace(".", "/") + ".py"


def _resolve_artifact_path(workload_file: str, module_rel_path: str) -> str:
    """Resolve a module-relative path (``library.py``) against the directory of
    the workload file being migrated, so the injected ``addArtifact`` points at
    the real file location.

    A UDF's ``from library import ...`` resolves to a sibling module next to the
    workload script, so ``src/main.py`` -> ``src/library.py`` (and a dotted
    ``pkg.mod`` -> ``src/pkg/mod.py``). Manifest paths use forward slashes; we
    keep that convention. When the workload file has no directory component
    (or is the synthetic ``<input.py>`` placeholder), the bare module path is
    returned unchanged.

    Package-absolute case: when the workload file itself lives *inside* the
    imported package (its directory contains the module's top-level segment,
    e.g. file ``cqs_engine/scoring/x.py`` importing ``cqs_engine.scoring.y``),
    the dotted module path is already rooted at the import root, so it is
    anchored at that root rather than naively prefixed with the file's
    directory (which would duplicate the package prefix).
    """
    base = workload_file.rsplit("/", 1)[0] if "/" in workload_file else ""
    if not base or base.startswith("<"):
        return module_rel_path
    top_seg = module_rel_path.split("/", 1)[0]
    base_parts = base.split("/")
    if top_seg in base_parts:
        root = "/".join(base_parts[: base_parts.index(top_seg)])
        return f"{root}/{module_rel_path}" if root else module_rel_path
    return f"{base}/{module_rel_path}"


def _already_declared(
    resolved: str, module_rel: str, existing: set[str]
) -> bool:
    """True iff an ``addArtifact`` for this module is already declared, tolerant
    of path-form differences (a bare ``library.py`` and a dir-qualified
    ``src/library.py`` are treated as the same artifact)."""
    if resolved in existing or module_rel in existing:
        return True
    suffix = "/" + module_rel
    return any(
        e.endswith(suffix) or resolved.endswith("/" + e) for e in existing
    )


def _local_artifacts_from_import(stmt: cst.BaseSmallStatement) -> list[str]:
    """Return artifact paths (``<module>.py``) for the local modules referenced
    by a single ``import`` / ``from ... import`` statement (may be empty)."""
    artifacts: list[str] = []
    if isinstance(stmt, cst.Import):
        for alias in stmt.names:
            dotted = _flatten_dotted(alias.name)
            if dotted and _is_local_module(dotted.split(".")[0]):
                artifacts.append(_module_to_artifact_path(dotted))
    elif isinstance(stmt, cst.ImportFrom):
        if stmt.module is not None:
            dotted = _flatten_dotted(stmt.module)
            if dotted and _is_local_module(dotted.split(".")[0]):
                artifacts.append(_module_to_artifact_path(dotted))
        elif stmt.relative and not isinstance(stmt.names, cst.ImportStar):
            # ``from . import library`` -> each imported name is a local module.
            for alias in stmt.names:
                if isinstance(alias.name, cst.Name) and _is_local_module(
                    alias.name.value
                ):
                    artifacts.append(_module_to_artifact_path(alias.name.value))
    return artifacts


# ---------------------------------------------------------------------------
# Module-level local-import bindings + closed-over reference detection
# ---------------------------------------------------------------------------


def _asname(asname: Optional[cst.AsName]) -> Optional[str]:
    """Return the bound name of an ``as <name>`` clause, else None."""
    if asname is not None and isinstance(asname.name, cst.Name):
        return asname.name.value
    return None


def _local_bindings_from_import(
    stmt: cst.BaseSmallStatement,
) -> list[tuple[str, str]]:
    """For a single module-level ``import`` / ``from ... import`` statement,
    return ``[(bound_name, artifact_path), ...]`` for the *local* modules it
    binds into the module namespace (may be empty).

    The ``bound_name`` is the identifier a UDF would reference; the
    ``artifact_path`` is the ``.py`` file that must be shipped.
    """
    out: list[tuple[str, str]] = []
    if isinstance(stmt, cst.Import):
        for alias in stmt.names:
            dotted = _flatten_dotted(alias.name)
            if not dotted or not _is_local_module(dotted.split(".")[0]):
                continue
            artifact = _module_to_artifact_path(dotted)
            bound = _asname(alias.asname) or dotted.split(".")[0]
            out.append((bound, artifact))
    elif isinstance(stmt, cst.ImportFrom):
        if isinstance(stmt.names, cst.ImportStar):
            return out
        if stmt.module is not None:
            dotted = _flatten_dotted(stmt.module)
            if dotted and _is_local_module(dotted.split(".")[0]):
                artifact = _module_to_artifact_path(dotted)
                for alias in stmt.names:
                    bound = _asname(alias.asname) or (
                        alias.name.value
                        if isinstance(alias.name, cst.Name)
                        else None
                    )
                    if bound:
                        out.append((bound, artifact))
        elif stmt.relative:
            # ``from . import library`` -> each name is itself a local module.
            for alias in stmt.names:
                if isinstance(alias.name, cst.Name) and _is_local_module(
                    alias.name.value
                ):
                    artifact = _module_to_artifact_path(alias.name.value)
                    bound = _asname(alias.asname) or alias.name.value
                    out.append((bound, artifact))
    return out


def _target_names(node: cst.BaseExpression) -> set[str]:
    """Names bound by an assignment/loop/with/comprehension target. Attribute
    and subscript targets bind no new local name and are ignored."""
    names: set[str] = set()
    if isinstance(node, cst.Name):
        names.add(node.value)
    elif isinstance(node, (cst.Tuple, cst.List)):
        for el in node.elements:
            names |= _target_names(el.value)
    elif isinstance(node, cst.StarredElement):
        names |= _target_names(node.value)
    return names


def _import_bound_names(stmt: cst.BaseSmallStatement) -> set[str]:
    """Names an import statement binds into the local namespace."""
    names: set[str] = set()
    if isinstance(stmt, cst.Import):
        for alias in stmt.names:
            asn = _asname(alias.asname)
            if asn:
                names.add(asn)
            else:
                dotted = _flatten_dotted(alias.name)
                if dotted:
                    names.add(dotted.split(".")[0])
    elif isinstance(stmt, cst.ImportFrom):
        if isinstance(stmt.names, cst.ImportStar):
            return names
        for alias in stmt.names:
            asn = _asname(alias.asname)
            if asn:
                names.add(asn)
            elif isinstance(alias.name, cst.Name):
                names.add(alias.name.value)
    return names


def _locally_bound_names(func: cst.FunctionDef) -> set[str]:
    """Names that are *locally* bound inside ``func`` (parameters, assignment
    targets, loop/with/comprehension targets, nested def/class names, and
    in-body imports). A closed-over reference to a module-level import is only
    counted when the name is NOT in this set."""
    names: set[str] = set()

    params = func.params
    for plist in (params.posonly_params, params.params, params.kwonly_params):
        for param in plist:
            names.add(param.name.value)
    star_arg = params.star_arg
    if isinstance(star_arg, cst.Param):
        names.add(star_arg.name.value)
    if params.star_kwarg is not None:
        names.add(params.star_kwarg.name.value)

    for asgn in m.findall(func, m.Assign()):
        for tgt in asgn.targets:
            names |= _target_names(tgt.target)
    for ann in m.findall(func, m.AnnAssign()):
        names |= _target_names(ann.target)
    for aug in m.findall(func, m.AugAssign()):
        names |= _target_names(aug.target)
    for walrus in m.findall(func, m.NamedExpr()):
        names |= _target_names(walrus.target)
    for loop in m.findall(func, m.For()):
        names |= _target_names(loop.target)
    for item in m.findall(func, m.WithItem()):
        if item.asname is not None:
            names |= _target_names(item.asname.name)
    for comp in m.findall(func, m.CompFor()):
        names |= _target_names(comp.target)
    for nested in m.findall(func, m.FunctionDef() | m.ClassDef()):
        if nested is not func:
            names.add(nested.name.value)
    for imp in m.findall(func, m.Import() | m.ImportFrom()):
        names |= _import_bound_names(imp)

    return names


def _closed_over_artifacts(
    func: cst.FunctionDef, bindings: dict[str, tuple[str, int]]
) -> list[tuple[str, int]]:
    """Return ``[(artifact_path, src_line), ...]`` for module-level local
    imports that ``func`` references as closed-over free variables."""
    if not bindings:
        return []
    excluded = _locally_bound_names(func)
    found: dict[str, int] = {}
    for name_node in m.findall(func, m.Name()):
        nm = name_node.value
        if nm in bindings and nm not in excluded:
            artifact, line = bindings[nm]
            found.setdefault(artifact, line)
    return [(artifact, line) for artifact, line in found.items()]


# ---------------------------------------------------------------------------
# UDF / session detection
# ---------------------------------------------------------------------------


def _decorator_callee(dec: cst.Decorator) -> Optional[str]:
    """Return the bare callable name of a decorator (``udf`` for ``@udf`` and
    ``@udf(...)`` and ``@F.udf`` and ``@F.udf(...)``), else None."""
    expr = dec.decorator
    if isinstance(expr, cst.Call):
        expr = expr.func
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name):
        return expr.attr.value
    return None


def _is_decorated_udf(func: cst.FunctionDef) -> bool:
    return any(
        _decorator_callee(d) in _UDF_CALLABLES for d in func.decorators
    )


def _session_var_from_assign(stmt: cst.SimpleStatementLine) -> Optional[str]:
    """If ``stmt`` is a top-level assignment that creates a Spark/Snowflake
    session, return the assigned variable name, else None.

    Recognises any RHS whose call chain ends in ``.getOrCreate()`` or that
    references ``SparkSession`` / ``SnowflakeSession``.
    """
    for small in stmt.body:
        if not isinstance(small, cst.Assign):
            continue
        if not small.targets:
            continue
        target = small.targets[0].target
        if not isinstance(target, cst.Name):
            continue
        if _looks_like_session(small.value):
            return target.value
    return None


def _looks_like_session(value: cst.BaseExpression) -> bool:
    if m.findall(value, m.Call(func=m.Attribute(attr=m.Name("getOrCreate")))):
        return True
    # Snowpark Connect entry point: snowpark_connect.init_spark_session().
    if m.findall(value, m.Call(func=m.Attribute(attr=m.Name("init_spark_session")))):
        return True
    if m.findall(value, m.Call(func=m.Name("init_spark_session"))):
        return True
    for name in ("SparkSession", "SnowflakeSession"):
        if m.findall(value, m.Name(name)):
            return True
    return False


# ---------------------------------------------------------------------------
# Collector pass
# ---------------------------------------------------------------------------


class _Collector(cst.CSTVisitor):
    """Single pass that gathers everything ``apply`` needs to decide on edits."""

    METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

    def __init__(self) -> None:
        super().__init__()
        # func_name -> {"node": FunctionDef, "decorated": bool,
        #               "deps": [(artifact_path, src_line)]}
        self.func_defs: dict[str, dict] = {}
        # Names referenced as a function argument to a UDF factory / apply call.
        self.factory_udf_names: set[str] = set()
        # Artifact path strings already passed to an addArtifact(s) call.
        self.existing_artifacts: set[str] = set()
        # (var_name, top_level_stmt_node) for the last session created at top level.
        self.session_var: Optional[str] = None
        self.session_stmt: Optional[cst.SimpleStatementLine] = None
        # Module-level local-import bindings: bound_name -> (artifact, src_line).
        self.local_import_bindings: dict[str, tuple[str, int]] = {}
        self._module_depth = 0

    # -- session detection + module-level local imports (top level only) ----

    def visit_Module(self, node: cst.Module) -> None:
        for stmt in node.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            var = _session_var_from_assign(stmt)
            if var is not None:
                self.session_var = var
                self.session_stmt = stmt
            line = self.get_metadata(
                cst.metadata.PositionProvider, stmt
            ).start.line
            for small in stmt.body:
                for bound, artifact in _local_bindings_from_import(small):
                    self.local_import_bindings.setdefault(bound, (artifact, line))

    # -- existing addArtifact calls + factory/apply references --------------

    def visit_Call(self, node: cst.Call) -> None:
        func = node.func
        attr_name = func.attr.value if isinstance(func, cst.Attribute) and isinstance(
            func.attr, cst.Name
        ) else None
        bare_name = func.value if isinstance(func, cst.Name) else None

        # Record artifact paths already declared so we stay idempotent.
        if attr_name in _ADD_ARTIFACT_NAMES:
            for arg in node.args:
                if arg.keyword is None:
                    val = _string_value(arg.value)
                    if val is not None:
                        self.existing_artifacts.add(val)

        # Factory style: udf(fn, ...) / pandas_udf(fn, ...).
        callee = None
        if bare_name in _UDF_CALLABLES:
            callee = bare_name
        elif attr_name in _UDF_CALLABLES:
            callee = attr_name
        if callee is not None:
            self._record_first_func_arg(node)

        # Server-side apply methods: df.applyInPandas(fn, ...), etc.
        if attr_name in _UDF_APPLY_METHODS:
            self._record_first_func_arg(node)

    def _record_first_func_arg(self, call: cst.Call) -> None:
        for arg in call.args:
            if arg.keyword is None:
                if isinstance(arg.value, cst.Name):
                    self.factory_udf_names.add(arg.value.value)
                return  # only the first positional arg matters

    # -- function defs + their local-module imports -------------------------

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        deps: list[tuple[str, int]] = []
        for imp in m.findall(node, m.Import() | m.ImportFrom()):
            line = self.get_metadata(cst.metadata.PositionProvider, imp).start.line
            for artifact in _local_artifacts_from_import(imp):
                deps.append((artifact, line))
        self.func_defs[node.name.value] = {
            "node": node,
            "decorated": _is_decorated_udf(node),
            "deps": deps,
        }


def _string_value(node: cst.BaseExpression) -> Optional[str]:
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        try:
            return node.evaluated_value  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return None
    return None


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


def _add_artifact_stmt(
    session_var: str, artifact_path: str, *, leading_comment: Optional[str] = None
) -> cst.SimpleStatementLine:
    """Build ``<session_var>.addArtifact("<artifact_path>", pyfile=True)``."""
    leading = (
        (cst.EmptyLine(comment=cst.Comment(leading_comment)),)
        if leading_comment
        else ()
    )
    return cst.SimpleStatementLine(
        leading_lines=leading,
        body=[
            cst.Expr(
                value=cst.Call(
                    func=cst.Attribute(
                        value=cst.Name(session_var),
                        attr=cst.Name("addArtifact"),
                    ),
                    args=[
                        cst.Arg(value=cst.SimpleString(f'"{artifact_path}"')),
                        cst.Arg(
                            keyword=cst.Name("pyfile"),
                            value=cst.Name("True"),
                            equal=cst.AssignEqual(
                                whitespace_before=cst.SimpleWhitespace(""),
                                whitespace_after=cst.SimpleWhitespace(""),
                            ),
                        ),
                    ],
                )
            )
        ],
    )


class _CommentFallback(cst.CSTTransformer):
    """Prepend a ``# SCOS-TODO`` comment to each UDF that needs an artifact but
    has no top-level SparkSession to attach it to."""

    def __init__(self, targets: dict[int, list[str]]) -> None:
        super().__init__()
        # id(FunctionDef) -> [artifact_path, ...]
        self._targets = targets

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        artifacts = self._targets.get(id(original_node))
        if not artifacts:
            return updated_node
        new_leading = list(updated_node.leading_lines)
        for artifact in artifacts:
            comment = (
                f"# SCOS-TODO: [SPRKCNTPY5700-Error] add `spark.addArtifact(\"{artifact}\", pyfile=True)` "
                f"after the SparkSession is created so this UDF dependency loads "
                f"in Spark Connect"
            )
            new_leading.append(cst.EmptyLine(comment=cst.Comment(comment)))
        return updated_node.with_changes(leading_lines=tuple(new_leading))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    module = cst.parse_module(source)
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    collector = _Collector()
    wrapper.visit(collector)

    # Resolve which functions are UDFs and the local modules they pull in.
    # Preserve first-seen order, dedupe artifact paths, drop already-declared.
    ordered_artifacts: list[str] = []
    seen: set[str] = set()
    # id(FunctionDef) -> [artifact_path, ...] for the comment fallback path.
    udf_targets: dict[int, list[str]] = {}
    # artifact_path -> src_line of the import that first introduced it.
    artifact_line: dict[str, int] = {}

    for name, info in collector.func_defs.items():
        is_udf = info["decorated"] or name in collector.factory_udf_names
        if not is_udf:
            continue
        # (a) imports inside the UDF body + (b) closed-over references to
        # module-level local imports. Dedupe per (artifact, line).
        combined_deps: list[tuple[str, int]] = list(info["deps"])
        for dep in _closed_over_artifacts(
            info["node"], collector.local_import_bindings
        ):
            if dep not in combined_deps:
                combined_deps.append(dep)
        if not combined_deps:
            continue
        func_artifacts: list[str] = []
        for module_rel, line in combined_deps:
            artifact = _resolve_artifact_path(file, module_rel)
            if _already_declared(artifact, module_rel, collector.existing_artifacts):
                continue
            func_artifacts.append(artifact)
            artifact_line.setdefault(artifact, line)
            if artifact not in seen:
                seen.add(artifact)
                ordered_artifacts.append(artifact)
        if func_artifacts:
            udf_targets[id(info["node"])] = func_artifacts

    if not ordered_artifacts:
        return _common.RecipeResult(source=module.code, edits=[])

    edits: list = []

    def _record(artifact: str) -> None:
        line = artifact_line[artifact]
        anchor = _common.output_anchor(RECIPE_ID, line, f"addArtifact:{artifact}")
        edits.append(
            _recipe_base.record_edit(
                file=file,
                src_line=line,
                recipe_id=RECIPE_ID,
                output_line_anchor=anchor,
                facts_db=facts_db,
            )
        )

    if collector.session_var and collector.session_stmt is not None:
        body = list(module.body)
        insert_at = next(
            i for i, s in enumerate(body) if s is collector.session_stmt
        )
        new_stmts: list[cst.BaseStatement] = []
        for idx, artifact in enumerate(ordered_artifacts):
            comment = (
                "# SCOS: [SPRKCNTPY5700-Fixed] load local UDF dependencies for Spark Connect (addArtifact)"
                if idx == 0
                else None
            )
            new_stmts.append(
                _add_artifact_stmt(
                    collector.session_var, artifact, leading_comment=comment
                )
            )
            _record(artifact)
        new_body = body[: insert_at + 1] + new_stmts + body[insert_at + 1 :]
        new_module = module.with_changes(body=tuple(new_body))
        return _common.RecipeResult(source=new_module.code, edits=edits)

    # Fallback: no top-level session -> annotate each affected UDF.
    # Drop UDFs that already carry the SCOS-TODO comment (idempotency).
    pending: dict[int, list[str]] = {}
    for name, info in collector.func_defs.items():
        targets = udf_targets.get(id(info["node"]))
        if not targets:
            continue
        kept = [
            a
            for a in targets
            if not _func_already_annotated(info["node"], a)
        ]
        if kept:
            pending[id(info["node"])] = kept

    if not pending:
        return _common.RecipeResult(source=module.code, edits=[])

    new_module = module.visit(_CommentFallback(pending))
    for targets in pending.values():
        for artifact in targets:
            _record(artifact)
    return _common.RecipeResult(source=new_module.code, edits=edits)


def _func_already_annotated(func: cst.FunctionDef, artifact: str) -> bool:
    """True iff ``func``'s leading lines already carry a SCOS-TODO comment that
    references ``addArtifact("<artifact>"``."""
    marker = f'addArtifact("{artifact}"'
    for line in func.leading_lines:
        if line.comment is not None and marker in line.comment.value:
            return True
    return False
