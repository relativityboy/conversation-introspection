"""``introspect session-name`` (2026-08-25): the /session-name skill's logic as a tested command.

The skill used to narrate this flow in markdown -- read an env var, curl twice, map a status
code -- and a model executing it cost thousands of tokens and tens of seconds. Now the skill's
``!`` block runs this command BEFORE the model turn and the model only relays one line. All
the branches the markdown described live here, against the real endpoint via a TestClient-
backed transport (never a live server, never the real archive).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from introspect import cli, session_name
from introspect.api import create_app
from introspect.session_name import Outcome, ServerUnreachable, name_session
from tests.conftest import SESSION_UUID_1

BASE = "http://archive.test"


def _client_transport(client: TestClient):
    """Adapter: the module's (method, url, body) -> (status, body) contract over a TestClient."""

    def transport(method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
        assert url.startswith(BASE)
        headers = {"content-type": "application/json"} if body is not None else {}
        resp = client.request(method, url[len(BASE) :], content=body, headers=headers)
        return resp.status_code, resp.content

    return transport


@pytest.fixture
def uncaptured(tmp_path: Path, fixture_tree: Path) -> TestClient:
    """Empty archive over the fixture tree: the session exists on disk, not in the DB yet."""
    return TestClient(create_app(db_path=tmp_path / "archive.db", source_root=fixture_tree))


# --- the happy path is the import-then-title path ----------------------------------------


def test_names_an_uncaptured_session_by_importing_first(uncaptured: TestClient) -> None:
    out = name_session(
        "the day we named sessions",
        SESSION_UUID_1,
        base_url=BASE,
        transport=_client_transport(uncaptured),
    )
    assert out == Outcome(
        ok=True, message='Named this session "the day we named sessions" in the archive.'
    )
    detail = uncaptured.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()
    assert detail["user_title"] == "the day we named sessions"


def test_name_is_stripped_and_quotes_survive(uncaptured: TestClient) -> None:
    out = name_session(
        '  the "quoted" day, it\'s fine  \n',
        SESSION_UUID_1,
        base_url=BASE,
        transport=_client_transport(uncaptured),
    )
    assert out.ok
    detail = uncaptured.get(f"/api/v1/sessions/{SESSION_UUID_1}").json()
    assert detail["user_title"] == 'the "quoted" day, it\'s fine'


# --- refusals before any request ----------------------------------------------------------


def test_empty_name_is_refused_without_a_request() -> None:
    def never(method, url, body):  # noqa: ANN001
        raise AssertionError("no request expected")

    out = name_session("   ", SESSION_UUID_1, base_url=BASE, transport=never)
    assert out == Outcome(ok=False, message="session-name: no name given")


def test_missing_session_uuid_is_refused_without_a_request() -> None:
    def never(method, url, body):  # noqa: ANN001
        raise AssertionError("no request expected")

    out = name_session("x", None, base_url=BASE, transport=never)
    assert not out.ok
    assert "CLAUDE_CODE_SESSION_ID" in out.message


# --- server state -------------------------------------------------------------------------


def test_server_down_names_the_start_command() -> None:
    def refused(method, url, body):  # noqa: ANN001
        raise ServerUnreachable("connection refused")

    out = name_session(
        "x", SESSION_UUID_1, base_url=BASE, transport=refused, server_dir=Path("/x/server")
    )
    assert not out.ok
    assert "not running" in out.message
    assert "cd /x/server && uv run introspect serve" in out.message


def test_server_older_than_1_8_is_refused_before_the_put() -> None:
    calls: list[str] = []

    def old(method, url, body):  # noqa: ANN001
        calls.append(method)
        return 200, b'{"version": "1.7.0"}'

    out = name_session("x", SESSION_UUID_1, base_url=BASE, transport=old)
    assert not out.ok
    assert "1.7.0" in out.message and "1.8.0" in out.message and "restart" in out.message
    assert calls == ["GET"]


# --- endpoint outcomes relayed as one line each -------------------------------------------


def test_404_after_import_relays_the_server_detail(uncaptured: TestClient) -> None:
    out = name_session(
        "x",
        "99999999-9999-9999-9999-999999999999",
        base_url=BASE,
        transport=_client_transport(uncaptured),
    )
    assert not out.ok
    assert "after import run" in out.message
    assert "excluded" in out.message


def test_422_too_long_relays_the_limit(uncaptured: TestClient) -> None:
    out = name_session(
        "x" * 201, SESSION_UUID_1, base_url=BASE, transport=_client_transport(uncaptured)
    )
    assert not out.ok
    assert "200" in out.message


def test_409_lock_says_try_again() -> None:
    def busy(method, url, body):  # noqa: ANN001
        if method == "GET":
            return 200, b'{"version": "1.8.0"}'
        return 409, b'{"status":409,"title":"import already running","detail":"held"}'

    out = name_session("x", SESSION_UUID_1, base_url=BASE, transport=busy)
    assert not out.ok
    assert "try again" in out.message


def test_unexpected_status_is_relayed_verbatim() -> None:
    def weird(method, url, body):  # noqa: ANN001
        if method == "GET":
            return 200, b'{"version": "1.8.0"}'
        return 500, b'{"status":500,"title":"Internal Server Error","detail":"boom"}'

    out = name_session("x", SESSION_UUID_1, base_url=BASE, transport=weird)
    assert not out.ok
    assert "500" in out.message and "boom" in out.message


# --- CLI wiring: env default, --stdin, stdout-always, exit codes ---------------------------


def test_cli_session_name_defaults_session_from_env(
    uncaptured: TestClient, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION_UUID_1)
    monkeypatch.setattr(session_name, "urllib_transport", _client_transport(uncaptured))
    rc = cli.main(["session-name", "--url", BASE, "named from argv"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == 'Named this session "named from argv" in the archive.'


def test_cli_session_name_reads_stdin_and_failures_go_to_stdout(
    uncaptured: TestClient, capsys, monkeypatch
) -> None:
    # The skill's ``!`` block feeds the name on stdin (a quoted heredoc: any characters are
    # safe) and captures STDOUT -- so a failure line must land there too, or the user never
    # sees why nothing happened.
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("a name from stdin\n"))
    monkeypatch.setattr(session_name, "urllib_transport", _client_transport(uncaptured))
    rc = cli.main(["session-name", "--url", BASE, "--stdin"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "CLAUDE_CODE_SESSION_ID" in captured.out
    assert captured.err == ""
