"""Favorites endpoints (Task 7): ``PUT``/``DELETE /api/v1/sessions/{uuid}/favorite``.

A favorite is existence-based, not a boolean column (see ``Favorite`` in
:mod:`introspect.models`): a session is favorited iff a ``favorites`` row with its
``session_uuid`` exists. Both verbs are therefore naturally idempotent by checking for the
row first -- a second PUT adds nothing, a DELETE with no row deletes nothing -- and both
return a bare 204 with no body either way, so the client never needs to distinguish "created"
from "already favorited" (same for delete).

Favorites are read-model-adjacent state, never touched by import/reparse: neither
``run_import`` nor ``reparse_all`` writes to or deletes from ``favorites`` (see
``introspect.ingest.reparse._delete_all_interpretation_rows``, which enumerates exactly the
tables reparse wipes and ``favorites`` is not among them) -- spec Sec.4's "never touched"
invariant, proven end-to-end by ``test_favorite_survives_import_and_reparse``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.models import ChatSession, Favorite

router = APIRouter(prefix="/api/v1")


def _require_session(db: Session, session_uuid: str) -> None:
    if db.get(ChatSession, session_uuid) is None:
        raise LookupError(f"session {session_uuid} not found")


@router.put("/sessions/{session_uuid}/favorite", status_code=204)
def add_favorite(session_uuid: str, db: Session = Depends(get_db)) -> Response:
    _require_session(db, session_uuid)
    if db.get(Favorite, session_uuid) is None:
        db.add(Favorite(session_uuid=session_uuid, created_at=datetime.now(timezone.utc)))
        db.commit()
    return Response(status_code=204)


@router.delete("/sessions/{session_uuid}/favorite", status_code=204)
def remove_favorite(session_uuid: str, db: Session = Depends(get_db)) -> Response:
    _require_session(db, session_uuid)
    favorite = db.get(Favorite, session_uuid)
    if favorite is not None:
        db.delete(favorite)
        db.commit()
    return Response(status_code=204)
