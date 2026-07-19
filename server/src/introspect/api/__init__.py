"""FastAPI application factory for the conversation-introspection archive API -- mostly a read
layer, but not read-only: ``POST /import`` triggers a background import and the favorites
router writes favorite/unfavorite state.

``create_app`` mirrors the CLI's DB lifecycle (:mod:`introspect.config` resolves paths,
:mod:`introspect.db` opens + migrates the engine) so the same archive can be inspected via
``introspect status`` and served for reading without divergent config-handling logic. The
sessionmaker, resolved source root, and resolved DB path all live on ``app.state`` so
dependencies -- and, from Task 5 on, route handlers -- can reach them without reopening the
engine per request.

Task 5 registers the sessions/projects/messages read router (:mod:`introspect.api.routes.sessions`);
Task 6 adds the search router (:mod:`introspect.api.routes.search`). Task 7 adds the favorites
router (:mod:`introspect.api.routes.favorites`); Task 8 adds the import router on top.
``/api/v1/health`` and the problem-details error handlers (:mod:`introspect.api.errors`)
round out the surface.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from introspect import config
from introspect.api.errors import register_error_handlers
from introspect.api.routes.admin import router as admin_router
from introspect.api.routes.favorites import router as favorites_router
from introspect.api.routes.search import router as search_router
from introspect.api.routes.sessions import router as sessions_router
from introspect.db import get_engine, session_factory, upgrade_to_head


def create_app(db_path: Path | None = None, source_root: Path | None = None) -> FastAPI:
    """Build a fully-migrated app instance bound to one archive DB.

    Resolves ``db_path``/``source_root`` through :mod:`introspect.config` (explicit arg >
    env var > default -- the same precedence the CLI uses), then opens the engine and runs
    Alembic migrations to head so a fresh DB is queryable immediately, with no separate
    ``introspect import`` step required first (tests rely on this: a bare ``tmp_path`` DB
    boots straight into a usable app).
    """
    resolved_db_path = config.db_path(str(db_path) if db_path is not None else None)
    resolved_source_root = config.source_root(str(source_root) if source_root is not None else None)

    engine = get_engine(resolved_db_path)
    upgrade_to_head(engine)

    app = FastAPI(title="conversation-introspection")
    app.state.session_factory = session_factory(engine)
    app.state.db_path = resolved_db_path
    app.state.source_root = resolved_source_root

    register_error_handlers(app)
    app.include_router(sessions_router)
    app.include_router(search_router)
    app.include_router(favorites_router)
    app.include_router(admin_router)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
