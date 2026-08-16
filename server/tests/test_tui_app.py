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
from textual.widgets import Input, RichLog

from introspect.cron import CrontabIO
from introspect.tui.app import IntrospectApp, PanelDivider, linkify
from introspect.tui.webserver import PUBLIC_BIND_WARNING, WebServerManager
from tests.conftest import AGENT_HEX_ID, SESSION_UUID_1
from tests.test_cron import FakeRunner
from tests.test_tui_commands import VERBS, FakeWeb


def _fake_crontab(initial: str = "") -> CrontabIO:
    return CrontabIO(runner=FakeRunner(read_text=initial))


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
            # subagent content: needs the --agents widen flag under the chat-default sources
            app.query_one("#cmd", Input).value = "cormorant --agents"
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
            await _type_and_settle(pilot, inp, "/w")
            await pilot.press("tab")
            await pilot.pause()
            result["value"] = inp.value
            result["focused_is_input"] = app.focused is inp

    asyncio.run(scenario())
    assert result["value"] == "/web"
    assert result["focused_is_input"] is True  # Tab accepted, did NOT move focus


def test_app_right_accepts_completion_at_end(tmp_path: Path) -> None:
    result: dict[str, object] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd", Input)
            await _type_and_settle(pilot, inp, "/w")
            await pilot.press("right")
            await pilot.pause()
            result["value"] = inp.value

    asyncio.run(scenario())
    assert result["value"] == "/web"


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
        # A REAL WebServerManager, but a fake crontab so /status never reads the real one.
        app = IntrospectApp(db_path=tmp_path / "a.db", crontab=_fake_crontab())
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            inp = app.query_one("#cmd", Input)
            inp.value = "/web start"
            await pilot.press("enter")
            await pilot.pause()
            inp.value = "/status"
            await pilot.press("enter")
            await pilot.pause()
            app.stop_web()

    asyncio.run(scenario())
    assert any("serving at http://127.0.0.1:8765" in line for line in recorded)
    assert any("web server: running at http://127.0.0.1:8765" in line for line in recorded)
    assert any(line == "cron: not installed" for line in recorded)


def test_app_cron_status_through_running_app(tmp_path: Path) -> None:
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb(), crontab=_fake_crontab())
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app.query_one("#cmd", Input).value = "/cron"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert any(line == "cron: not installed" for line in recorded)


def test_app_start_web_port_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("introspect.tui.webserver.port_in_use", lambda host, port: True)
    recorded: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db")  # real manager, port always "taken"
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app.query_one("#cmd", Input).value = "/web start"
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
            app.query_one("#cmd", Input).value = "/web start public"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert PUBLIC_BIND_WARNING in recorded


# --- Clickable URLs in the log ------------------------------------------------------------


def test_linkify_line_without_url_passes_through_unstyled(tmp_path: Path) -> None:
    out = linkify("import: files=3 records=12 status=ok")
    assert out.plain == "import: files=3 records=12 status=ok"
    assert not any(getattr(span.style, "link", None) for span in out.spans)


def test_linkify_url_span_carries_copy_action_and_hyperlink(tmp_path: Path) -> None:
    line = "web: serving at http://127.0.0.1:8765 (bound 127.0.0.1:8765)"
    out = linkify(line)
    assert out.plain == line  # visible text unchanged, only styling added
    url_spans = [s for s in out.spans if getattr(s.style, "link", None)]
    assert len(url_spans) == 1
    span = url_spans[0]
    assert line[span.start : span.end] == "http://127.0.0.1:8765"
    assert span.style.link == "http://127.0.0.1:8765"  # OSC 8: cmd+click opens
    assert span.style.meta["@click"] == "app.copy_url('http://127.0.0.1:8765')"  # click copies


def test_linkify_url_at_end_of_line(tmp_path: Path) -> None:
    url = f"http://127.0.0.1:8765/s/{SESSION_UUID_1}"
    out = linkify(f"opening {url}")
    url_spans = [s for s in out.spans if getattr(s.style, "link", None)]
    assert len(url_spans) == 1
    assert url_spans[0].style.link == url


def test_app_action_copy_url_copies_and_confirms(tmp_path: Path) -> None:
    recorded: list[str] = []
    copied: list[str] = []

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test() as pilot:
            _spy_log(app, recorded)
            app._copy_text = copied.append  # type: ignore[method-assign]
            app.action_copy_url("http://127.0.0.1:8765")
            await pilot.pause()

    asyncio.run(scenario())
    assert copied == ["http://127.0.0.1:8765"]
    assert any("copied http://127.0.0.1:8765" in line for line in recorded)


def test_copy_text_uses_pbcopy_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls["argv"] = argv
        calls["input"] = kwargs.get("input")

    monkeypatch.setattr("introspect.tui.app.subprocess.run", fake_run)
    monkeypatch.setattr("introspect.tui.app.sys.platform", "darwin")
    app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
    app._copy_text("http://127.0.0.1:8765")
    assert calls["argv"] == ["pbcopy"]
    assert calls["input"] == b"http://127.0.0.1:8765"


def test_copy_text_falls_back_to_osc52_off_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("introspect.tui.app.sys.platform", "linux")
    app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
    osc52: list[str] = []
    monkeypatch.setattr(app, "copy_to_clipboard", osc52.append)
    app._copy_text("http://127.0.0.1:8765")
    assert osc52 == ["http://127.0.0.1:8765"]


# --- Panel resize (divider + hotkeys) -----------------------------------------------------


def test_app_resize_actions_change_log_height(tmp_path: Path) -> None:
    heights: dict[str, int] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test(size=(100, 40)) as pilot:
            log = app.query_one("#log", RichLog)
            heights["start"] = log.region.height
            app.action_grow_log()
            await pilot.pause()
            heights["grown"] = log.region.height
            app.action_shrink_log()
            app.action_shrink_log()
            await pilot.pause()
            heights["shrunk"] = log.region.height

    asyncio.run(scenario())
    assert heights["grown"] == heights["start"] + 1
    assert heights["shrunk"] == heights["grown"] - 2


def test_app_resize_respects_bounds(tmp_path: Path) -> None:
    sizes: dict[str, int] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test(size=(100, 40)) as pilot:
            for _ in range(50):
                app.action_shrink_log()
            await pilot.pause()
            sizes["log_min"] = app.query_one("#log", RichLog).region.height
            for _ in range(100):
                app.action_grow_log()
            await pilot.pause()
            sizes["log_max"] = app.query_one("#log", RichLog).region.height
            sizes["results_left"] = app.query_one("#results").region.height

    asyncio.run(scenario())
    assert sizes["log_min"] == 3  # never collapses to nothing
    assert sizes["results_left"] >= 5  # growing the log can't crush the results away


def test_app_alt_arrows_resize_log(tmp_path: Path) -> None:
    heights: dict[str, int] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test(size=(100, 40)) as pilot:
            log = app.query_one("#log", RichLog)
            heights["start"] = log.region.height
            await pilot.press("alt+up")
            heights["grown"] = log.region.height
            await pilot.press("alt+down")
            heights["back"] = log.region.height

    asyncio.run(scenario())
    assert heights["grown"] == heights["start"] + 1
    assert heights["back"] == heights["start"]


def test_app_divider_sits_between_results_and_log(tmp_path: Path) -> None:
    rows: dict[str, int] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            rows["results_bottom"] = app.query_one("#results").region.bottom
            divider = app.query_one(PanelDivider)
            rows["divider_y"] = divider.region.y
            rows["divider_h"] = divider.region.height
            rows["log_y"] = app.query_one("#log", RichLog).region.y

    asyncio.run(scenario())
    assert rows["divider_h"] == 1
    assert rows["results_bottom"] == rows["divider_y"]
    assert rows["log_y"] == rows["divider_y"] + 1


def test_app_set_log_height_clamps(tmp_path: Path) -> None:
    heights: dict[str, int] = {}

    async def scenario() -> None:
        app = IntrospectApp(db_path=tmp_path / "a.db", web=FakeWeb())
        async with app.run_test(size=(100, 40)) as pilot:
            app._set_log_height(7)
            await pilot.pause()
            heights["exact"] = app.query_one("#log", RichLog).region.height
            app._set_log_height(0)
            await pilot.pause()
            heights["low"] = app.query_one("#log", RichLog).region.height
            app._set_log_height(999)
            await pilot.pause()
            heights["high"] = app.query_one("#log", RichLog).region.height
            heights["results_left"] = app.query_one("#results").region.height

    asyncio.run(scenario())
    assert heights["exact"] == 7
    assert heights["low"] == 3
    assert heights["results_left"] >= 5
