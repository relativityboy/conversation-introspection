"""``introspect session-name`` -- name the CURRENT Claude session in the archive (2026-08-25).

The ``/session-name`` skill runs this BEFORE the model turn (a ``!`` block in its SKILL.md), so
the whole flow -- identify the session, check the server is up and new enough, one PUT with
``import_if_missing`` -- costs ~500ms of shell instead of a model narrating curl calls. The
skill relays whatever single line this prints; every branch below therefore ends in exactly
one human-readable line, and the CLI prints it on STDOUT whether or not it succeeded (the
``!`` capture only sees stdout).

Pure module: the HTTP transport is injected so tests drive the real endpoint through a
TestClient adapter and fake the server-down / old-version cases -- no live server, no real
archive, ever.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

DEFAULT_URL = "http://127.0.0.1:8765"
# ``import_if_missing`` arrived in 1.8.0; older servers silently ignore the flag and would
# 404 an uncaptured session (or 204 a captured one while claiming nothing) -- refuse them.
MIN_SERVER_VERSION = (1, 8, 0)
_TIMEOUT_SECONDS = 180.0  # the PUT may run an import inside the request (titles.py)


class ServerUnreachable(Exception):
    """No server answered at all (connection refused, DNS, timeout)."""


Transport = Callable[[str, str, bytes | None], tuple[int, bytes]]
"""``(method, url, body) -> (status, body)``; raises :class:`ServerUnreachable` when nothing
answers. HTTP error statuses are returned, not raised."""


def urllib_transport(method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
    headers = {"content-type": "application/json"} if body is not None else {}
    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310 -- local API
            return resp.status, resp.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ServerUnreachable(str(exc)) from exc


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:  # "unknown" (no changelog found) counts as too old
        return (0,)


def _detail(body: bytes) -> str:
    try:
        return str(json.loads(body).get("detail", "")) or body.decode(errors="replace")
    except (ValueError, AttributeError):
        return body.decode(errors="replace")


def name_session(
    name: str,
    session_uuid: str | None,
    *,
    base_url: str = DEFAULT_URL,
    transport: Transport = urllib_transport,
    server_dir: Path | None = None,
) -> Outcome:
    name = name.strip()
    if not name:
        return Outcome(False, "session-name: no name given")
    if not session_uuid:
        return Outcome(
            False,
            "session-name: cannot identify this session (CLAUDE_CODE_SESSION_ID is not set)",
        )

    try:
        _status, body = transport("GET", f"{base_url}/api/v1/status", None)
    except ServerUnreachable:
        where = f"cd {server_dir} && " if server_dir is not None else ""
        return Outcome(
            False,
            f"session-name: archive server is not running at {base_url} -- start it with "
            f"`{where}uv run introspect serve` or the TUI's /web start",
        )
    version = str(json.loads(body).get("version", "unknown"))
    if _version_tuple(version) < MIN_SERVER_VERSION:
        return Outcome(
            False,
            f"session-name: server at {base_url} is {version}; naming needs 1.8.0+ -- "
            "restart it on current code",
        )

    status, body = transport(
        "PUT",
        f"{base_url}/api/v1/sessions/{session_uuid}/title?import_if_missing=true",
        json.dumps({"title": name}).encode(),
    )
    if status == 204:
        return Outcome(True, f'Named this session "{name}" in the archive.')
    if status == 404:
        return Outcome(
            False, f"session-name: not in the archive even after import -- {_detail(body)}"
        )
    if status == 409:
        return Outcome(
            False,
            "session-name: an import held the archive lock the whole time the server "
            "waited; try again in a minute",
        )
    if status == 422:
        return Outcome(False, f"session-name: {_detail(body)}")
    return Outcome(False, f"session-name: server answered {status}: {_detail(body)}")
