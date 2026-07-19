"""FastAPI dependency providers.

``get_db`` is the one dependency every route handler needs: a DB session scoped to the
request, bound to the sessionmaker :func:`introspect.api.create_app` stores on
``app.state.session_factory``.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    session_factory = request.app.state.session_factory
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
