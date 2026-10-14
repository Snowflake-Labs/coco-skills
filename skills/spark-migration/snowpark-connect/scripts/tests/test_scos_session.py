from __future__ import annotations

import pytest

import scos_session


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeBuilder:
    """Records whether ``.config('connection_name', ...)`` was pinned."""

    def __init__(self):
        self.pinned = None

    def config(self, key, value):
        if key == "connection_name":
            self.pinned = value
        return self

    def create(self):
        return _FakeSession()


def _install_fake_builder(monkeypatch):
    builder = _FakeBuilder()
    fake_session_ns = type("_FakeSessionNS", (), {"builder": builder})
    monkeypatch.setattr(scos_session, "Session", fake_session_ns)
    return builder


@pytest.mark.parametrize("conn", [None, "", "default"])
def test_open_session_does_not_pin_for_default(monkeypatch, conn):
    builder = _install_fake_builder(monkeypatch)
    scos_session.open_session(conn)
    assert builder.pinned is None


def test_open_session_pins_explicit_connection(monkeypatch):
    builder = _install_fake_builder(monkeypatch)
    scos_session.open_session("myconn")
    assert builder.pinned == "myconn"
