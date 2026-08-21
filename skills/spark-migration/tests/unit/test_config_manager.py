"""Tests for config_manager – configuration management for Snowpark Migrator."""

import json
import os

import pytest

import config_manager
from config_manager import (
    DEFAULTS,
    GLOBAL_CONFIG_NAME,
    create_configuration,
    get_config_path,
    get_global_config_path,
    list_configurations,
    load_configuration,
    load_global,
    save_configuration,
    save_global,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(path, cfg):
    """Write a config dict to *path* for test setup."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)


def _read_config(path):
    """Read a config dict from *path*."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# get_config_path
# ---------------------------------------------------------------------------


class TestGetConfigPath:
    def test_basic(self):
        assert get_config_path("/skill/configurations", "my_project") == (
            os.path.join("/skill/configurations", "my_project.json")
        )

    def test_preserves_dir_separator(self):
        result = get_config_path("/a/b/c", "proj")
        assert result.endswith("proj.json")
        assert "/a/b/c/" in result or result.startswith("/a/b/c")


# ---------------------------------------------------------------------------
# list_configurations
# ---------------------------------------------------------------------------


class TestListConfigurations:
    def test_empty_dir(self, tmp_path):
        config_dir = str(tmp_path / "configurations")
        os.makedirs(config_dir)
        assert list_configurations(config_dir) == []

    def test_dir_does_not_exist(self, tmp_path):
        config_dir = str(tmp_path / "nonexistent")
        assert list_configurations(config_dir) == []

    def test_returns_sorted_names(self, tmp_path):
        config_dir = str(tmp_path / "configurations")
        os.makedirs(config_dir)
        _write_config(os.path.join(config_dir, "zebra.json"), {"project_name": "zebra"})
        _write_config(os.path.join(config_dir, "alpha.json"), {"project_name": "alpha"})
        _write_config(os.path.join(config_dir, "mid.json"), {"project_name": "mid"})
        assert list_configurations(config_dir) == ["alpha", "mid", "zebra"]

    def test_ignores_non_json_files(self, tmp_path):
        config_dir = str(tmp_path / "configurations")
        os.makedirs(config_dir)
        _write_config(os.path.join(config_dir, "project.json"), {"project_name": "project"})
        # Create a non-json file
        with open(os.path.join(config_dir, "readme.txt"), "w") as f:
            f.write("not a config")
        assert list_configurations(config_dir) == ["project"]


# ---------------------------------------------------------------------------
# load_configuration
# ---------------------------------------------------------------------------


class TestLoadConfiguration:
    def test_loads_complete_config(self, tmp_path):
        config_path = str(tmp_path / "test.json")
        full_cfg = dict(DEFAULTS)
        full_cfg["email"] = "user@example.com"
        full_cfg["project_name"] = "test"
        _write_config(config_path, full_cfg)

        result = load_configuration(config_path)
        assert result["email"] == "user@example.com"
        assert result["conversion_type"] == "scos"

    def test_merges_missing_defaults(self, tmp_path):
        config_path = str(tmp_path / "partial.json")
        _write_config(config_path, {"email": "a@b.com", "project_name": "partial"})

        result = load_configuration(config_path)
        # Original keys preserved
        assert result["email"] == "a@b.com"
        # Defaults merged
        assert result["conversion_type"] == "scos"
        assert result["sql_flavor"] == "SparkSql"
        assert result["run_ewi_fixer"] == "yes"
        assert result["run_stage_conversion.stage_name"] == "migration_stage"

    def test_persists_after_merge(self, tmp_path):
        config_path = str(tmp_path / "sparse.json")
        _write_config(config_path, {"project_name": "sparse"})

        load_configuration(config_path)

        # Re-read from disk — defaults should be persisted
        on_disk = _read_config(config_path)
        for key, value in DEFAULTS.items():
            assert on_disk[key] == value

    def test_no_rewrite_when_complete(self, tmp_path):
        config_path = str(tmp_path / "complete.json")
        full_cfg = dict(DEFAULTS)
        full_cfg["project_name"] = "complete"
        _write_config(config_path, full_cfg)
        mtime_before = os.path.getmtime(config_path)

        # Small delay to detect mtime change
        import time
        time.sleep(0.05)

        load_configuration(config_path)
        mtime_after = os.path.getmtime(config_path)
        assert mtime_before == mtime_after

    def test_file_not_found(self, tmp_path):
        config_path = str(tmp_path / "missing.json")
        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            load_configuration(config_path)

    def test_does_not_overwrite_user_values(self, tmp_path):
        """If the user set a default-key to a non-default value, keep it."""
        config_path = str(tmp_path / "custom.json")
        _write_config(config_path, {
            "conversion_type": "snowpark_api",
            "sql_flavor": "HiveSql",
            "project_name": "custom",
        })

        result = load_configuration(config_path)
        assert result["conversion_type"] == "snowpark_api"
        assert result["sql_flavor"] == "HiveSql"


# ---------------------------------------------------------------------------
# create_configuration
# ---------------------------------------------------------------------------


class TestCreateConfiguration:
    def test_creates_dir_and_file(self, tmp_path):
        config_dir = str(tmp_path / "configurations")
        path, cfg = create_configuration(config_dir, "new_project")

        assert os.path.isdir(config_dir)
        assert os.path.isfile(path)
        assert path == get_config_path(config_dir, "new_project")

    def test_defaults_prepopulated(self, tmp_path):
        config_dir = str(tmp_path / "configurations")
        _, cfg = create_configuration(config_dir, "proj")

        for key, value in DEFAULTS.items():
            assert cfg[key] == value

    def test_project_name_in_config(self, tmp_path):
        config_dir = str(tmp_path / "configurations")
        _, cfg = create_configuration(config_dir, "my_proj")
        assert cfg["project_name"] == "my_proj"

    def test_persisted_to_disk(self, tmp_path):
        config_dir = str(tmp_path / "configurations")
        path, _ = create_configuration(config_dir, "disk_test")

        on_disk = _read_config(path)
        assert on_disk["project_name"] == "disk_test"
        for key, value in DEFAULTS.items():
            assert on_disk[key] == value

    def test_deterministic_output(self, tmp_path):
        """Keys in the file must be sorted (sort_keys=True)."""
        config_dir = str(tmp_path / "configurations")
        path, _ = create_configuration(config_dir, "ordered")

        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        on_disk = json.loads(raw)
        keys = list(on_disk.keys())
        assert keys == sorted(keys)

    def test_idempotent_dir(self, tmp_path):
        """Calling twice with same dir should not raise."""
        config_dir = str(tmp_path / "configurations")
        create_configuration(config_dir, "first")
        create_configuration(config_dir, "second")
        assert sorted(os.listdir(config_dir)) == ["first.json", "second.json"]

    def test_no_non_default_keys(self, tmp_path):
        """email, company, input_folder, output_folder, sma_cli_path are NOT created."""
        config_dir = str(tmp_path / "configurations")
        _, cfg = create_configuration(config_dir, "minimal")
        for key in ("email", "company", "input_folder", "output_folder", "sma_cli_path"):
            assert key not in cfg


# ---------------------------------------------------------------------------
# save_configuration
# ---------------------------------------------------------------------------


class TestSaveConfiguration:
    def test_merges_into_existing(self, tmp_path):
        config_path = str(tmp_path / "existing.json")
        _write_config(config_path, {"project_name": "existing", "email": "old@co.com"})

        result = save_configuration(config_path, {"email": "new@co.com"})
        assert result["email"] == "new@co.com"
        assert result["project_name"] == "existing"

    def test_creates_new_file(self, tmp_path):
        config_path = str(tmp_path / "brand_new.json")
        result = save_configuration(config_path, {"email": "a@b.com"})
        assert result["email"] == "a@b.com"
        assert os.path.isfile(config_path)

    def test_does_not_lose_existing_keys(self, tmp_path):
        config_path = str(tmp_path / "keep.json")
        _write_config(config_path, {
            "project_name": "keep",
            "email": "e@co.com",
            "conversion_type": "scos",
        })

        save_configuration(config_path, {"sma_cli_path": "/usr/bin/sma"})
        on_disk = _read_config(config_path)
        assert on_disk["project_name"] == "keep"
        assert on_disk["email"] == "e@co.com"
        assert on_disk["sma_cli_path"] == "/usr/bin/sma"

    def test_deterministic_output(self, tmp_path):
        config_path = str(tmp_path / "sorted.json")
        save_configuration(config_path, {"z_key": "z", "a_key": "a"})

        with open(config_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        on_disk = json.loads(raw)
        keys = list(on_disk.keys())
        assert keys == sorted(keys)

    def test_returns_full_config(self, tmp_path):
        config_path = str(tmp_path / "full.json")
        _write_config(config_path, {"project_name": "full", "email": "x@y.com"})

        result = save_configuration(config_path, {"company": "Acme"})
        assert result == {"project_name": "full", "email": "x@y.com", "company": "Acme"}


# ---------------------------------------------------------------------------
# DEFAULTS constant
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_has_eleven_keys(self):
        assert len(DEFAULTS) == 11

    def test_expected_keys(self):
        expected = {
            "conversion_type",
            "enable_jupyter_conversion",
            "generate_checkpoints",
            "migration_status",
            "run_dvp_orchestrator",
            "run_ewi_fixer",
            "run_ewi_fixer.ewi_comments",
            "run_ewi_fixer.ewi_scope",
            "run_stage_conversion",
            "run_stage_conversion.stage_name",
            "sql_flavor",
        }
        assert set(DEFAULTS.keys()) == expected

    def test_expected_values(self):
        assert DEFAULTS["conversion_type"] == "scos"
        assert DEFAULTS["migration_status"] == "migrate"
        assert DEFAULTS["sql_flavor"] == "SparkSql"
        assert DEFAULTS["run_ewi_fixer.ewi_scope"] == "only_pending"
        assert DEFAULTS["run_stage_conversion.stage_name"] == "migration_stage"


# ---------------------------------------------------------------------------
# CLI entry-point (_cli)
# ---------------------------------------------------------------------------


class TestCLI:
    def test_list_command(self, tmp_path, capsys):
        config_dir = str(tmp_path / "configurations")
        os.makedirs(config_dir)
        _write_config(os.path.join(config_dir, "proj_a.json"), {"project_name": "proj_a"})

        import sys
        orig_argv = sys.argv
        try:
            sys.argv = ["config_manager.py", "list", config_dir]
            config_manager._cli()
        finally:
            sys.argv = orig_argv

        captured = capsys.readouterr()
        assert json.loads(captured.out) == ["proj_a"]

    def test_create_command(self, tmp_path, capsys):
        config_dir = str(tmp_path / "configurations")

        import sys
        orig_argv = sys.argv
        try:
            sys.argv = ["config_manager.py", "create", config_dir, "new_proj"]
            config_manager._cli()
        finally:
            sys.argv = orig_argv

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["project_name"] == "new_proj"
        assert os.path.isfile(os.path.join(config_dir, "new_proj.json"))

    def test_load_command(self, tmp_path, capsys):
        config_path = str(tmp_path / "load_test.json")
        _write_config(config_path, {"project_name": "load_test"})

        import sys
        orig_argv = sys.argv
        try:
            sys.argv = ["config_manager.py", "load", config_path]
            config_manager._cli()
        finally:
            sys.argv = orig_argv

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["project_name"] == "load_test"
        # Defaults merged
        assert result["conversion_type"] == "scos"

    def test_save_command(self, tmp_path, capsys):
        config_path = str(tmp_path / "save_test.json")
        _write_config(config_path, {"project_name": "save_test"})

        updates = json.dumps({"email": "cli@test.com"})
        import sys
        orig_argv = sys.argv
        try:
            sys.argv = ["config_manager.py", "save", config_path, updates]
            config_manager._cli()
        finally:
            sys.argv = orig_argv

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["email"] == "cli@test.com"
        assert result["project_name"] == "save_test"

    def test_unknown_command(self, capsys):
        import sys
        orig_argv = sys.argv
        try:
            sys.argv = ["config_manager.py", "bogus"]
            with pytest.raises(SystemExit, match="1"):
                config_manager._cli()
        finally:
            sys.argv = orig_argv


# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------


class TestGetGlobalConfigPath:
    def test_returns_config_json(self, tmp_path):
        result = get_global_config_path(str(tmp_path))
        assert result == os.path.join(str(tmp_path), "config.json")

    def test_uses_global_config_name_constant(self, tmp_path):
        result = get_global_config_path(str(tmp_path))
        assert result.endswith(GLOBAL_CONFIG_NAME)


class TestLoadGlobal:
    def test_returns_empty_when_no_file(self, tmp_path):
        result = load_global(str(tmp_path))
        assert result == {}

    def test_loads_existing_global_config(self, tmp_path):
        cfg = {"sma_cli_path": "/usr/local/bin/sma"}
        path = os.path.join(str(tmp_path), "config.json")
        _write_config(path, cfg)
        result = load_global(str(tmp_path))
        assert result == cfg

    def test_preserves_extra_keys(self, tmp_path):
        cfg = {"sma_cli_path": "/usr/local/bin/sma", "future_key": "value"}
        path = os.path.join(str(tmp_path), "config.json")
        _write_config(path, cfg)
        result = load_global(str(tmp_path))
        assert result["future_key"] == "value"


class TestSaveGlobal:
    def test_creates_new_global_config(self, tmp_path):
        result = save_global(str(tmp_path), {"sma_cli_path": "/bin/sma"})
        assert result == {"sma_cli_path": "/bin/sma"}
        # Verify file was written
        path = os.path.join(str(tmp_path), "config.json")
        assert os.path.isfile(path)
        with open(path, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        assert on_disk == {"sma_cli_path": "/bin/sma"}

    def test_merges_with_existing(self, tmp_path):
        path = os.path.join(str(tmp_path), "config.json")
        _write_config(path, {"sma_cli_path": "/old/path"})
        result = save_global(str(tmp_path), {"sma_cli_path": "/new/path"})
        assert result["sma_cli_path"] == "/new/path"

    def test_preserves_existing_keys(self, tmp_path):
        path = os.path.join(str(tmp_path), "config.json")
        _write_config(path, {"sma_cli_path": "/bin/sma", "other": "keep"})
        result = save_global(str(tmp_path), {"sma_cli_path": "/new/sma"})
        assert result["other"] == "keep"
        assert result["sma_cli_path"] == "/new/sma"

    def test_deterministic_output(self, tmp_path):
        save_global(str(tmp_path), {"b_key": "2", "a_key": "1"})
        path = os.path.join(str(tmp_path), "config.json")
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        # sort_keys=True means a_key before b_key
        assert raw.index('"a_key"') < raw.index('"b_key"')
        # trailing newline
        assert raw.endswith("\n")


class TestCLIGlobal:
    def test_load_global_no_file(self, tmp_path, capsys):
        import sys
        orig_argv = sys.argv
        try:
            sys.argv = ["config_manager.py", "load-global", str(tmp_path)]
            config_manager._cli()
        finally:
            sys.argv = orig_argv
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {}

    def test_load_global_existing(self, tmp_path, capsys):
        import sys
        path = os.path.join(str(tmp_path), "config.json")
        _write_config(path, {"sma_cli_path": "/bin/sma"})
        orig_argv = sys.argv
        try:
            sys.argv = ["config_manager.py", "load-global", str(tmp_path)]
            config_manager._cli()
        finally:
            sys.argv = orig_argv
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["sma_cli_path"] == "/bin/sma"

    def test_save_global(self, tmp_path, capsys):
        import sys
        orig_argv = sys.argv
        try:
            sys.argv = [
                "config_manager.py",
                "save-global",
                str(tmp_path),
                '{"sma_cli_path": "/usr/bin/sma"}',
            ]
            config_manager._cli()
        finally:
            sys.argv = orig_argv
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["sma_cli_path"] == "/usr/bin/sma"
        # Verify file persisted
        path = os.path.join(str(tmp_path), "config.json")
        with open(path, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        assert on_disk["sma_cli_path"] == "/usr/bin/sma"
