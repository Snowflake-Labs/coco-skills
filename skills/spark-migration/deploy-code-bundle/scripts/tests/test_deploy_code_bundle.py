"""Unit tests for the deploy-code-bundle deterministic scripts."""

import prepare_code_bundle as pcb
import create_code_bundle as ccb
import execute_code_bundle as ecb
import inspect_entrypoint as ie
import generate_entrypoint as ge


# ---------------------------------------------------------------------------
# prepare_code_bundle — pure builders + file writing
# ---------------------------------------------------------------------------

class TestBuildCodeBundleYml:
    def test_warehouse_minimal_emits_runtime_version(self):
        y = pcb.build_code_bundle_yml("warehouse", "python")
        assert y.startswith("bundle:\n")
        assert "  type: custom" in y
        assert "  compute_type: warehouse" in y
        assert "  language: python" in y
        # runtime_version is REQUIRED and always emitted (default 3.10)
        assert "  compute_options:" in y
        assert '    runtime_version: "3.10"' in y
        # no reqs/env => those sub-blocks omitted; no pool fields on warehouse
        assert "properties:" not in y
        assert "env_vars:" not in y
        assert "compute_pool:" not in y
        assert "query_warehouse:" not in y

    def test_warehouse_does_not_emit_pool_fields(self):
        y = pcb.build_code_bundle_yml("warehouse", "python", runtime_version="3.11",
                                      compute_pool="CP", query_warehouse="WH")
        assert 'runtime_version: "3.11"' in y
        assert "compute_pool:" not in y          # pool fields suppressed on warehouse
        assert "query_warehouse:" not in y

    def test_compute_pool_emits_pool_and_warehouse(self):
        y = pcb.build_code_bundle_yml("compute_pool", "python", runtime_version="3.10",
                                      compute_pool="MY_POOL", query_warehouse="MY_WH")
        assert "  compute_type: compute_pool" in y
        assert '    runtime_version: "3.10"' in y
        assert "    compute_pool: MY_POOL" in y
        assert "    query_warehouse: MY_WH" in y

    def test_requirements_and_env(self):
        y = pcb.build_code_bundle_yml("warehouse", "python",
                                      requirements_file="requirements.txt",
                                      env_vars={"FOO": "bar"})
        assert "  properties:\n    requirements_file: requirements.txt" in y
        assert "  env_vars:\n    - FOO: bar" in y


class TestPrepare:
    def test_auto_detects_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("numpy\n")
        r = pcb.prepare(str(tmp_path), compute_type="warehouse")
        assert r["verdict"] == "PASS"
        assert r["requirements_file"] == "requirements.txt"
        assert (tmp_path / "code_bundle.yml").exists()

    def test_auto_detects_pyproject_when_no_requirements(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        r = pcb.prepare(str(tmp_path), compute_type="warehouse")
        assert r["requirements_file"] == "pyproject.toml"

    def test_compute_pool_requires_pool(self, tmp_path):
        r = pcb.prepare(str(tmp_path), compute_type="compute_pool")
        assert r["verdict"] == "FAIL"
        assert "compute-pool" in r["error"]

    def test_invalid_compute_type(self, tmp_path):
        r = pcb.prepare(str(tmp_path), compute_type="serverless")
        assert r["verdict"] == "FAIL"

    def test_missing_project_dir(self):
        r = pcb.prepare("/no/such/dir")
        assert r["verdict"] == "FAIL"

    def test_rejects_snowflake_prefixed_env(self, tmp_path):
        r = pcb.prepare(str(tmp_path), compute_type="warehouse",
                        env_vars={"SNOWFLAKE_DATABASE": "DB"})
        assert r["verdict"] == "FAIL"
        assert "not permitted" in r["error"]

    def test_runtime_version_defaults(self, tmp_path):
        r = pcb.prepare(str(tmp_path), compute_type="warehouse")
        assert r["runtime_version"] == pcb.DEFAULT_RUNTIME_VERSION

    def test_curate_requirements_writes_bundle_file(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pyspark>=2.4.5,<3.0.0\nnumpy>=1.18\n")
        r = pcb.prepare(str(tmp_path), compute_type="warehouse", curate_requirements_file=True)
        assert r["requirements_file"] == pcb.CURATED_REQUIREMENTS_FILE
        body = (tmp_path / pcb.CURATED_REQUIREMENTS_FILE).read_text()
        assert "pyspark" not in body           # dropped
        assert "numpy" in body                 # kept
        assert "snowpark-connect" in body      # added

    def test_parse_env(self):
        assert pcb._parse_env(["A=1", "B=x=y", "junk", "=empty"]) == {"A": "1", "B": "x=y"}


class TestCurateRequirements:
    def test_drops_pyspark_and_adds_snowpark_connect(self):
        out = pcb.curate_requirements(["pyspark>=2.4", "# comment", "boto3", "numpy"])
        assert not any(l.strip().lower().startswith("pyspark") for l in out)
        assert "snowpark-connect" in out
        assert "# comment" in out              # comments preserved
        assert "boto3" in out and "numpy" in out

    def test_does_not_duplicate_existing_snowpark_connect(self):
        out = pcb.curate_requirements(["snowpark-connect==0.1"])
        assert sum(1 for l in out if "snowpark-connect" in l) == 1


# ---------------------------------------------------------------------------
# create_code_bundle
# ---------------------------------------------------------------------------

class TestCreateCodeBundle:
    def test_build_create_sql_with_and_without_comment(self):
        sql = ccb.build_create_sql("DB.SC.B", "@DB.SC.STG/proj/", None)
        assert sql == "CREATE OR REPLACE CODE BUNDLE DB.SC.B FROM '@DB.SC.STG/proj/'"
        sql2 = ccb.build_create_sql("DB.SC.B", "@DB.SC.STG/proj/", "it's mine")
        assert "COMMENT = 'it''s mine'" in sql2   # single quote escaped

    def test_create_pass_when_describe_ok(self, monkeypatch):
        def fake_run_sql(sql, connection, timeout=300):
            return 0, "row", ""
        monkeypatch.setattr(ccb.sf_exec, "run_sql", fake_run_sql)
        r = ccb.create("DB", "SC", "STG", "proj", "B", "conn")
        assert r["verdict"] == "PASS"
        assert r["verified"] is True
        assert r["code_bundle"] == "DB.SC.B"
        assert r["from"] == "@DB.SC.STG/proj/"

    def test_create_no_connection_skipped(self, monkeypatch):
        monkeypatch.setattr(ccb.sf_exec, "run_sql",
                            lambda *a, **k: (1, "", "connector connect failed"))
        r = ccb.create("DB", "SC", "STG", "proj", "B", "conn")
        assert r["verdict"] == "SKIPPED"

    def test_create_failure(self, monkeypatch):
        monkeypatch.setattr(ccb.sf_exec, "run_sql",
                            lambda *a, **k: (1, "", "syntax error near CODE"))
        r = ccb.create("DB", "SC", "STG", "proj", "B", "conn")
        assert r["verdict"] == "FAIL"
        assert "CREATE CODE BUNDLE failed" in r["error"]


# ---------------------------------------------------------------------------
# execute_code_bundle
# ---------------------------------------------------------------------------

class TestExecuteCodeBundle:
    def test_build_execute_sql(self):
        assert ecb.build_execute_sql("DB.SC.B", "main.py", None) == \
            "EXECUTE CODE BUNDLE DB.SC.B ENTRYPOINT = 'main.py'"
        with_args = ecb.build_execute_sql("DB.SC.B", "src/main.py", "--flag x")
        assert with_args == "EXECUTE CODE BUNDLE DB.SC.B ENTRYPOINT = 'src/main.py' ARGUMENTS = '--flag x'"

    def test_pass(self, monkeypatch):
        monkeypatch.setattr(ecb.sf_exec, "run_sql", lambda *a, **k: (0, "done", ""))
        assert ecb.execute("DB.SC.B", "main.py", "conn")["verdict"] == "PASS"

    def test_with_warehouse_uses_run_sqls(self, monkeypatch):
        captured = {}
        def fake_run_sqls(sqls, connection, timeout=300):
            captured["sqls"] = sqls
            return 0, "done", ""
        monkeypatch.setattr(ecb.sf_exec, "run_sqls", fake_run_sqls)
        r = ecb.execute("DB.SC.B", "main.py", "conn", warehouse="WH")
        assert r["verdict"] == "PASS"
        assert captured["sqls"][0] == "USE WAREHOUSE WH"
        assert captured["sqls"][1].startswith("EXECUTE CODE BUNDLE")

    def test_no_connection_skipped(self, monkeypatch):
        monkeypatch.setattr(ecb.sf_exec, "run_sql",
                            lambda *a, **k: (1, "", "no default connection found"))
        assert ecb.execute("DB.SC.B", "main.py", "conn")["verdict"] == "SKIPPED"

    def test_runtime_error_is_fail(self, monkeypatch):
        monkeypatch.setattr(ecb.sf_exec, "run_sql",
                            lambda *a, **k: (1, "", "ModuleNotFoundError: pyspark"))
        r = ecb.execute("DB.SC.B", "main.py", "conn")
        assert r["verdict"] == "FAIL"
        assert "pyspark" in r["error"]


# ---------------------------------------------------------------------------
# inspect_entrypoint (Step 0.6 detect)
# ---------------------------------------------------------------------------

def _make_project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "batch.json").write_text("{}")
    (tmp_path / "src" / "main.py").write_text(
        "import argparse, os\n"
        "def go(args):\n"
        "    os.environ.get('SNOWFLAKE_DATABASE')\n"
        "if __name__ == '__main__':\n"
        "    p = argparse.ArgumentParser()\n"
        "    p.add_argument('--configFile', dest='configFilePath', required=True)\n"
        "    p.add_argument('--geoCodeFile', dest='geoCodeFilePath')\n"
        "    go(p.parse_args())\n"
    )
    return tmp_path


class TestInspectEntrypoint:
    def test_parse_argparse_flags_dest_required(self):
        import ast
        tree = ast.parse(
            "p.add_argument('--configFile', dest='configFilePath', required=True)\n"
            "p.add_argument('--geoCodeFile', dest='geoCodeFilePath')\n"
        )
        args = ie.parse_argparse(tree)
        by = {a["dest"]: a for a in args}
        assert by["configFilePath"]["required"] is True
        assert by["geoCodeFilePath"]["required"] is False

    def test_dest_derived_from_long_flag_when_no_dest(self):
        import ast
        tree = ast.parse("p.add_argument('--source-table')\n")
        assert ie.parse_argparse(tree)[0]["dest"] == "source_table"

    def test_inspect_full_project(self, tmp_path):
        _make_project(tmp_path)
        r = ie.inspect(str(tmp_path))
        assert r["verdict"] == "PASS"
        assert r["needs_wrapper"] is True                      # required arg + namespace + file hint
        assert "src" in r["source_roots"]
        assert "config/batch.json" in r["config_candidates"]
        assert r["reads_namespace"] is True
        assert "configFilePath" in r["local_file_arg_hints"]
        entry = next(e for e in r["entrypoints"] if e["file"].endswith("main.py"))
        assert entry["main_call"] == "go"
        assert any(a["dest"] == "configFilePath" and a["required"] for a in entry["args"])

    def test_no_wrapper_for_plain_main(self, tmp_path):
        (tmp_path / "run.py").write_text("if __name__ == '__main__':\n    print('hi')\n")
        r = ie.inspect(str(tmp_path))
        assert r["needs_wrapper"] is False


# ---------------------------------------------------------------------------
# generate_entrypoint (Step 0.6 draft)
# ---------------------------------------------------------------------------

class TestGenerateEntrypoint:
    def test_build_minimal_always_imports_snowpark_connect(self):
        src = ge.build_entrypoint("main:main")
        assert "import snowflake.snowpark_connect" in src
        assert "SNOWFLAKE_DATABASE" not in src        # no namespace block
        assert "sys.path.insert" not in src           # no source root
        assert "file.get" not in src                  # no stage files
        assert "from main import main" in src
        assert "    main(_args)" in src

    def test_build_namespace_and_source_root_conditional(self):
        src = ge.build_entrypoint("main:MainApplication.main", source_root="src",
                                  namespace_db="DB", namespace_schema="SC")
        assert "os.environ[\"SNOWFLAKE_DATABASE\"] = 'DB'" in src
        assert "os.environ[\"SNOWFLAKE_SCHEMA\"] = 'SC'" in src
        assert "_SRC = os.path.join(_ROOT, 'src')" in src
        assert "from main import MainApplication" in src
        assert "    MainApplication.main(_args)" in src

    def test_build_stage_file_and_literal_arg_binding(self):
        src = ge.build_entrypoint(
            "main:MainApplication.main",
            literal_args={"configFilePath": "config/batch.json"},
            stage_files=[{"stage_path": "@D.S.ST/geo/g.csv", "local_dir": "/tmp",
                          "dest": "geoCodeFilePath", "basename": "g.csv"}],
        )
        assert "_session.file.get('@D.S.ST/geo/g.csv', '/tmp')" in src
        assert "geoCodeFilePath = os.path.join('/tmp', 'g.csv')" in src
        assert "configFilePath = os.path.join(_ROOT, 'config/batch.json')" in src
        assert "SimpleNamespace(configFilePath=configFilePath, geoCodeFilePath=geoCodeFilePath)" in src

    def test_build_neutralizes_injection_in_string_values(self):
        # A namespace value containing a quote + Python must be embedded as a safe
        # literal (repr), not break out into executable code. Generated file stays valid.
        import ast
        malicious = 'X"; import os; os.system("touch /tmp/pwned"); _="'
        src = ge.build_entrypoint("main:main", namespace_db=malicious)
        ast.parse(src)  # must still be syntactically valid
        assert "import os; os.system" not in src.replace(repr(malicious), "")
        assert repr(malicious) in src  # embedded as a literal

    def test_build_rejects_non_identifier_dest(self):
        import pytest
        with pytest.raises(ValueError):
            ge.build_entrypoint("main:main", literal_args={"a); import os; (": "x"})

    def test_build_rejects_injection_in_module_path(self):
        import pytest
        with pytest.raises(ValueError):
            ge.build_entrypoint("os; import sys:main")

    def test_build_rejects_bad_entry_import(self):
        import pytest
        with pytest.raises(ValueError):
            ge.build_entrypoint("main.MainApplication.main")   # missing ':'

    def test_generate_writes_and_refuses_overwrite(self, tmp_path):
        r = ge.generate(str(tmp_path), "main:main", out="run_bundle.py")
        assert r["verdict"] == "PASS"
        assert (tmp_path / "run_bundle.py").exists()
        r2 = ge.generate(str(tmp_path), "main:main", out="run_bundle.py")
        assert r2["verdict"] == "FAIL" and "already exists" in r2["error"]
        r3 = ge.generate(str(tmp_path), "main:main", out="run_bundle.py", force=True)
        assert r3["verdict"] == "PASS"

    def test_parse_stage_file_and_arg_helpers(self):
        sf = ge._parse_stage_file("@D.S.ST/geo/g.csv::/tmp::geoCodeFilePath")
        assert sf == {"stage_path": "@D.S.ST/geo/g.csv", "local_dir": "/tmp",
                      "dest": "geoCodeFilePath", "basename": "g.csv"}
        assert ge._parse_arg("configFilePath=config/x.json") == ("configFilePath", "config/x.json")


# ---------------------------------------------------------------------------
# _main_call heuristic (the twice-buggy detector) — direct tests
# ---------------------------------------------------------------------------

class TestMainCall:
    def _call(self, body: str):
        import ast
        return ie._main_call(ast.parse("if __name__ == '__main__':\n" + body))

    def test_prefers_main_inside_try_except_with_noise(self):
        # mirrors the real main.py: entry wrapped in try/except with sys.exit + traceback
        body = (
            "    p = argparse.ArgumentParser()\n"
            "    args = p.parse_args()\n"
            "    try:\n"
            "        MainApplication.main(args)\n"
            "    except Exception:\n"
            "        traceback.print_exc()\n"
            "        print('boom')\n"
            "        sys.exit(1)\n"
        )
        assert self._call(body) == "MainApplication.main"

    def test_returns_none_when_only_noise(self):
        assert self._call("    print('hi')\n    sys.exit(0)\n") is None

    def test_last_plain_call_when_no_dot_main(self):
        assert self._call("    p.add_argument('--x')\n    go(p.parse_args())\n") == "go"


# ---------------------------------------------------------------------------
# inspect_entrypoint — detection branches
# ---------------------------------------------------------------------------

class TestInspectBranches:
    def test_reads_namespace_false_and_no_wrapper(self, tmp_path):
        (tmp_path / "run.py").write_text(
            "import argparse\n"
            "if __name__ == '__main__':\n"
            "    p = argparse.ArgumentParser()\n"
            "    p.add_argument('--verbose', default=False)\n"   # optional, not a file, no namespace
            "    main(p.parse_args())\n"
        )
        r = ie.inspect(str(tmp_path))
        assert r["reads_namespace"] is False
        assert r["local_file_arg_hints"] == []
        assert r["needs_wrapper"] is False        # no required arg, no namespace, no file hint

    def test_config_candidates_exclude_bundle_yaml(self, tmp_path):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "kipawa.json").write_text("{}")
        (tmp_path / "code_bundle.yml").write_text("bundle: {}")
        (tmp_path / "snowflake.yml").write_text("x: 1")
        (tmp_path / "environment.yml").write_text("name: x")
        r = ie.inspect(str(tmp_path))
        assert "config/kipawa.json" in r["config_candidates"]
        assert not any(c.endswith(("code_bundle.yml", "snowflake.yml", "environment.yml"))
                       for c in r["config_candidates"])

    def test_parse_argparse_positional_and_default(self):
        import ast
        tree = ast.parse("p.add_argument('input')\np.add_argument('--n', default=5)\n")
        by = {a["dest"]: a for a in ie.parse_argparse(tree)}
        assert by["input"]["dest"] == "input" and by["input"]["required"] is False
        assert by["n"]["default"] == 5

    def test_skips_unparseable_file(self, tmp_path):
        (tmp_path / "bad.py").write_text("def (:\n")                       # syntax error
        (tmp_path / "good.py").write_text("if __name__ == '__main__':\n    main()\n")
        r = ie.inspect(str(tmp_path))
        assert r["verdict"] == "PASS"
        assert any(e["file"] == "good.py" for e in r["entrypoints"])       # didn't crash on bad.py


# ---------------------------------------------------------------------------
# generate_entrypoint — the generated launcher must be valid Python
# ---------------------------------------------------------------------------

class TestGeneratedSourceValidity:
    import pytest

    @pytest.mark.parametrize("kwargs", [
        {},
        {"source_root": "src", "namespace_db": "DB", "namespace_schema": "SC"},
        {"literal_args": {"configFilePath": "config/b.json"},
         "stage_files": [{"stage_path": "@D.S.ST/g/x.csv", "local_dir": "/tmp",
                          "dest": "geoCodeFilePath", "basename": "x.csv"}]},
        {"source_root": "src", "namespace_db": "DB", "namespace_schema": "SC",
         "literal_args": {"a": "x.json", "b": "y.json"},
         "stage_files": [
             {"stage_path": "@D.S.ST/g/x.csv", "local_dir": "/tmp", "dest": "c", "basename": "x.csv"},
             {"stage_path": "@D.S.ST/g/y.csv", "local_dir": "/tmp", "dest": "d", "basename": "y.csv"}]},
    ])
    def test_generated_source_compiles(self, kwargs):
        src = ge.build_entrypoint("main:MainApplication.main", **kwargs)
        compile(src, "<generated run_bundle.py>", "exec")   # raises SyntaxError if malformed

    def test_multiple_stage_files_and_args_all_bound(self):
        src = ge.build_entrypoint(
            "main:MainApplication.main",
            literal_args={"a": "x.json", "b": "y.json"},
            stage_files=[
                {"stage_path": "@D.S.ST/g/x.csv", "local_dir": "/tmp", "dest": "c", "basename": "x.csv"},
                {"stage_path": "@D.S.ST/g/y.csv", "local_dir": "/tmp", "dest": "d", "basename": "y.csv"}],
        )
        assert "SimpleNamespace(a=a, b=b, c=c, d=d)" in src

    def test_generate_fails_when_project_missing(self):
        r = ge.generate("/no/such/dir/xyz", "main:main")
        assert r["verdict"] == "FAIL" and "not found" in r["error"]
