"""FastAPI application factory for the conversation-introspection archive API -- mostly a read
layer, but not read-only: ``POST /import`` triggers a background import, the favorites router
writes favorite/unfavorite state, and the titles router writes user title state.

``create_app`` mirrors the CLI's DB lifecycle (:mod:`introspect.config` resolves paths,
:mod:`introspect.db` opens + migrates the engine) so the same archive can be inspected via
``introspect status`` and served for reading without divergent config-handling logic. The
sessionmaker, resolved source root, and resolved DB path all live on ``app.state`` so
dependencies -- and, from Task 5 on, route handlers -- can reach them without reopening the
engine per request.

Task 5 registers the sessions/projects/messages read router (:mod:`introspect.api.routes.sessions`);
Task 6 adds the search router (:mod:`introspect.api.routes.search`). Task 7 adds the favorites
router (:mod:`introspect.api.routes.favorites`); Task 8 adds the import router on top. Task
P4-1 adds the user title router (:mod:`introspect.api.routes.titles`).
``/api/v1/health`` and the problem-details error handlers (:mod:`introspect.api.errors`)
round out the surface. Task 9 mounts the built React UI (``web/dist``) alongside the API so
``introspect serve`` is one process, one port -- see ``_resolve_ui_dist`` and the SPA-fallback
middleware below.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from introspect import changelog, config
from introspect.api.errors import register_error_handlers
from introspect.api.routes.admin import router as admin_router
from introspect.api.routes.archive import router as archive_router
from introspect.api.routes.favorites import router as favorites_router
from introspect.api.routes.records import router as records_router
from introspect.api.routes.resume import router as resume_router
from introspect.api.routes.search import router as search_router
from introspect.api.routes.sessions import router as sessions_router
from introspect.api.routes.titles import router as titles_router
from introspect.cron import Runner
from introspect.db import get_engine, session_factory, upgrade_to_head


def create_app(
    db_path: Path | None = None,
    source_root: Path | None = None,
    ui_dist: Path | None = None,
    terminal_app: str | None = None,
    resume_runner: "Runner | None" = None,
    app_version: str | None = None,
) -> FastAPI:
    """Build a fully-migrated app instance bound to one archive DB.

    Resolves ``db_path``/``source_root`` through :mod:`introspect.config` (explicit arg >
    env var > default -- the same precedence the CLI uses), then opens the engine and runs
    Alembic migrations to head so a fresh DB is queryable immediately, with no separate
    ``introspect import`` step required first (tests rely on this: a bare ``tmp_path`` DB
    boots straight into a usable app).

    ``ui_dist`` (see :func:`_resolve_ui_dist`) controls whether the built React UI is served
    alongside the API. When resolved, its ``assets/`` are mounted at ``/assets`` and a
    SPA-fallback middleware serves ``index.html`` for any unmatched GET/HEAD outside
    ``/api/*``. When it can't be resolved -- or the dist is broken/partial -- the app is
    API-only (pre-Task-9 behavior). The outcome lands on ``app.state.ui_dist``
    (path-or-None); this factory is side-effect-silent -- it never logs or prints. The one
    line of user-facing UI logging lives in the CLI (``_cmd_serve``), which reads that state.
    """
    resolved_db_path = config.db_path(str(db_path) if db_path is not None else None)
    resolved_source_root = config.source_root(str(source_root) if source_root is not None else None)
    resolved_ui_dist = _resolve_ui_dist(ui_dist)
    resolved_app_version = app_version if app_version is not None else changelog.app_version()

    engine = get_engine(resolved_db_path)
    upgrade_to_head(engine)

    app = FastAPI(title="conversation-introspection")
    app.state.session_factory = session_factory(engine)
    app.state.db_path = resolved_db_path
    app.state.source_root = resolved_source_root
    app.state.terminal_app = config.terminal_app(terminal_app)
    app.state.resume_runner = resume_runner  # tests inject a fake; None = real subprocess
    app.state.app_version = resolved_app_version

    register_error_handlers(app)
    app.include_router(sessions_router)
    app.include_router(search_router)
    app.include_router(favorites_router)
    app.include_router(titles_router)
    app.include_router(archive_router)
    app.include_router(records_router)
    app.include_router(admin_router)
    app.include_router(resume_router)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if resolved_ui_dist is not None:
        try:
            assets_files = StaticFiles(directory=resolved_ui_dist / "assets")
        except Exception:
            # NOTE(claude): a broken/partial dist (e.g. index.html present but assets/
            # missing) degrades to API-only; create_app must never crash over UI state,
            # and -- factory purity -- must not log about it either. The CLI reads
            # app.state.ui_dist (None here) and reports "UI: not built".
            resolved_ui_dist = None
        else:
            app.mount("/assets", assets_files, name="ui-assets")

            # NOTE(claude): a SPA fallback implemented as a literal
            # `@app.get("/{path:path}")` route would have to be registered here, inside
            # create_app, to come "last" per the task contract -- but Starlette's Router
            # matches routes in REGISTRATION order, not by specificity, so a catch-all
            # route registered here would shadow any route added to the returned `app`
            # object afterward. Several existing tests (test_api_skeleton.py) do exactly
            # that (`@app.get(...)` on the fixture app, post-hoc) to exercise the error
            # handlers. Verified empirically that a literal catch-all route breaks those
            # tests. Middleware runs AFTER routing + exception handling have already
            # produced a response, so it can never shadow a route regardless of
            # registration order, and it reuses the existing problem-JSON 404
            # (:mod:`introspect.api.errors`) for every `/api/*` miss instead of
            # duplicating it. The `api/` prefix check below depends on the invariant that
            # every real API route lives under `/api/v1` -- a future route mounted
            # outside `/api/` would have its 404s swallowed into the SPA shell.
            @app.middleware("http")
            async def _serve_ui_fallback(request: Request, call_next) -> Response:
                response = await call_next(request)
                if response.status_code != 404 or request.method not in ("GET", "HEAD"):
                    return response
                path = request.url.path.lstrip("/")
                if path.startswith("api/"):
                    return response
                if path:
                    candidate = (resolved_ui_dist / path).resolve()
                    try:
                        candidate.relative_to(resolved_ui_dist.resolve())
                    except ValueError:
                        candidate = None
                    if candidate is not None and candidate.is_file():
                        return FileResponse(candidate)
                return FileResponse(resolved_ui_dist / "index.html")

    app.state.ui_dist = resolved_ui_dist
    return app


def _resolve_ui_dist(explicit: Path | None) -> Path | None:
    """Resolve the built UI's ``dist`` directory, or ``None`` for API-only mode.

    Precedence: ``explicit`` param > ``INTROSPECT_UI_DIST`` env var > walking up from this
    package's own location looking for a repo checkout's ``web/dist/index.html``. The walk
    covers ``uv run`` from a cloned repo; a site-packages install with neither the param nor
    the env var set has nothing to walk up to and falls back to API-only.
    """
    if explicit is not None:
        return explicit
    env_value = os.environ.get("INTROSPECT_UI_DIST")
    if env_value:
        return Path(env_value)
    return _walk_up_for_ui_dist(Path(__file__).resolve())


def _walk_up_for_ui_dist(start: Path) -> Path | None:
    """Walk from ``start`` up through its ancestors for a checked-out, COMPLETE ``web/dist``.

    A candidate counts only if it has BOTH ``index.html`` and an ``assets/`` directory -- a
    half-built dist is treated as absent rather than mounted broken. The walk stops at the
    repo boundary: the first ancestor containing ``.git`` is still checked for ``web/dist``
    (that ancestor IS the repo root in a checkout), but the walk never ascends past it, so
    an unrelated ``web/dist`` above the repo can never be picked up.

    NOTE(claude): the boundary is ``.git`` ONLY, deliberately not ``pyproject.toml`` -- in
    this repo's layout ``server/pyproject.toml`` sits BETWEEN this package and the repo
    root, so a pyproject.toml boundary would stop the walk at ``server/`` and never reach
    the repo root's ``web/dist`` (verified against the editable-install path uv sync
    produces). Don't add pyproject.toml as a stopper without restructuring the repo.
    """
    for ancestor in (start, *start.parents):
        candidate = ancestor / "web" / "dist"
        if (candidate / "index.html").is_file() and (candidate / "assets").is_dir():
            return candidate
        if (ancestor / ".git").exists():
            return None
    return None
