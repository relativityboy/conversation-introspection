"""The Textual application for ``introspect tui`` (§16, step 1).

This is the ONLY module in the package that imports ``textual``; everything it drives -- the
command registry/handlers, the in-process search, the web-server manager -- is textual-free and
independently unit-tested. The CLI imports :func:`run_tui` lazily so cron never pays this cost.

Surface: a bottom command :class:`~textual.widgets.Input` with as-you-type slash-command
autocomplete; a results :class:`OptionList` (default, non-slash input runs an archive search);
and a :class:`~textual.widgets.RichLog` log area where command output and progress stream. Enter
and Right both open the highlighted result's best-matching message in the browser (§16 amendment
2026-07-20: "one destination, two keys"), auto-starting the local web server if it is stopped.
Esc clears; Ctrl-C / ``/quit`` exit, stopping any web server the TUI started.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.suggester import SuggestFromList
from textual.widgets import Footer, Input, OptionList, RichLog
from textual.widgets.option_list import Option

from introspect import changelog, config
from introspect.cron import CrontabIO
from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.tui.commands import Command, CommandContext, build_registry, parse_command
from introspect.tui.search import SearchResultRow, search_sessions
from introspect.tui.webserver import (
    WebServerManager,
    right_arrow_url,
)


class CommandInput(Input):
    """The command bar. Adds Tab = accept the autocomplete suggestion.

    Textual's default Tab is a Screen-level ``app.focus_next`` binding, so without this a
    showing inline hint could never be accepted with Tab -- focus would jump off the bar (the
    exact "completion not acceptable" bug from hand-testing). A widget-level ``tab`` binding on
    the focused Input intercepts that Screen binding. Right-arrow at end already accepts via
    ``Input.action_cursor_right``; Tab now matches that convention. With NO suggestion showing,
    Tab falls back to normal focus navigation.
    """

    BINDINGS = [Binding("tab", "accept_or_focus_next", "Accept completion", show=False)]

    def action_accept_or_focus_next(self) -> None:
        if self.cursor_at_end and self._suggestion:
            self.value = self._suggestion
            self.cursor_position = len(self.value)
        else:
            self.app.action_focus_next()


class ResultsList(OptionList):
    """The search results list. Adds Right = open the highlighted hit's message deep-link.

    Up/Down/Enter come from :class:`OptionList` (Enter -> ``OptionSelected``, handled by the app
    as "open the best-hit message" -- the same destination as Right per the §16 amendment); Right
    is the explicit §16 gesture for that message-level deep link.
    """

    BINDINGS = [Binding("right", "open_message", "Open message", show=False)]

    def action_open_message(self) -> None:
        self.app.open_selected_message()  # type: ignore[attr-defined]


class IntrospectApp(App):
    """Search-and-command TUI over one archive DB."""

    TITLE = "introspect"
    SUB_TITLE = "archive reading room"

    CSS = """
    Screen { background: #0B1220; }
    #results {
        height: 1fr;
        border: round #24344f;
        background: #111B2E;
        color: #E7ECF4;
    }
    #results > .option-list--option-highlighted {
        background: #24344f;
        color: #E5A54B;
    }
    #log {
        height: 12;
        border: round #24344f;
        background: #0e1626;
        color: #cdd6e6;
    }
    #cmd { border: round #6FD3C7; }
    """

    BINDINGS = [Binding("escape", "clear", "Clear")]

    def __init__(
        self,
        *,
        db_path: Path | str,
        source_root: Path | str | None = None,
        web: WebServerManager | None = None,
        crontab: CrontabIO | None = None,
        session_factory=None,  # noqa: ANN001 -- a sqlalchemy sessionmaker; injected in tests
    ) -> None:
        super().__init__()
        self._db_path = Path(db_path)
        self._source_root = Path(source_root) if source_root is not None else config.source_root()
        if session_factory is None:
            engine = get_engine(self._db_path)
            upgrade_to_head(engine)
            session_factory = _session_factory_for(engine)
        self._session_factory = session_factory
        self._web = web if web is not None else WebServerManager(self._db_path)
        self._crontab = crontab if crontab is not None else CrontabIO()
        self._command_registry = build_registry()
        self._results: list[SearchResultRow] = []

    # --- Layout --------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield ResultsList(id="results")
        yield RichLog(id="log", markup=False, wrap=True, highlight=False)
        yield CommandInput(
            id="cmd",
            placeholder="type to search the archive, or /help for commands",
            suggester=SuggestFromList(self._command_registry.slash_names(), case_sensitive=False),
        )
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        version = changelog.app_version()
        label = f"v{version}" if version != "unknown" else "(version unknown)"
        log.write(f"introspect {label} -- type to search, /help for commands, /quit to exit")
        self.query_one("#cmd", Input).focus()

    # --- Input routing -------------------------------------------------------------------

    @on(Input.Submitted, "#cmd")
    def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value
        self.query_one("#cmd", Input).value = ""
        if not text.strip():
            return
        if text.strip().startswith("/"):
            self._run_command(text)
        else:
            self._run_search(text)

    def _run_command(self, text: str) -> None:
        name, args = parse_command(text)
        command = self._command_registry.get(name)
        if command is None:
            self._append_log(f"unknown command: /{name} -- type /help")
            return
        if command.background:
            self._run_background(command, args)
        else:
            command.handler(self._make_ctx(self._append_log), args)

    @work(thread=True)
    def _run_background(self, command: Command, args: list[str]) -> None:
        # Long-running verbs run off the UI thread; emit marshals each line back onto it so the
        # log updates live while the worker runs (§16: "UI stays live").
        ctx = self._make_ctx(lambda line: self.call_from_thread(self._append_log, line))
        command.handler(ctx, args)

    def _make_ctx(self, emit) -> CommandContext:  # noqa: ANN001 -- emit: Callable[[str], None]
        return CommandContext(
            db_path=self._db_path,
            source_root=self._source_root,
            session_factory=self._session_factory,
            web=self._web,
            emit=emit,
            exit=self.exit,
            registry=self._command_registry,
            crontab=self._crontab,
        )

    # --- Search --------------------------------------------------------------------------

    def _run_search(self, query: str) -> None:
        with self._session_factory() as db:
            self._results = search_sessions(db, query)
        results_list = self.query_one("#results", ResultsList)
        results_list.clear_options()
        if not self._results:
            self._append_log(f'no results for "{query}"')
            return
        results_list.add_options(
            [Option(self._format_row(row), id=row.session_uuid) for row in self._results]
        )
        results_list.highlighted = 0
        results_list.focus()
        self._append_log(f'{len(self._results)} result(s) for "{query}" -- '
                         "Enter or Right opens the best-hit message")

    def _format_row(self, row: SearchResultRow) -> Text:
        # Build with Text.append (never markup parsing) so untrusted archive content -- titles,
        # slugs, snippets -- can never be interpreted as Rich markup or console control.
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(row.title, style="bold #E5A54B")
        text.append("  ·  ")
        text.append(row.project_slug, style="#6FD3C7")
        if row.date is not None:
            text.append("  ·  ")
            text.append(row.date.strftime("%Y-%m-%d"), style="#7f8db0")
        if row.snippet:
            text.append("  ·  ")
            text.append(row.snippet, style="#9aa7c4")
        return text

    # --- Result actions (open in browser) ------------------------------------------------

    @on(OptionList.OptionSelected, "#results")
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Enter (or click) on a result opens the best-hit message deep-link -- the same
        # destination as Right (§16 amendment 2026-07-20: "one destination, two keys").
        self._open_message(event.option_index)

    def open_selected_message(self) -> None:
        """Right gesture: open the highlighted result's best-matching message."""
        self._open_message(self.query_one("#results", ResultsList).highlighted)

    def _open_message(self, index: int | None) -> None:
        """Open the result at ``index`` at its best-matching message.

        The URL is routed by the hit's transcript identity -- a subagent hit opens the
        /a/{hex}/m/ drill-in, never the main-transcript /m/ that would 404 (see
        :func:`introspect.tui.webserver.right_arrow_url`). Shared by Enter and Right.
        """
        row = self._row_at(index)
        if row is None:
            return
        base = self._ensure_web_for_open()
        self._open_url(
            right_arrow_url(base, row.session_uuid, row.agent_hex_id, row.record_uuid)
        )

    def _row_at(self, index: int | None) -> SearchResultRow | None:
        if index is None or not (0 <= index < len(self._results)):
            return None
        return self._results[index]

    def _ensure_web_for_open(self) -> str:
        if self._web.ensure_started_local():
            self._append_log(
                f"started web server at {self._web.local_url()} (a browser launch needs one)"
            )
        return self._web.local_url()

    def _open_url(self, url: str) -> None:
        self._append_log(f"opening {url}")
        webbrowser.open(url)

    # --- Misc ----------------------------------------------------------------------------

    def action_clear(self) -> None:
        self.query_one("#cmd", Input).value = ""
        self.query_one("#results", ResultsList).clear_options()
        self._results = []
        self.query_one("#cmd", Input).focus()

    def _append_log(self, line: str) -> None:
        self.query_one("#log", RichLog).write(line)

    def stop_web(self) -> None:
        """Stop any web server the TUI started (called on exit by :func:`run_tui`)."""
        self._web.stop()


def _session_factory_for(engine):  # noqa: ANN001, ANN202 -- Engine -> sessionmaker
    return session_factory(engine)


def run_tui(*, db_path: Path, source_root: Path) -> object | None:
    """Build and run the TUI, stopping any web server it started on the way out.

    Returns ``App.run()``'s result -- the ``/restart`` command exits with the string
    ``"restart"``, which ``_cmd_tui`` (cli.py) checks AFTER this returns to re-exec a fresh
    process (terminal already restored by then; never exec from inside a running Textual app).
    """
    app = IntrospectApp(db_path=db_path, source_root=source_root)
    try:
        result = app.run()
    finally:
        app.stop_web()
    return result
