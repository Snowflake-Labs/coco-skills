"""Unit tests for the deploy-common shared deterministic scripts."""

import scan_migration_gaps as scan
import upload_project as up
import ensure_namespace as en
import sf_exec


# ---------------------------------------------------------------------------
# scan_migration_gaps
# ---------------------------------------------------------------------------

class TestScanMigrationGaps:
    def test_flags_action_markers_and_placeholders(self, tmp_path):
        (tmp_path / "a.py").write_text(
            "# SCOS: [SPRKCNTPY1000] TODO - .save(path) needs a stage\n"
            "x = 1  # SCOS: fixed, no action\n"
            'session.sql("USE DATABASE <DATABASE>")\n'
        )
        r = scan.scan(str(tmp_path))
        assert r["has_action_items"] is True
        assert r["action_marker_count"] == 1          # only the TODO one
        assert len(r["markers"]) == 2                  # both SCOS lines captured
        assert any(p["placeholder"] == "<DATABASE>" for p in r["placeholders"])

    def test_reads_issues_csv_and_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pyspark>=2.4\n# comment\nnumpy\n")
        rep = tmp_path / "Reports"
        rep.mkdir()
        (rep / "Issues.csv").write_text("Code,Desc\nSPRKCNTPY1000,x\nSPRKCNTPY4000,y\n")
        r = scan.scan(str(tmp_path))
        assert r["packages"] == ["pyspark>=2.4", "numpy"]
        assert r["issues_csv"]["count"] == 2
        assert r["has_action_items"] is True

    def test_clean_project_has_no_action_items(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        r = scan.scan(str(tmp_path))
        assert r["has_action_items"] is False


# ---------------------------------------------------------------------------
# upload_project.plan_uploads
# ---------------------------------------------------------------------------

class TestPlanUploads:
    def test_preserves_relative_paths_and_skips_junk(self, tmp_path):
        (tmp_path / "main.ipynb").write_text("{}")
        (tmp_path / ".DS_Store").write_text("junk")
        sub = tmp_path / "src" / "pipeline"
        sub.mkdir(parents=True)
        (sub / "impl.py").write_text("x = 1\n")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_text("")

        stage_root, uploads, skipped = up.plan_uploads(str(tmp_path), "proj", "DB", "SC", "STG")
        assert stage_root == "@DB.SC.STG/proj"
        rels = {u["rel"] for u in uploads}
        assert rels == {"main.ipynb", "src/pipeline/impl.py"}   # .DS_Store + __pycache__ skipped
        targets = {u["rel"]: u["stage_target"] for u in uploads}
        assert targets["main.ipynb"] == "@DB.SC.STG/proj/"
        assert targets["src/pipeline/impl.py"] == "@DB.SC.STG/proj/src/pipeline/"
        assert skipped == []

    def test_skips_sensitive_files(self, tmp_path):
        (tmp_path / "main.py").write_text("x=1")
        (tmp_path / ".env").write_text("SECRET=1")
        (tmp_path / "rsa_key.p8").write_text("-----BEGIN PRIVATE KEY-----")
        (tmp_path / "credentials.json").write_text("{}")
        (tmp_path / "server.pem").write_text("x")
        stage_root, uploads, skipped = up.plan_uploads(str(tmp_path), "proj", "DB", "SC", "STG")
        rels = {u["rel"] for u in uploads}
        assert rels == {"main.py"}  # secrets excluded
        assert set(skipped) == {".env", "rsa_key.p8", "credentials.json", "server.pem"}


# ---------------------------------------------------------------------------
# ensure_namespace
# ---------------------------------------------------------------------------

class TestEnsureNamespace:
    def test_check_pass_when_both_exist(self, monkeypatch):
        def fake_run_sql(sql, connection, timeout=300):
            return 0, "row", ""  # non-empty output => exists
        monkeypatch.setattr(en.sf_exec, "run_sql", fake_run_sql)
        r = en.check("DB", "SC", "conn")
        assert r["verdict"] == "PASS"
        assert r["database_exists"] and r["schema_exists"]

    def test_check_missing_schema(self, monkeypatch):
        def fake_run_sql(sql, connection, timeout=300):
            if "SHOW DATABASES" in sql:
                return 0, "row", ""            # db exists
            return 0, "", ""                    # schema missing (no rows)
        monkeypatch.setattr(en.sf_exec, "run_sql", fake_run_sql)
        r = en.check("DB", "SC", "conn")
        assert r["verdict"] == "MISSING"
        assert r["database_exists"] is True
        assert r["schema_exists"] is False

    def test_check_no_connection_is_skipped(self, monkeypatch):
        def fake_run_sql(sql, connection, timeout=300):
            return 1, "", "connector connect failed: bad creds"
        monkeypatch.setattr(en.sf_exec, "run_sql", fake_run_sql)
        r = en.check("DB", "SC", "conn")
        assert r["verdict"] == "SKIPPED"

    def test_create_runs_ddl_then_rechecks(self, monkeypatch):
        calls = []

        def fake_run_sql(sql, connection, timeout=300):
            calls.append(sql)
            return 0, "row", ""  # every statement succeeds; SHOWs return a row
        monkeypatch.setattr(en.sf_exec, "run_sql", fake_run_sql)
        r = en.create("DB", "SC", "conn")
        assert r["verdict"] == "PASS"
        assert r.get("created") is True
        assert any("CREATE DATABASE IF NOT EXISTS DB" in s for s in calls)
        assert any("CREATE SCHEMA IF NOT EXISTS DB.SC" in s for s in calls)


# ---------------------------------------------------------------------------
# sf_exec — backend selection helpers
# ---------------------------------------------------------------------------

class TestSfExec:
    def test_backend_cli_when_snow_available(self, monkeypatch):
        monkeypatch.setattr(sf_exec, "snow_cli_available", lambda: True)
        assert sf_exec.backend("conn") == "cli"

    def test_backend_connector_when_no_cli(self, monkeypatch):
        monkeypatch.setattr(sf_exec, "snow_cli_available", lambda: False)
        assert sf_exec.backend("conn") == "connector"

    def test_run_sql_falls_back_to_connector(self, monkeypatch):
        monkeypatch.setattr(sf_exec, "snow_cli_available", lambda: False)
        monkeypatch.setattr(sf_exec, "_run_sql_connector", lambda sql, c: (0, "ok", ""))
        assert sf_exec.run_sql("SELECT 1", "conn") == (0, "ok", "")

    def test_is_no_connection(self):
        assert sf_exec.is_no_connection("connector connect failed: bad") is True
        assert sf_exec.is_no_connection("No default connection found") is True
        assert sf_exec.is_no_connection("connector not available for put") is True
        assert sf_exec.is_no_connection("some SQL error") is False

    def test_run_sqls_connector_runs_all_in_one_session(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sf_exec, "snow_cli_available", lambda: False)

        class FakeCur:
            def execute(self, sql): calls.append(sql)
            def fetchall(self): return [("ok",)]
            def close(self): pass

        class FakeConn:
            def cursor(self): return FakeCur()
            def close(self): pass

        monkeypatch.setattr(sf_exec, "_connect", lambda c: FakeConn())
        rc, out, err = sf_exec.run_sqls(["USE WAREHOUSE WH", "EXECUTE CODE BUNDLE X ENTRYPOINT='m.py'"], "conn")
        assert rc == 0
        assert calls == ["USE WAREHOUSE WH", "EXECUTE CODE BUNDLE X ENTRYPOINT='m.py'"]
