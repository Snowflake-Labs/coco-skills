"""Tests for the Phase A / Phase B blockers found on a minimal 1-entrypoint workload.

Covers:
  1 ``_accepts_session_first`` — the harness must not bind the Spark session to an
    entrypoint's first *business* argument (``run(source_table=..., ...)``). Doing so
    surfaced far away as "Argument `tableName` should be str, got SparkSession".
  2 ``_pin_unresolvable_hostname`` — Spark 3.5 calls ``InetAddress.getLocalHost()``
    from paths that ignore ``SPARK_LOCAL_IP``, so a host missing from /etc/hosts
    loses the whole Phase A baseline.
  3 ``intercept_session`` — the harness owns the session lifecycle, so a workload's
    ``finally: spark.stop()`` must not kill the session ``capture_results`` needs.
  4 seed-venv phase b — pyspark must be pinned below 4 (snowpark-connect runs on
    Spark 3.5 and breaks on PySpark 4's renamed ``error_class`` kwarg).

Run: ../.venv/bin/pytest scripts/tests/test_entrypoint_binding_and_env.py -q
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent
_SCRIPTS_DIR = _TESTS_DIR.parent
_HARNESS_DIR = _SCRIPTS_DIR / "harness"
for _p in (str(_HARNESS_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from runtimes._executor import _accepts_session_first  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_java_tool_options():
    """Guarantee JAVA_TOOL_OPTIONS is left exactly as it was found.

    The code under test writes it directly, and ``monkeypatch.delenv(raising=False)``
    records no undo entry when the variable was already absent — so that write
    would survive the test. A leaked ``-Djdk.net.hosts.file`` pointing at a
    deleted ``tmp_path`` silently breaks every later test that shells out to a JVM
    (observed: ``test_check_java_syntax_rejects_broken_syntax`` began returning no
    error at all).
    """
    saved = os.environ.get("JAVA_TOOL_OPTIONS")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("JAVA_TOOL_OPTIONS", None)
        else:
            os.environ["JAVA_TOOL_OPTIONS"] = saved


# ---------------------------------------------------------------------------
# 1. session injection is signature-aware
# ---------------------------------------------------------------------------

def test_session_injected_when_first_param_is_named_spark():
    def run(spark, table="t"):
        pass

    assert _accepts_session_first(run) is True


def test_session_injected_for_other_session_names():
    for name in ("session", "spark_session", "sc"):
        fn = eval(f"lambda {name}, x=1: None")  # noqa: S307 - test-local literal
        assert _accepts_session_first(fn) is True, name


def test_session_NOT_injected_when_first_param_is_a_defaulted_business_arg():
    # The real regression: pipeline.py's entrypoint builds its own session, so
    # `spark` was binding to source_table.
    def run(source_table="orders", target_table="orders_enriched"):
        pass

    assert _accepts_session_first(run) is False


def test_required_non_session_first_param_still_injects():
    # Ambiguous; preserve historical behaviour rather than silently changing it.
    def run(df):
        pass

    assert _accepts_session_first(run) is True


def test_zero_arg_entrypoint_does_not_receive_session():
    def run():
        pass

    assert _accepts_session_first(run) is False


def test_var_positional_absorbs_session():
    def run(*args):
        pass

    assert _accepts_session_first(run) is True


def test_unintrospectable_callable_falls_back_to_injecting():
    # C builtins raise from inspect.signature; must not crash and must keep the
    # legacy behaviour.
    assert _accepts_session_first(print) is True


def test_keyword_only_entrypoint_does_not_receive_positional_session():
    def run(*, source_table="orders"):
        pass

    assert _accepts_session_first(run) is False


def test_unbound_method_judges_the_slot_after_self():
    """`Class.run` resolved via a dotted callable_name carries `self` in slot 0."""

    class Job:
        def run(self, source_table="orders"):
            pass

        def run_with_session(self, spark, table="t"):
            pass

        @classmethod
        def run_cls(cls, source_table="orders"):
            pass

    # Unbound: self occupies slot 0 and must not be mistaken for a session slot.
    assert _accepts_session_first(Job.run) is False
    assert _accepts_session_first(Job.run_with_session) is True
    assert _accepts_session_first(Job.run_cls) is False
    # Bound: self is already applied, so the visible first slot is the real one.
    assert _accepts_session_first(Job().run) is False
    assert _accepts_session_first(Job().run_with_session) is True


# ---------------------------------------------------------------------------
# 2. unresolvable-hostname pin
# ---------------------------------------------------------------------------

def _reload_local_runtime():
    """Import local_runtime with pyspark/delta absent (we only need the helper)."""
    from runtimes import local_runtime

    return local_runtime



def test_hostname_pin_writes_hosts_file_and_sets_java_tool_options(tmp_path, monkeypatch):

    lr = _reload_local_runtime()
    monkeypatch.delenv("JAVA_TOOL_OPTIONS", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "ai-d0c5")
    monkeypatch.setattr(lr, "_hostname_in_etc_hosts", lambda h: False)
    monkeypatch.setattr(lr, "_JVM_HOSTS_FILE", tmp_path / "jvm_hosts")

    lr._pin_unresolvable_hostname()

    written = (tmp_path / "jvm_hosts").read_text()
    assert "127.0.0.1 ai-d0c5" in written
    assert os.environ["JAVA_TOOL_OPTIONS"] == (
        f"-Djdk.net.hosts.file={tmp_path / 'jvm_hosts'}"
    )


def test_hostname_pin_preserves_existing_etc_hosts_entries(tmp_path, monkeypatch):
    """jdk.net.hosts.file replaces DNS wholesale, so real entries must be kept."""
    lr = _reload_local_runtime()
    monkeypatch.delenv("JAVA_TOOL_OPTIONS", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "ai-d0c5")
    monkeypatch.setattr(lr, "_hostname_in_etc_hosts", lambda h: False)
    monkeypatch.setattr(lr, "_JVM_HOSTS_FILE", tmp_path / "jvm_hosts")

    lr._pin_unresolvable_hostname()

    written = (tmp_path / "jvm_hosts").read_text()
    # Whatever the real /etc/hosts had must survive, plus our own name.
    real = Path("/etc/hosts").read_text()
    for line in real.splitlines():
        if line.strip() and not line.strip().startswith("#"):
            assert line in written, f"dropped /etc/hosts entry: {line!r}"
    assert "127.0.0.1 ai-d0c5" in written


def test_hostname_pin_is_a_noop_when_hostname_is_in_etc_hosts(tmp_path, monkeypatch):

    lr = _reload_local_runtime()
    monkeypatch.delenv("JAVA_TOOL_OPTIONS", raising=False)
    monkeypatch.setattr(lr, "_JVM_HOSTS_FILE", tmp_path / "jvm_hosts")
    monkeypatch.setattr(socket, "gethostname", lambda: "known-box")
    monkeypatch.setattr(lr, "_hostname_in_etc_hosts", lambda h: True)

    lr._pin_unresolvable_hostname()

    assert "JAVA_TOOL_OPTIONS" not in os.environ
    assert not (tmp_path / "jvm_hosts").exists()


def test_etc_hosts_lookup_ignores_comments_and_substrings(tmp_path, monkeypatch):
    """The real gate: a commented entry or a longer name must not count as a match."""
    lr = _reload_local_runtime()
    hosts = tmp_path / "etc_hosts"
    hosts.write_text("# 127.0.0.1 ai-d0c5\n10.0.0.1 ai-d0c5-extra\n127.0.0.1\tai-real\n")
    real_open = open
    monkeypatch.setattr(
        lr, "open",
        lambda f, *a, **k: real_open(hosts if f == "/etc/hosts" else f, *a, **k),
        raising=False,
    )

    assert lr._hostname_in_etc_hosts("ai-d0c5") is False   # commented out
    assert lr._hostname_in_etc_hosts("ai-d0c5-extra") is True
    assert lr._hostname_in_etc_hosts("ai-real") is True     # tab-separated
    assert lr._hostname_in_etc_hosts("ai") is False         # substring only


def test_etc_hosts_lookup_survives_unreadable_file(monkeypatch):
    lr = _reload_local_runtime()

    def _boom(*a, **k):
        raise OSError("no /etc/hosts here")

    monkeypatch.setattr(lr, "open", _boom, raising=False)
    # Unreadable means "cannot prove it is mapped", so the pin must still apply.
    assert lr._hostname_in_etc_hosts("anything") is False


def test_hostname_pin_degrades_gracefully_when_the_file_cannot_be_written(
    tmp_path, monkeypatch, capsys
):
    """A failed workaround must warn, not abort the trial."""

    lr = _reload_local_runtime()
    monkeypatch.delenv("JAVA_TOOL_OPTIONS", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "ai-d0c5")
    monkeypatch.setattr(lr, "_hostname_in_etc_hosts", lambda h: False)
    unwritable = tmp_path / "no_such_dir" / "jvm_hosts"
    monkeypatch.setattr(lr, "_JVM_HOSTS_FILE", unwritable)

    lr._pin_unresolvable_hostname()  # must not raise

    assert "JAVA_TOOL_OPTIONS" not in os.environ
    assert "could not write JVM hosts file" in capsys.readouterr().out


def test_hostname_pin_replaces_a_stale_hosts_option_instead_of_appending(
    tmp_path, monkeypatch
):
    """Two -Djdk.net.hosts.file options are last-wins; a stale one must be dropped."""

    lr = _reload_local_runtime()
    monkeypatch.setenv(
        "JAVA_TOOL_OPTIONS", "-Xmx1g -Djdk.net.hosts.file=/tmp/stale_deleted_path"
    )
    monkeypatch.setattr(socket, "gethostname", lambda: "ai-d0c5")
    monkeypatch.setattr(lr, "_hostname_in_etc_hosts", lambda h: False)
    monkeypatch.setattr(lr, "_JVM_HOSTS_FILE", tmp_path / "jvm_hosts")

    lr._pin_unresolvable_hostname()

    opts = os.environ["JAVA_TOOL_OPTIONS"]
    assert opts.count("-Djdk.net.hosts.file=") == 1
    assert "/tmp/stale_deleted_path" not in opts
    assert "-Xmx1g" in opts
    assert f"-Djdk.net.hosts.file={tmp_path / 'jvm_hosts'}" in opts


def test_hosts_file_is_not_under_a_per_trial_warehouse_dir(monkeypatch):
    """warehouse_dir is rmtree'd per trial; the JVM outlives that."""
    lr = _reload_local_runtime()
    assert "scos_local_" not in str(lr._JVM_HOSTS_FILE), (
        "hosts file must not live in the per-trial warehouse dir"
    )
    assert str(os.getpid()) in str(lr._JVM_HOSTS_FILE)


def test_a_path_with_a_space_is_refused_rather_than_corrupting_java_tool_options(
    tmp_path, monkeypatch, capsys
):
    """JAVA_TOOL_OPTIONS is whitespace-delimited, so such a path is unusable."""
    lr = _reload_local_runtime()
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Xmx1g")
    monkeypatch.setattr(socket, "gethostname", lambda: "ai-d0c5")
    monkeypatch.setattr(lr, "_hostname_in_etc_hosts", lambda h: False)
    spaced = tmp_path / "dir with space" / "jvm_hosts"
    spaced.parent.mkdir()
    monkeypatch.setattr(lr, "_JVM_HOSTS_FILE", spaced)

    lr._pin_unresolvable_hostname()

    # Untouched, not corrupted into two bogus tokens.
    assert os.environ["JAVA_TOOL_OPTIONS"] == "-Xmx1g"
    assert not spaced.exists()
    assert "contains a space" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 3. intercept_session keeps the harness-owned session alive
# ---------------------------------------------------------------------------

class _FakeSession:
    """Stands in for a Spark session; `stop` records that it was called."""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeConnectSession(_FakeSession):
    """A different class, mirroring Phase B's Spark Connect session type."""


def _intercept_session():
    """Import intercept_session with pyspark's builder attribute stubbed out."""
    import types

    pyspark = sys.modules.setdefault("pyspark", types.ModuleType("pyspark"))
    sql_mod = sys.modules.setdefault("pyspark.sql", types.ModuleType("pyspark.sql"))
    if not hasattr(sql_mod, "SparkSession"):
        class _SparkSession:  # minimal shape for mock.patch's attribute walk
            class builder:
                @staticmethod
                def getOrCreate():
                    raise AssertionError("real getOrCreate must not be called")

        sql_mod.SparkSession = _SparkSession
        pyspark.sql = sql_mod

    from helpers import intercept_session

    return intercept_session


def test_workload_spark_stop_is_neutralized_during_the_trial():
    intercept_session = _intercept_session()
    session = _FakeSession()

    with intercept_session(session) as handed_out:
        assert handed_out is session
        session.stop()  # what `finally: spark.stop()` does
        assert session.stopped is False, "harness session must survive the workload"

    # After the trial the runtime must still be able to tear the session down.
    session.stop()
    assert session.stopped is True


def test_stop_neutralization_follows_the_sessions_own_class():
    """Phase B's session is a different class than Phase A's; both must be covered."""
    intercept_session = _intercept_session()
    session = _FakeConnectSession()

    with intercept_session(session):
        session.stop()
        assert session.stopped is False

    assert _FakeConnectSession.stop is not None  # patch cleanly reverted
    session.stop()
    assert session.stopped is True


# ---------------------------------------------------------------------------
# 4. seed-venv pins pyspark below 4 for the SCOS venv
# ---------------------------------------------------------------------------

def test_scos_venv_install_pins_pyspark_below_4():
    """snowpark-connect declares no pyspark dep; the harness must pin it itself."""
    src = (_SCRIPTS_DIR / "validate.py").read_text()
    scos_block = src.split('scos venv: installing snowpark-connect', 1)[1][:800]
    assert "pyspark>=3.5,<4" in scos_block, (
        "Phase B install must pin pyspark<4: PySpark 4 renamed "
        "PySparkException's error_class kwarg and breaks snowpark-connect "
        "with SNOWPARK CONNECT ERROR CODE: 5001"
    )
