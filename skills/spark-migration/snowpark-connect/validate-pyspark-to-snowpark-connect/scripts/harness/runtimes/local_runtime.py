"""Local Delta runtime — Phase A baseline via in-process PySpark + Delta Lake."""

from __future__ import annotations

import functools
import os
import shutil
import socket
import tempfile
from pathlib import Path
from uuid import uuid4

from .base import TrialContext, TrialRequest, TrialResult, ValidationRuntime
from ._executor import run_and_capture

from helpers import (  # type: ignore[import-not-found]
    declared_sink_tables,
    file_io_env,
    install_delta_patches,
    seed_entrypoint,
    _io_id_from_name,
)


# NOTE: _resolve_delta_jars lets _build_local_session bypass
# configure_spark_with_delta_pip, which triggers a Maven/ivy download at JVM
# startup. Concurrent pytest-xdist workers race on the shared ~/.ivy2 cache,
# producing partial/zero-byte JARs and JAVA_GATEWAY_EXITED failures. When the
# JARs are already on disk we set spark.jars directly and skip the download.


@functools.lru_cache(maxsize=1)
def _resolve_delta_jars() -> "tuple[str, ...] | None":
    """Return absolute paths to local Delta JARs, or None if not found.

    Search order:
      1. The installed ``delta`` Python package's own ``jars/`` directory.
      2. The ivy2 cache tree (``~/.ivy2``), globbing for delta-spark, delta-storage,
         and antlr4-runtime JARs.

    Returns a non-empty list only when the main delta-spark JAR is present.
    """
    import glob as _glob
    from pathlib import Path as _Path

    # 1. delta package bundled jars (present in delta-spark >= 2.x pip installs)
    try:
        import delta as _delta  # type: ignore[import-not-found]
        pkg_jars_dir = _Path(_delta.__file__).parent / "jars"
        if pkg_jars_dir.is_dir():
            jars = [str(p) for p in pkg_jars_dir.glob("*.jar") if p.stat().st_size > 0]
            if any("delta-spark" in _Path(j).name or "delta_spark" in _Path(j).name for j in jars):
                return tuple(jars)
    except Exception:
        pass

    # 2. ivy2 cache / local repository
    ivy_base = _Path.home() / ".ivy2"
    patterns = [
        str(ivy_base / "**" / "delta-spark_*.jar"),
        str(ivy_base / "**" / "delta-storage-*.jar"),
        str(ivy_base / "**" / "antlr4-runtime-*.jar"),
    ]
    found: list[str] = []
    seen_stems: set[str] = set()
    for pat in patterns:
        for jar in sorted(_glob.glob(pat, recursive=True)):
            p = _Path(jar)
            if p.stat().st_size > 0 and p.stem not in seen_stems:
                seen_stems.add(p.stem)
                found.append(jar)
    if any("delta-spark_" in _Path(j).name for j in found):
        return tuple(found)
    return None


def _hostname_in_etc_hosts(hostname: str) -> bool:
    """Whether ``/etc/hosts`` maps *hostname* (exact token, comments stripped)."""
    try:
        with open("/etc/hosts") as fh:
            return any(
                hostname in line.split("#", 1)[0].split() for line in fh
            )
    except OSError:
        return False


# One stable path per process. Deliberately NOT under warehouse_dir: that is
# rmtree'd when a trial ends, but the Spark JVM outlives a single trial, so a
# per-trial path would leave -Djdk.net.hosts.file pointing at a deleted file and
# break resolution for every later trial in the same worker.
_JVM_HOSTS_FILE = Path(tempfile.gettempdir()) / f"scos_jvm_hosts_{os.getpid()}"


def _pin_unresolvable_hostname() -> None:
    """Make the JVM able to resolve this host's own name.

    ``SPARK_LOCAL_IP`` covers ``Utils.findLocalInetAddress``, but Spark 3.5 also
    calls ``InetAddress.getLocalHost()`` from places that never consult it —
    ``SparkHadoopUtil.appendS3CredentialsFromEnvironment`` and
    ``Utils.localCanonicalHostName`` (a static initializer). On a host whose name
    is missing from ``/etc/hosts`` those throw ``UnknownHostException`` before any
    SparkConf exists, so the whole Phase A baseline is lost. ``jdk.net.hosts.file``
    replaces the JDK's resolver and needs no root — container ``/etc/hosts`` is
    typically read-only.

    Gated on ``/etc/hosts`` rather than ``socket.gethostbyname``: some Python
    builds resolve a name the JVM cannot, so Python's resolver is not a valid
    proxy and would skip the fix exactly where it is needed.

    Caveat: ``jdk.net.hosts.file`` replaces DNS *entirely* for that JVM, so the
    file is seeded with the real ``/etc/hosts`` contents to avoid dropping
    entries the workload may need. Names that resolve only via a nameserver are
    still unavailable, which is why this is applied to the local Phase A runtime
    (local Spark + Delta over local files) and only when the hostname is already
    unresolvable — i.e. when the alternative is a guaranteed failure.
    """
    hostname = socket.gethostname()
    if _hostname_in_etc_hosts(hostname):
        return

    # JAVA_TOOL_OPTIONS is whitespace-delimited, so a path containing a space
    # cannot be expressed there at all. Bail out rather than corrupt the variable.
    if " " in str(_JVM_HOSTS_FILE):
        print(
            f"warn: JVM hosts file path contains a space ({_JVM_HOSTS_FILE}); "
            "cannot pass it via JAVA_TOOL_OPTIONS, skipping hostname pin"
        )
        return

    try:
        base = ""
        try:
            with open("/etc/hosts") as fh:
                base = fh.read()
        except OSError:
            base = "127.0.0.1 localhost\n::1 localhost\n"
        if not base.endswith("\n"):
            base += "\n"
        _JVM_HOSTS_FILE.write_text(f"{base}127.0.0.1 {hostname}\n")
    except OSError as exc:
        # Losing the workaround is not worth aborting the trial: without it Spark
        # fails with its own, more informative UnknownHostException.
        print(
            f"warn: could not write JVM hosts file {_JVM_HOSTS_FILE}: {exc}; "
            "local Spark may fail to resolve its own hostname"
        )
        return

    # Replace any prior -Djdk.net.hosts.file rather than appending: duplicated
    # options are resolved last-wins by the JVM, so appending would silently
    # repoint earlier trials' resolver.
    opt = f"-Djdk.net.hosts.file={_JVM_HOSTS_FILE}"
    kept = [
        tok for tok in os.environ.get("JAVA_TOOL_OPTIONS", "").split()
        if not tok.startswith("-Djdk.net.hosts.file=")
    ]
    os.environ["JAVA_TOOL_OPTIONS"] = " ".join([*kept, opt])


def _build_local_session(warehouse_dir):
    """Create a local Spark+Delta session rooted at *warehouse_dir*."""
    from pyspark.sql import SparkSession  # noqa: F811

    builder = (
        SparkSession.builder.master("local[1]")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.warehouse.dir", str(warehouse_dir))
        .config("spark.driver.extraJavaOptions", f"-Dderby.system.home={warehouse_dir}/derby")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.databricks.delta.schema.autoMerge.enabled", "true")
        .config("spark.sql.sources.default", "delta")
        # CREATE TABLE AS SELECT (common in notebooks converted from %%sql cells)
        # must create Delta tables, not Hive-metastore tables, in local Spark.
        .config("spark.sql.legacy.createHiveTableByDefault", "false")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    )
    jar_paths = _resolve_delta_jars()
    if jar_paths:
        return builder.config("spark.jars", ",".join(jar_paths)).getOrCreate()
    # Fallback: let configure_spark_with_delta_pip download JARs at runtime.
    from delta import configure_spark_with_delta_pip  # type: ignore[import-not-found]
    return configure_spark_with_delta_pip(builder).getOrCreate()


class LocalDeltaRuntime(ValidationRuntime):
    """Phase A: local PySpark + Delta Lake with per-trial schema isolation."""

    flavor = "local"

    def run_trial(self, request: TrialRequest) -> TrialResult:
        warehouse_dir = tempfile.mkdtemp(prefix="scos_local_")
        spark = None

        saved_env: dict[str, str | None] = {}
        env_keys = [
            "SCOS_OUTPUT_SCHEMA", "SCOS_DATABASE_NAME", "SPARK_LOCAL_IP",
            "JAVA_TOOL_OPTIONS",
        ]
        for key in env_keys:
            saved_env[key] = os.environ.get(key)

        try:
            # Spark resolves the driver's hostname before SparkConf takes effect.
            # On some hosts the autogenerated hostname is not resolvable, so pin
            # the local IP in the environment before the JVM starts.
            os.environ["SPARK_LOCAL_IP"] = "127.0.0.1"
            _pin_unresolvable_hostname()
            # Build session
            spark = _build_local_session(warehouse_dir)

            # Apply delta patches (idempotent)
            install_delta_patches(spark)

            # Create isolated schema
            local_schema = f"scos_{request.trial_id[:24]}_{uuid4().hex[:8]}".lower()
            os.environ["SCOS_OUTPUT_SCHEMA"] = local_schema
            # Phase A catalog token: local Spark's session catalog is `spark_catalog`,
            # so namespace-rebind patches that build 3-part names from
            # SCOS_DATABASE_NAME (e.g. spark.table(f"{db}.{schema}.T")) resolve here.
            # Phase B (scos_runtime) sets this to the Snowflake database instead.
            os.environ["SCOS_DATABASE_NAME"] = "spark_catalog"
            spark.sql(f"CREATE DATABASE IF NOT EXISTS {local_schema}")
            spark.sql(f"USE {local_schema}")

            # File I/O env setup — clear first so a retried Phase A run doesn't
            # append into stale files from a previous attempt.
            sink_capture_dir = os.path.join(request.results_dir, request.trial_id, "_sinks")
            shutil.rmtree(sink_capture_dir, ignore_errors=True)
            os.makedirs(sink_capture_dir, exist_ok=True)

            _tables = request.ep_config.get("tables") or {}
            file_read_paths = {
                name: os.path.join(request.mock_data_dir, tbl["mock_file"])
                for name, tbl in _tables.items()
                if tbl.get("category") == "file"
                and tbl.get("access", "read") != "write"
                and tbl.get("mock_file")
            }
            file_write_paths = {
                name: os.path.join(sink_capture_dir, _io_id_from_name(name).lower())
                for name, tbl in _tables.items()
                if tbl.get("category") == "file"
                and tbl.get("access", "read") in ("write", "readwrite")
            }
            file_env = file_io_env(request.ep_config, read_paths=file_read_paths, write_paths=file_write_paths)

            # Save/restore file env vars
            for key in file_env:
                saved_env[key] = os.environ.get(key)
            for key, val in file_env.items():
                os.environ[key] = val

            # Seed and declare sinks
            sink_tables = declared_sink_tables(request.ep_config, local_schema)
            seed_tables = seed_entrypoint(
                spark, request.ep_config, request.mock_data_dir, output_schema=local_schema
            )

            # Build context and run
            run_id = os.environ.get("SCOS_RUN_ID", uuid4().hex[:8])
            ctx = TrialContext(
                trial_id=request.trial_id,
                flavor="local",
                output_schema=local_schema,
                results_dir=request.results_dir,
                seed_tables=seed_tables,
                sink_tables=sink_tables,
                sink_capture_dir=sink_capture_dir,
                run_id=run_id,
            )

            manifest = run_and_capture(spark, request, ctx)

            return TrialResult(
                trial_id=request.trial_id,
                flavor="local",
                results_dir=request.results_dir,
                ok=manifest["ok"],
                manifest=manifest,
                output_schema=local_schema,
                error=manifest.get("error"),
            )
        finally:
            if spark is not None and hasattr(spark, "stop"):
                spark.stop()
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            shutil.rmtree(warehouse_dir, ignore_errors=True)
