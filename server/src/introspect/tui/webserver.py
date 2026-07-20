"""In-process web-server management for the TUI (§16).

Runs the FastAPI archive app under a :class:`uvicorn.Server` in a daemon thread -- the
established "``Server.run`` in a thread, ``should_exit`` to stop" pattern -- so ``/start-web``
keeps the TUI live while the browser reads the archive. ``/start-web`` binds 127.0.0.1:8765;
``/start-web public`` binds 0.0.0.0 (the caller prints :data:`PUBLIC_BIND_WARNING` first).
Before any start we probe the port (:func:`port_in_use`) and refuse cleanly if another process
already holds it.

No ``textual`` import here: the app module is the only textual importer, so this stays a plain
uvicorn wrapper (and cron's ``introspect import`` never reaches it).
"""

from __future__ import annotations

import enum
import socket
import threading
import time
from pathlib import Path

import uvicorn

from introspect.api import create_app

LOCAL_HOST = "127.0.0.1"
PUBLIC_HOST = "0.0.0.0"  # noqa: S104 -- binding all interfaces is the opt-in `public` behavior
DEFAULT_PORT = 8765

#: The mandatory, non-negotiable warning printed before a 0.0.0.0 bind (§16). The archive has
#: NO authentication, so a public bind makes every captured message readable by anyone on the
#: network. Keep this text alarming and explicit.
PUBLIC_BIND_WARNING = (
    "WARNING: binding to 0.0.0.0 exposes your ENTIRE archive to the whole network. There is "
    "NO authentication -- anyone who can reach this machine can read every captured message, "
    "including full conversation contents. Only do this on a network you fully trust."
)


class StartResult(enum.Enum):
    STARTED = "started"
    ALREADY_RUNNING = "already_running"
    PORT_IN_USE = "port_in_use"
    FAILED = "failed"


def port_in_use(host: str, port: int) -> bool:
    """True if ``port`` on ``host`` is already bound by an actively-listening process.

    The probe MUST mirror uvicorn's bind semantics: uvicorn sets ``SO_REUSEADDR`` before it
    binds, so a port that is merely in ``TIME_WAIT`` -- e.g. right after the user Ctrl-C'd a
    previous server on :8765 -- is still bindable by uvicorn. A probe WITHOUT ``SO_REUSEADDR``
    would fail on that ``TIME_WAIT`` socket and LIE that the port is taken, refusing
    ``/start-web`` while nothing is actually listening (the browser then gets connection
    refused). With ``SO_REUSEADDR`` the probe bind fails ONLY when another socket is actively
    LISTENING (sharing an active listener needs ``SO_REUSEPORT``, which neither we nor uvicorn
    set) -- exactly the outcome uvicorn's own bind would produce. ``host`` is the same interface
    the start will use (127.0.0.1, or 0.0.0.0 for public), so the probe matches the real bind.

    There is a small TOCTOU window between this probe and uvicorn's own bind; the manager treats
    a bind that fails despite a clear probe as :attr:`StartResult.FAILED` rather than crashing.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def session_url(base_url: str, session_uuid: str) -> str:
    return f"{base_url}/s/{session_uuid}"


def message_url(base_url: str, session_uuid: str, record_uuid: str) -> str:
    return f"{base_url}/s/{session_uuid}/m/{record_uuid}"


def agent_message_url(
    base_url: str, session_uuid: str, agent_hex_id: str, record_uuid: str
) -> str:
    return f"{base_url}/s/{session_uuid}/a/{agent_hex_id}/m/{record_uuid}"


def right_arrow_url(
    base_url: str,
    session_uuid: str,
    agent_hex_id: str | None,
    record_uuid: str | None,
) -> str:
    """The Right-arrow deep link, routed by the best hit's TRANSCRIPT IDENTITY.

    A hit in a subagent transcript gets the ``/a/{hex}/m/`` drill-in path; a main-transcript hit
    gets the plain ``/m/`` path. Linking a subagent record to the main-transcript ``/m/`` route
    makes the reader fetch the MAIN transcript with a foreign record uuid and 404 -- the exact
    cross-layer bug the Phase 3 walk caught in the web sidebar (see api.models HitOut). ``None``
    ``record_uuid`` (defensive: content hits always carry one) falls back to the session view.
    """
    if record_uuid is None:
        return session_url(base_url, session_uuid)
    if agent_hex_id is not None:
        return agent_message_url(base_url, session_uuid, agent_hex_id, record_uuid)
    return message_url(base_url, session_uuid, record_uuid)


class _ThreadedServer:
    """A :class:`uvicorn.Server` driven in a daemon thread; stopped via ``should_exit``."""

    def __init__(self, app, host: str, port: int) -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self, timeout: float = 5.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("web server did not start within the timeout")
            time.sleep(0.02)

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout)


class WebServerManager:
    """Owns the TUI's optional in-process web server (start/stop/state).

    A single manager instance lives on the app; because the TUI is the only thing that could
    have started it, stopping it on exit satisfies "stop any server the TUI started" (§16).
    """

    def __init__(self, db_path: Path, *, port: int = DEFAULT_PORT) -> None:
        self._db_path = Path(db_path)
        self._port = port
        self._server: _ThreadedServer | None = None
        self._host: str | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def host(self) -> str | None:
        return self._host

    def local_url(self) -> str:
        """The browsable localhost URL. Always valid to open: a 0.0.0.0 bind includes
        localhost, and the local host is where an auto-start lands, so the browser opens here
        regardless of the bound interface."""
        return f"http://{LOCAL_HOST}:{self._port}"

    def _make_server(self, host: str, port: int) -> _ThreadedServer:
        """Build the threaded server. A seam so tests can patch out the real uvicorn bind."""
        return _ThreadedServer(create_app(db_path=self._db_path), host, port)

    def start(self, host: str) -> StartResult:
        """Start the server on ``host``:``port`` unless already running or the port is taken."""
        if self.is_running:
            return StartResult.ALREADY_RUNNING
        if port_in_use(host, self._port):
            return StartResult.PORT_IN_USE
        try:
            server = self._make_server(host, self._port)
            server.start()
        except Exception:  # noqa: BLE001 -- a bind lost to the TOCTOU race, or startup failure
            return StartResult.FAILED
        self._server = server
        self._host = host
        return StartResult.STARTED

    def stop(self) -> bool:
        """Stop the server if running. Returns True iff a running server was stopped."""
        if self._server is None:
            return False
        self._server.stop()
        self._server = None
        self._host = None
        return True

    def ensure_started_local(self) -> bool:
        """Start the server on localhost if it isn't running. Returns True iff it just started.

        Used by the browser-open flow (§16: "auto-starts the web server on 127.0.0.1 if
        stopped -- a browser launch needs a server"). A foreign process already on the port
        yields a non-start; the caller still opens :meth:`local_url` and lets the browser report.
        """
        if self.is_running:
            return False
        return self.start(LOCAL_HOST) is StartResult.STARTED

    def describe(self) -> str:
        """One-line web-server state for ``/status``."""
        if not self.is_running:
            return "web server: stopped"
        return f"web server: running at {self.local_url()} (bound {self._host}:{self._port})"
