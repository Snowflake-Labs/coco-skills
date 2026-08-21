"""Flavor-agnostic trial body: load entrypoint -> run under intercepts -> capture.

Every runtime (local, databricks-on-cluster, scos) calls
``run_and_capture(spark, request, ctx)`` after it has provisioned a session and
an output schema. This is the single place that knows how to run a workload
entrypoint and snapshot its end state into the skill baseline layout
(``tables/<name>.parquet`` + ``_index.json`` + ``_harness_status.json``).

Provisioning (session, schema, seeding, clone) and teardown live in the runtime
classes; comparison against a Phase A baseline lives in the caller.
"""

from __future__ import annotations

import builtins
import contextlib
import importlib.util
import inspect
import json
import os
import runpy
import sys
import time
import traceback
import types
from typing import Any, Dict, Optional

import notebook_source  # type: ignore[import-not-found]
from helpers import (  # type: ignore[import-not-found]
    capture_results,
    declared_allow_empty_sink_tables,
    intercept_session,
    validate_declared_sink_outputs,
)

from .base import TrialContext, TrialRequest, normalize_flavor


def load_entrypoint_source(path: str) -> str:
    """Return executable Python source for an entrypoint.

    Notebooks are translated via ``notebook_source`` (``%%sql``/``%sql`` →
    ``spark.sql()``, ``%run`` → ``_nb_run()``, magics neutralized): ``.ipynb``
    JSON and Databricks notebook-source ``.py`` files (``# MAGIC`` cells) both
    translate; a plain ``.py`` module is returned verbatim.
    """
    return notebook_source.source_to_python(path)


def _is_clean_exit(code: object) -> bool:
    """A ``SystemExit`` with code ``None`` or ``0`` is a clean completion.

    ``dbutils.notebook.exit(...)`` is patched to ``sys.exit(0)``; a bare
    ``exit()``/``sys.exit()`` (code ``None``) or ``sys.exit(0)`` means the
    workload finished early but successfully. Any other code is a real failure.

    This is applied uniformly to ``.py`` and ``.ipynb`` entrypoints by design: a
    ``.py`` workload that calls ``sys.exit(2)`` is a genuine failure and is now
    recorded as a failed trial (previously the ``SystemExit`` escaped
    ``run_and_capture`` and aborted the pytest worker instead). ``sys.exit(0)`` in
    a ``.py`` script is likewise treated as a clean finish.
    """
    return code in (None, 0)


def _make_nb_run(root: str):
    """Build a ``_nb_run(target, ns)`` closure bound to *root* (the workload root).

    Handles ``%run <target>`` / ``dbutils.notebook.run(...)`` by resolving the
    target notebook (``./x``, ``../x``, ``x``, with or without ``.ipynb``/``.py``)
    and exec'ing its translated source into the caller's namespace — Databricks
    ``%run`` copy-paste semantics (defs/vars land in the caller's namespace).
    """
    guard: set = set()  # recursion guard: (caller_file, target) pairs

    def _resolve(target: str, ns: dict) -> Optional[str]:
        target = target.strip().strip("\"'")
        candidates = [target]
        if not target.endswith((".ipynb", ".py")):
            candidates = [target + ".ipynb", target + ".py", target]
        search_dirs: list = []
        caller_file = ns.get("__file__")
        if caller_file:
            search_dirs.append(os.path.dirname(os.path.abspath(caller_file)))
        if root and os.path.isdir(root):
            search_dirs.append(root)
        search_dirs.extend(p for p in sys.path if os.path.isdir(p))
        for d in search_dirs:
            for cand in candidates:
                full = os.path.normpath(os.path.join(d, cand))
                if os.path.isfile(full):
                    return full
        return None

    def _nb_run(target: str, ns: dict) -> None:
        caller_file = ns.get("__file__", "")
        run_key = (caller_file, target)
        if run_key in guard:
            raise RuntimeError(
                f"_nb_run: circular dependency detected: {target!r} "
                f"(already executing from {caller_file!r})"
            )
        guard.add(run_key)
        try:
            resolved = _resolve(target, ns)
            if resolved is None:
                raise RuntimeError(
                    f"_nb_run: cannot resolve target {target!r} from "
                    f"{caller_file!r}. Searched relative dir, workload root, and "
                    f"sys.path entries."
                )
            src = load_entrypoint_source(resolved)
            code = compile(src, resolved, "exec")
            # Nested %run resolves relative to the sub-notebook's own directory
            # (Databricks semantics); restore the caller's __file__ afterward.
            ns.setdefault("_nb_run", _nb_run)
            prev_file = ns.get("__file__")
            ns["__file__"] = resolved
            try:
                exec(code, ns)
            except SystemExit as exc:
                # A `%run` child that calls dbutils.notebook.exit(...) (patched to
                # sys.exit(0)) should end only the CHILD and let the parent
                # continue — Databricks semantics. Swallow a clean exit here; a
                # non-zero exit is a real failure and propagates to the parent.
                if not _is_clean_exit(exc.code):
                    raise
            finally:
                if prev_file is not None:
                    ns["__file__"] = prev_file
                else:
                    ns.pop("__file__", None)
        finally:
            guard.discard(run_key)

    return _nb_run


def workload_root(project_root: str, flavor: str) -> str:
    """Where the adapted entrypoint module lives for this flavor.

    - local / databricks run the ORIGINAL source: ``<root>/Validation/source``
    - scos runs the MIGRATED output:               ``<root>/Output``
    """
    if normalize_flavor(flavor) == "scos":
        return os.path.join(project_root, "Output")
    return os.path.join(project_root, "Validation", "source")


def _patch_databricks_extensions():
    """Inject Databricks/Snowflake-specific symbols into pyspark.sql.functions/types.

    Databricks runtime provides parse_json (pyspark.sql.functions) and VariantType
    (pyspark.sql.types) which do not exist in open-source PySpark. The migrated
    code imports these at module scope. We provide stubs so the module loads;
    at SCOS runtime these code paths target Variant columns which don't appear
    in the mock CSV schemas, so the stubs are sufficient.
    """
    import pyspark.sql.functions as F
    import pyspark.sql.types as T

    if not hasattr(F, "parse_json"):
        def _parse_json(col_expr):
            """Stub: wraps col in a parse_json SQL expression via expr()."""
            from pyspark.sql.functions import expr, col as _col
            if isinstance(col_expr, str):
                return expr(f"PARSE_JSON({col_expr})")
            # Column object — use its internal name
            return expr(f"PARSE_JSON({col_expr._jc.toString()})")
        F.parse_json = _parse_json

    if not hasattr(T, "VariantType"):
        class _VariantType(T.DataType):
            """Stub for Databricks VariantType."""
            def simpleString(self):
                return "variant"
            def jsonValue(self):
                return "variant"
        T.VariantType = _VariantType


def _resolve_entrypoint_src(root: str, entrypoint_path: str) -> str:
    """Absolute path to the entrypoint on disk, resolving the notebook-migration
    fallback: a source ``.py`` (Databricks format) is migrated to ``.py.ipynb``,
    so fall back to ``<path>.ipynb`` when the plain ``.py`` doesn't exist."""
    src_path = os.path.join(root, entrypoint_path)
    if not os.path.isfile(src_path) and entrypoint_path.endswith(".py"):
        candidate = src_path + ".ipynb"
        if os.path.isfile(candidate):
            return candidate
    return src_path


_IS_NOTEBOOK_CACHE: Dict[tuple, bool] = {}


def _is_notebook_entrypoint(root: str, entrypoint_path: str) -> bool:
    """True when the entrypoint must be translated before execution: a Jupyter
    ``.ipynb`` or a Databricks notebook-source ``.py`` (``# MAGIC`` cells, detected
    by content). A plain ``.py`` module returns False (runs via importlib/runpy).
    Resolves the ``.py``→``.py.ipynb`` migration fallback first.

    Memoized per ``(src_path, mtime)`` so a dbx ``.py`` isn't re-read on each of
    the several call sites in the per-trial path (run_and_capture +
    _load_entrypoint_module)."""
    src_path = _resolve_entrypoint_src(root, entrypoint_path)
    if src_path.endswith(".ipynb"):
        return True
    if src_path.endswith(".py"):
        try:
            key = (src_path, os.path.getmtime(src_path))
        except OSError:
            return False
        cached = _IS_NOTEBOOK_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            with open(src_path, encoding="utf-8") as fh:
                result = notebook_source.is_dbx_notebook_py(fh.read())
        except OSError:
            return False
        _IS_NOTEBOOK_CACHE[key] = result
        return result
    return False


def _load_entrypoint_module(
    root: str,
    entrypoint_path: str,
    module_globals: Optional[Dict[str, Any]] = None,
):
    _patch_databricks_extensions()
    src_path = _resolve_entrypoint_src(root, entrypoint_path)
    module_name = entrypoint_path.replace("\\", "/")
    for _ext in (".ipynb", ".py"):
        if module_name.endswith(_ext):
            module_name = module_name[: -len(_ext)]
            break
    module_name = module_name.replace("/", ".")

    # A notebook entrypoint is .ipynb JSON or a Databricks notebook-source .py
    # (# MAGIC cells). Both are translated to Python and exec'd into a fresh
    # module — they are not importable as files. A plain .py module goes through
    # importlib.
    if _is_notebook_entrypoint(root, entrypoint_path):
        # Always script mode: top-level code IS the workload, so module globals +
        # _nb_run must be in the namespace BEFORE exec.
        source = load_entrypoint_source(src_path)
        mod = types.ModuleType(module_name)
        mod.__file__ = src_path
        mod.__name__ = "__main__"
        mod.__dict__["_nb_run"] = _make_nb_run(root)
        if module_globals:
            mod.__dict__.update(module_globals)
        sys.modules[module_name] = mod
        code = compile(source, src_path, "exec")
        exec(code, mod.__dict__)
        return mod

    spec = importlib.util.spec_from_file_location(module_name, src_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load entrypoint module from {src_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    # Plain .py: top-level ran on import; module globals are set afterward (only
    # relevant for callable mode, which reads them when the callable runs).
    if module_globals:
        for key, value in module_globals.items():
            setattr(mod, key, value)
    return mod


def _run_entrypoint_script(root: str, entrypoint_path: str, init_globals: Dict[str, Any]):
    _patch_databricks_extensions()
    src_path = os.path.join(root, entrypoint_path)
    return runpy.run_path(src_path, init_globals=init_globals, run_name="__main__")


def _resolve_callable(mod, dotted: str):
    obj = mod
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


_SESSION_PARAM_NAMES = {"spark", "session", "spark_session", "sparksession", "sc"}


def _accepts_session_first(func) -> bool:
    """Whether *func*'s first positional parameter is the Spark session.

    Entrypoints come in two shapes: ``run(spark, ...)`` expects the harness to
    inject the session, while ``run(source_table="orders", ...)`` builds its own
    (``intercept_session`` makes its ``getOrCreate()`` return ours). Passing
    ``spark`` positionally to the second shape silently binds the session to a
    business argument, and the failure surfaces far away as e.g.
    "Argument `tableName` should be str, got SparkSession".

    Unknown signatures (C callables, some builtins) fall back to True so existing
    ``run(spark)`` entrypoints keep working.
    """
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return True
    positional = [
        p for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    # An unbound method resolved as `Class.run` carries `self`/`cls` in slot 0;
    # judge the slot the caller would actually fill.
    if positional and positional[0].name in ("self", "cls"):
        positional = positional[1:]
    if not positional:
        # No positional slot at all; only *args could absorb it.
        return any(p.kind is p.VAR_POSITIONAL for p in params)
    first = positional[0]
    if first.name.lower() in _SESSION_PARAM_NAMES:
        return True
    # A required first parameter with a non-session name is ambiguous; keep the
    # historical behaviour of injecting. A default means the caller may omit it,
    # so it is a business argument, not an injected session.
    return first.default is inspect.Parameter.empty


@contextlib.contextmanager
def _temp_env(values: Dict[str, str]):
    saved = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = str(value)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_error_file(trial_dir: str, name: str, exc: BaseException, extra: str = "") -> None:
    os.makedirs(trial_dir, exist_ok=True)
    try:
        with open(os.path.join(trial_dir, name), "w", encoding="utf-8") as fh:
            fh.write(f"{exc}\n")
            if extra:
                fh.write(f"\n{extra}\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=fh)
    except Exception:
        pass


def run_and_capture(
    spark,
    request: TrialRequest,
    ctx: TrialContext,
) -> Dict[str, Any]:
    """Run one entrypoint and snapshot its outputs. Returns a manifest dict.

    The manifest carries ``tables``/``failures`` from ``capture_results`` plus
    ``ok`` and ``error`` keys. Never raises on a workload error — the in-process
    pytest wrapper decides whether to raise; the out-of-process CLI just logs.
    Always attempts capture so human-review artifacts exist even on failure.
    """
    flavor = normalize_flavor(request.flavor)
    trial_dir = os.path.join(request.results_dir, request.trial_id)
    os.makedirs(trial_dir, exist_ok=True)

    os.environ.setdefault("SCOS_TRIAL_START_TS", str(time.time()))

    root = workload_root(request.project_root, flavor)

    sink_table_names = {str(t).split(".")[-1].lower() for t in ctx.sink_tables}
    excluded_tables = [
        t for t in ctx.seed_tables
        if str(t).split(".")[-1].lower() not in sink_table_names
    ]

    run_kwargs = dict(request.kwargs)
    if request.kwargs_factory is not None:
        run_kwargs.update(request.kwargs_factory(ctx.output_schema) or {})

    # Inject spark into builtins BEFORE module load so scripts that reference a
    # global `spark` at import time (common Databricks pattern) resolve it.
    _had_spark = hasattr(builtins, "spark")
    _old_spark = getattr(builtins, "spark", None)
    builtins.spark = spark

    ep_kwargs_env = {k: str(v) for k, v in (request.ep_config.get("entrypoint_kwargs") or {}).items()}
    workload_error: Optional[BaseException] = None
    module_globals = None
    if request.module_globals_factory is not None:
        module_globals = request.module_globals_factory(ctx.output_schema) or {}
    with _temp_env({**request.extra_env, **ep_kwargs_env}), intercept_session(spark):
        try:
            if request.callable_name:
                mod = _load_entrypoint_module(root, request.entrypoint_path)
                if module_globals:
                    for key, value in module_globals.items():
                        setattr(mod, key, value)
                callable_obj = _resolve_callable(mod, request.callable_name)
                call_args = (spark,) if _accepts_session_first(callable_obj) else ()
                callable_obj(*call_args, **run_kwargs)
            elif _is_notebook_entrypoint(root, request.entrypoint_path):
                # Notebooks (.ipynb / dbx .py) are script mode but aren't valid
                # Python on disk, so they can't go through runpy. Translate + exec
                # via _load_entrypoint_module, injecting module globals BEFORE exec
                # (top-level code runs during load).
                _load_entrypoint_module(
                    root,
                    request.entrypoint_path,
                    module_globals=module_globals,
                )
            else:
                _run_entrypoint_script(
                    root,
                    request.entrypoint_path,
                    module_globals or {},
                )
        except SystemExit as exc:
            # dbutils.notebook.exit(...) is patched to sys.exit(0); a clean exit
            # (code None/0) is a successful early return, not a trial failure.
            if not _is_clean_exit(exc.code):
                workload_error = exc
        except BaseException as exc:  # noqa: BLE001 — capture, then surface to caller
            if isinstance(exc, KeyboardInterrupt):
                raise
            workload_error = exc

    # Restore builtins
    if _had_spark:
        builtins.spark = _old_spark
    else:
        if hasattr(builtins, "spark"):
            delattr(builtins, "spark")

    if ctx.pre_capture_hook is not None:
        try:
            ctx.pre_capture_hook()
        except Exception as hook_err:  # noqa: BLE001
            sys.stderr.write(f"warn: pre_capture_hook failed: {hook_err}\n")

    manifest: Optional[Dict[str, Any]] = None
    try:
        allow_empty_sink_tables = declared_allow_empty_sink_tables(
            request.ep_config, ctx.output_schema
        )
        manifest = capture_results(
            spark,
            ctx.output_schema,
            trial_dir,
            sink_capture_dir=ctx.sink_capture_dir,
            exclude=excluded_tables,
            exclude_if_empty=allow_empty_sink_tables,
        )
    except Exception as cap_err:  # noqa: BLE001
        sys.stderr.write(f"warn: capture_results failed: {cap_err}\n")
        extra = f"workload_error: {workload_error}" if workload_error else ""
        _write_error_file(trial_dir, "capture_error.txt", cap_err, extra)
        manifest = {"tables": [], "failures": [{"reason": str(cap_err)[:200]}]}

    if workload_error is not None:
        _write_error_file(trial_dir, "workload_error.txt", workload_error)

    if workload_error is None:
        manifest.setdefault("failures", []).extend(
            validate_declared_sink_outputs(request.ep_config, manifest)
        )

    ok = workload_error is None and not manifest.get("failures")
    manifest["ok"] = ok
    manifest["error"] = str(workload_error) if workload_error else None
    manifest["output_schema"] = ctx.output_schema

    # Re-persist _index.json with ok/error so readers of _index.json alone can
    # determine whether the baseline is valid (capture_results wrote it earlier
    # without a verdict).
    try:
        _idx = os.path.join(trial_dir, "_index.json")
        if os.path.isfile(_idx):
            with open(_idx, encoding="utf-8") as _f:
                _disk = json.load(_f)
            _disk["ok"] = ok
            _disk["error"] = manifest["error"]
            _disk["failures"] = manifest.get("failures", [])
            _tmp = _idx + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(_disk, _f, indent=2)
            os.replace(_tmp, _idx)
    except Exception:
        pass

    status_payload = {
        "trial_id": request.trial_id,
        "flavor": flavor,
        "ok": ok,
        "error": manifest["error"],
        "output_schema": ctx.output_schema,
        "tables": [t.get("name") for t in manifest.get("tables", [])],
        "artifacts": [a.get("name") for a in manifest.get("artifacts", [])],
        "failures": manifest.get("failures", []),
    }
    try:
        with open(os.path.join(trial_dir, "_harness_status.json"), "w", encoding="utf-8") as fh:
            json.dump(status_payload, fh, indent=2)
    except Exception:
        pass

    return manifest
