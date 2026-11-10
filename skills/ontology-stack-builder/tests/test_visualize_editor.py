"""The ontology editor saves safely and only serves on localhost.

Run:  uv run --extra dev pytest tests/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import visualize_ontology as vo  # noqa: E402


@pytest.mark.parametrize("name", ["Customer", "_x", "order_line_2"])
def test_identifier_names_are_accepted(name):
    assert vo._invalid_name_error("Class", name) is None


@pytest.mark.parametrize("name", ["V AS SELECT 1; GRANT", "2nd", "a-b", "a.b", 'x"y', ""])
def test_non_identifier_names_are_refused(name):
    assert "must start with a letter" in vo._invalid_name_error("Relation", name)


@pytest.mark.parametrize("address,ok", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("[::1]", True),
    ("0.0.0.0", False), ("", False), (None, False), ("10.0.0.5", False),
])
def test_is_loopback(address, ok):
    assert vo._is_loopback(address) is ok


def test_save_writes_the_cli_paths_atomically(tmp_path, monkeypatch):
    classes_path, relations_path = tmp_path / "classes.json", tmp_path / "relations.json"
    classes_path.write_text("[]")
    monkeypatch.setattr(vo.st, "session_state", {
        "classes": [{"name": "A"}], "relations": [{"name": "r"}],
        # A stale or injected session value must not redirect the write.
        "classes_path": str(tmp_path / "elsewhere.json"),
        "has_unsaved_changes": True,
    })
    vo._save_to_disk(str(classes_path), str(relations_path))
    assert json.loads(classes_path.read_text()) == [{"name": "A"}]
    assert json.loads(relations_path.read_text()) == [{"name": "r"}]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["classes.json", "relations.json"]
    assert vo.st.session_state["has_unsaved_changes"] is False


def test_failed_write_keeps_both_old_files(tmp_path):
    classes, relations = tmp_path / "classes.json", tmp_path / "relations.json"
    classes.write_text('[{"name": "old"}]')
    relations.write_text("[]")
    with pytest.raises(TypeError):
        vo._write_json_atomic({str(classes): [{"name": "new"}],
                               str(relations): [{"name": object()}]})
    assert json.loads(classes.read_text()) == [{"name": "old"}]
    assert json.loads(relations.read_text()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["classes.json", "relations.json"]


def test_write_keeps_mode_and_symlink(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("[]")
    real.chmod(0o644)
    link = tmp_path / "classes.json"
    link.symlink_to(real)
    vo._write_json_atomic({str(link): [{"name": "A"}]})
    assert link.is_symlink()
    assert json.loads(real.read_text()) == [{"name": "A"}]
    assert real.stat().st_mode & 0o777 == 0o644


def test_launcher_binds_localhost_unless_allowed(monkeypatch):
    calls = []
    monkeypatch.setattr(vo.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    forwarded = ["--classes-json", "c.json", "--relations-json", "r.json"]
    assert vo._launch_streamlit("8599", False, forwarded) == 0
    assert vo._launch_streamlit("8599", True, forwarded) == 0
    local, remote = calls
    assert local[local.index("--server.address") + 1] == "127.0.0.1"
    assert remote[remote.index("--server.address") + 1] == "0.0.0.0"
    assert local[local.index("--server.port") + 1] == "8599"
    assert local[local.index("--") + 1:] == forwarded


def test_app_refuses_to_render_on_a_public_address(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    class Stopped(Exception):
        pass

    for name in ("c.json", "r.json"):
        (tmp_path / name).write_text("[]")
    fake = MagicMock()
    fake.runtime.exists.return_value = True
    fake.get_option.return_value = None  # `streamlit run` default: every interface
    fake.stop.side_effect = Stopped
    monkeypatch.setattr(vo, "st", fake)
    monkeypatch.setattr(sys, "argv", ["x", "--", "--classes-json", str(tmp_path / "c.json"),
                                      "--relations-json", str(tmp_path / "r.json")])
    with pytest.raises(Stopped):
        vo.main()
    fake.error.assert_called_once()
    fake.sidebar.header.assert_not_called()
