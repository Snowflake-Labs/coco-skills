"""Unit tests for the deploy-notebook notebook-specific deterministic scripts.

Shared-script tests (scan_migration_gaps, upload_project, ensure_namespace,
sf_exec) live in deploy-common/scripts/tests/test_deploy_common.py.
"""

import create_notebook as cn
import prepare_validation as pv
import execute_notebook as ex


# ---------------------------------------------------------------------------
# create_notebook helpers
# ---------------------------------------------------------------------------

class TestCreateNotebookHelpers:
    def test_parse_org_acct(self):
        assert cn._parse_org_acct("('myorg', 'myacct_aws')") == ("myorg", "myacct_aws")
        assert cn._parse_org_acct("no tuple") == (None, None)


# ---------------------------------------------------------------------------
# prepare_validation — pure builders + file writing
# ---------------------------------------------------------------------------

class TestPrepareValidation:
    def test_build_environment_yml_has_python_base_and_dedupes_extras(self):
        content = pv.build_environment_yml("nb", ["pandas", "numpy"])  # numpy already base
        assert "  - python=3.10" in content
        assert "  - snowpark-connect" in content
        assert "  - scikit-learn" in content
        assert content.count("  - numpy") == 1          # not duplicated
        assert "  - pandas" in content
        assert content.startswith("name: nb\n")

    def test_build_snowflake_yml_structure(self):
        content = pv.build_snowflake_yml(
            "nb", "NB", "run.ipynb", "WH", ["run.ipynb", "environment.yml", "src/"])
        assert "definition_version: 2" in content
        assert "    type: notebook" in content
        assert "      name: NB" in content
        assert "    notebook_file: run.ipynb" in content
        assert "    query_warehouse: WH" in content
        assert "      - src/" in content

    def test_prepare_writes_both_files_and_discovers_artifacts(self, tmp_path):
        (tmp_path / "run.ipynb").write_text("{}")
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "c.json").write_text("{}")
        (tmp_path / "src").mkdir()
        (tmp_path / ".DS_Store").write_text("junk")
        r = pv.prepare(str(tmp_path), "run.ipynb", "NB", "WH")
        assert r["verdict"] == "PASS"
        assert (tmp_path / "environment.yml").exists()
        assert (tmp_path / "snowflake.yml").exists()
        assert "run.ipynb" in r["artifacts"] and "environment.yml" in r["artifacts"]
        assert "config/" in r["artifacts"] and "src/" in r["artifacts"]
        assert ".DS_Store" not in r["artifacts"]

    def test_prepare_fails_when_notebook_missing(self, tmp_path):
        r = pv.prepare(str(tmp_path), "missing.ipynb", "NB", "WH")
        assert r["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# execute_notebook
# ---------------------------------------------------------------------------

class TestExecuteNotebook:
    def test_pass(self, monkeypatch):
        monkeypatch.setattr(ex.sf_exec, "run_sql", lambda *a, **k: (0, "done", ""))
        assert ex.execute("DB.SC.NB", "conn")["verdict"] == "PASS"

    def test_no_connection_skipped(self, monkeypatch):
        monkeypatch.setattr(ex.sf_exec, "run_sql",
                            lambda *a, **k: (1, "", "connector connect failed"))
        assert ex.execute("DB.SC.NB", "conn")["verdict"] == "SKIPPED"

    def test_runtime_error_is_fail(self, monkeypatch):
        monkeypatch.setattr(ex.sf_exec, "run_sql",
                            lambda *a, **k: (1, "", "Could not setup the pipeline"))
        r = ex.execute("DB.SC.NB", "conn")
        assert r["verdict"] == "FAIL"
        assert "pipeline" in r["error"]
