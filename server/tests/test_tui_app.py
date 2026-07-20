"""Textual app tests (§16) via App.run_test() + Pilot.

Run through ``asyncio.run`` so no async-test plugin is needed. These cover the wiring the pure
unit tests can't: Input.Submitted routing to command vs. search, results navigation, and the
browser-open gestures (Enter -> session, Right -> best message) with ``webbrowser.open`` and the
web server both patched out."""

from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from textual.widgets import Input

from introspect.tui.app import IntrospectApp
from introspect.tui.webserver import PUBLIC_BIND_WARNING, WebServerManager
from tests.conftest import AGENT_HEX_ID, SESSION_UUID_1
from tests.test_tui_commands import VERBS, FakeWeb


class _FakeServer:
    def start(self, timeout: float = 5.0) -> None: ...
    def stop(self, timeout: float = 5.0) -> None: ...


def _spy_log(app: IntrospectApp, sink: list[str]) -> None:
    original = app._append_log

    def spy(line: str) -> None:
        sink.append(line)
        original(line)

    app._append_log = spy  # type: ignore[method-assign]


def _db_path_of(indexed_fixture: Session) -> Path:
    return Path(indexed_fixture.get_bind().url.database)


# --- Input routing / help -----------------------------------------------------------------


def test_app_help_lists_every_verb(tmp_path: Path) -> None:
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app.query_one("#cmd", Input).value = "/help"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    blob = "\n".join(recorded)
    for verb in VERBS:
        assert f"/{verb}" in blob


def test_app_unknown_command(tmp_path: Path) -> None:
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app.query_one("#cmd", Input).value = "/nope"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert any("unknown command" in line for line in recorded)


# --- Search + browser-open gestures -------------------------------------------------------


def test_app_search_populates_results(indexed_fixture: Session) -> None:
    dbp = _db_path_of(indexed_fixture)
    counts: dict[str, int] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=dbp, web=FakeWeb())
        async with app.run_test() as pilot:
            app.query_one("#cmd", Input).value = "horizon"
            await pilot.press("enter")
            await pilot.pause()
            counts["n"] = len(app._results)

    asyncio.run(scenario())
    assert counts["n"] == 1


def test_app_enter_opens_message_url(
    indexed_fixture: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §16 amendment 2026-07-20: Enter now opens the SAME best-hit message deep-link as Right
    # (one destination, two keys) -- no longer the bare session URL.
    dbp = _db_path_of(indexed_fixture)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))

    async def scenario() -> None:
        app = IntrospectApp(db_path=dbp, web=FakeWeb())
        async with app.run_test() as pilot:
            app.query_one("#cmd", Input).value = "horizon"
            await pilot.press("enter")  # runs the search, focus moves to results
            await pilot.pause()
            await pilot.press("enter")  # Enter on the highlighted result
            await pilot.pause()

    asyncio.run(scenario())
    assert len(opened) == 1
    assert opened[0].startswith(f"http://127.0.0.1:8765/s/{SESSION_UUID_1}/m/")


def test_app_right_opens_message_url(
    indexed_fixture: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    dbp = _db_path_of(indexed_fixture)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))

    async def scenario() -> None:
        app = IntrospectApp(db_path=dbp, web=FakeWeb())
        async with app.run_test() as pilot:
            app.query_one("#cmd", Input).value = "horizon"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("right")  # Right on the highlighted result
            await pilot.pause()

    asyncio.run(scenario())
    assert len(opened) == 1
    assert opened[0].startswith(f"http://127.0.0.1:8765/s/{SESSION_UUID_1}/m/")


def test_app_right_opens_subagent_message_url(
    indexed_fixture: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A best hit in the subagent transcript must open the /a/{hex}/m/ drill-in, not /m/.
    dbp = _db_path_of(indexed_fixture)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))

    async def scenario() -> None:
        app = IntrospectApp(db_path=dbp, web=FakeWeb())
        async with app.run_test() as pilot:
            app.query_one("#cmd", Input).value = "cormorant"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()

    asyncio.run(scenario())
    assert len(opened) == 1
    assert opened[0].startswith(
        f"http://127.0.0.1:8765/s/{SESSION_UUID_1}/a/{AGENT_HEX_ID}/m/"
    )


def test_app_search_no_results_logs_notice(indexed_fixture: Session) -> None:
    dbp = _db_path_of(indexed_fixture)
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=dbp, web=FakeWeb())
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app.query_one("#cmd", Input).value = "zzzznotarealword"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert any("no results" in line for line in recorded)


# --- Command autocomplete acceptance (Tab / Right) ----------------------------------------


async def _type_and_settle(pilot, inp, text: str) -> None:
    inp.focus()
    await pilot.pause()
    for ch in text:
        await pilot.press(ch)
    # the suggester resolves asynchronously; pump until it lands (or give up)
    for _ in range(8):
        await pilot.pause()
        if inp._suggestion:
            break


def test_app_tab_accepts_completion(tmp_path: Path) -> None:
    result: dict[str, object] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd", Input)
            await _type_and_settle(pilot, inp, "/start-")
            await pilot.press("tab")
            await pilot.pause()
            result["value"] = inp.value
            result["focused_is_input"] = app.focused is inp

    asyncio.run(scenario())
    assert result["value"] == "/start-web"
    assert result["focused_is_input"] is True  # Tab accepted, did NOT move focus


def test_app_right_accepts_completion_at_end(tmp_path: Path) -> None:
    result: dict[str, object] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd", Input)
            await _type_and_settle(pilot, inp, "/start-")
            await pilot.press("right")
            await pilot.pause()
            result["value"] = inp.value

    asyncio.run(scenario())
    assert result["value"] == "/start-web"


def test_app_tab_without_suggestion_moves_focus(tmp_path: Path) -> None:
    result: dict[str, object] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd", Input)
            await _type_and_settle(pilot, inp, "zzz")  # nothing completes "zzz"
            result["suggestion"] = inp._suggestion
            await pilot.press("tab")
            await pilot.pause()
            result["focused_is_input"] = app.focused is inp

    asyncio.run(scenario())
    assert not result["suggestion"]  # no completion showing
    assert result["focused_is_input"] is False  # normal Tab focus behavior preserved


# --- Web management through the running app ------------------------------------------------


def test_app_start_web_then_status_roundtrips_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("introspect.tui.webserver.port_in_use", lambda host, port: False)
    monkeypatch.setattr(
        WebServerManager, "_make_server", lambda self, host, port: _FakeServer()
    )
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db")  # a REAL WebServerManager
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            inp = app.query_one("#cmd", Input)
            inp.value = "/start-web"
            await pilot.press("enter")
            await pilot.pause()
            inp.value = "/status"
            await pilot.press("enter")
            await pilot.pause()
            app.stop_web()

    asyncio.run(scenario())
    assert any("serving at http://127.0.0.1:8765" in line for line in recorded)
    assert any("web server: running at http://127.0.0.1:8765" in line for line in recorded)


def test_app_start_web_port_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("introspect.tui.webserver.port_in_use", lambda host, port: True)
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db")  # real manager, port always "taken"
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app.query_one("#cmd", Input).value = "/start-web"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert any("already in use" in line for line in recorded)


def test_app_public_bind_warning_present(tmp_path: Path) -> None:
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app.query_one("#cmd", Input).value = "/start-web public"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert PUBLIC_BIND_WARNING in recorded
