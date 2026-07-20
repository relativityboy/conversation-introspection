"""Web-server manager unit tests (§16): URL builders, port probe, and the start/stop state
machine -- with the real uvicorn bind patched out (per the brief: "patch the server object; do
NOT test uvicorn's internals")."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from introspect.tui.webserver import (
    DEFAULT_PORT,
    LOCAL_HOST,
    PUBLIC_BIND_WARNING,
    StartResult,
    WebServerManager,
    message_url,
    port_in_use,
    right_arrow_url,
    session_url,
)


class _FakeServer:
    """Duck-types _ThreadedServer: records start/stop, never binds a socket."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self, timeout: float = 5.0) -> None:
        self.started = True

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebServerManager:
    """A manager whose port is always free and whose server never really binds."""
    monkeypatch.setattr("introspect.tui.webserver.port_in_use", lambda host, port: False)
    monkeypatch.setattr(
        WebServerManager, "_make_server", lambda self, host, port: _FakeServer()
    )
    return WebServerManager(tmp_path / "archive.db")


def test_url_builders() -> None:
    base = "http://127.0.0.1:8765"
    assert session_url(base, "abc") == "http://127.0.0.1:8765/s/abc"
    assert message_url(base, "abc", "rec") == "http://127.0.0.1:8765/s/abc/m/rec"


def test_right_arrow_url_routes_by_transcript_identity() -> None:
    base = "http://127.0.0.1:8765"
    # main-transcript hit -> plain /m/ path
    assert right_arrow_url(base, "s1", None, "rec") == "http://127.0.0.1:8765/s/s1/m/rec"
    # subagent hit -> /a/{hex}/m/ drill-in path (so it never 404s the main transcript)
    assert (
        right_arrow_url(base, "s1", "abc123", "rec")
        == "http://127.0.0.1:8765/s/s1/a/abc123/m/rec"
    )
    # defensive: no record_uuid -> fall back to the session view
    assert right_arrow_url(base, "s1", None, None) == "http://127.0.0.1:8765/s/s1"


def test_port_in_use_true_when_bound() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((LOCAL_HOST, 0))
    sock.listen()
    port = sock.getsockname()[1]
    try:
        assert port_in_use(LOCAL_HOST, port) is True
    finally:
        sock.close()


def test_port_in_use_false_when_free() -> None:
    # Grab-then-release a port to obtain one nothing is listening on.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((LOCAL_HOST, 0))
    port = sock.getsockname()[1]
    sock.close()
    assert port_in_use(LOCAL_HOST, port) is False


def test_probe_enables_so_reuseaddr(monkeypatch: pytest.MonkeyPatch) -> None:
    # The probe must mirror uvicorn's bind: SO_REUSEADDR set before bind, else a port merely in
    # TIME_WAIT (post Ctrl-C) is falsely reported "in use". Assert the option is set.
    calls: list[tuple[int, int, int]] = []
    original = socket.socket.setsockopt

    def spy(self, level, optname, value, *rest):  # noqa: ANN001, ANN202
        calls.append((level, optname, value))
        return original(self, level, optname, value, *rest)

    monkeypatch.setattr(socket.socket, "setsockopt", spy)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((LOCAL_HOST, 0))
    port = sock.getsockname()[1]
    sock.close()

    port_in_use(LOCAL_HOST, port)
    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in calls


def test_start_refused_by_active_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A GENUINELY listening socket (not just TIME_WAIT) must still refuse the start -- SO_REUSEADDR
    # does not let a second socket bind an actively-listening port.
    monkeypatch.setattr(
        WebServerManager, "_make_server", lambda self, host, port: _FakeServer()
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((LOCAL_HOST, 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        mgr = WebServerManager(tmp_path / "archive.db", port=port)
        assert mgr.start(LOCAL_HOST) is StartResult.PORT_IN_USE
        assert mgr.is_running is False
    finally:
        listener.close()


def test_start_stop_state_machine(manager: WebServerManager) -> None:
    assert manager.is_running is False
    assert manager.describe() == "web server: stopped"

    assert manager.start(LOCAL_HOST) is StartResult.STARTED
    assert manager.is_running is True
    assert manager.local_url() == f"http://127.0.0.1:{DEFAULT_PORT}"
    assert "running" in manager.describe()

    # Second start is a no-op reported as already-running.
    assert manager.start(LOCAL_HOST) is StartResult.ALREADY_RUNNING

    assert manager.stop() is True
    assert manager.is_running is False
    assert manager.stop() is False  # nothing to stop the second time


def test_start_refuses_when_port_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("introspect.tui.webserver.port_in_use", lambda host, port: True)
    mgr = WebServerManager(tmp_path / "archive.db")
    assert mgr.start(LOCAL_HOST) is StartResult.PORT_IN_USE
    assert mgr.is_running is False


def test_ensure_started_local_starts_once(manager: WebServerManager) -> None:
    assert manager.ensure_started_local() is True  # was stopped -> just started
    assert manager.is_running is True
    assert manager.ensure_started_local() is False  # already running -> no-op


def test_public_bind_warning_is_alarming() -> None:
    lowered = PUBLIC_BIND_WARNING.lower()
    assert "0.0.0.0" in PUBLIC_BIND_WARNING
    assert "no authentication" in lowered
    assert "network" in lowered
