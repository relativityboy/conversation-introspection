"""User title endpoint (Task P4-1): ``PUT /api/v1/sessions/{uuid}/title``.

A user title is existence-based, not a nullable column on ``sessions`` (see ``UserTitle`` in
:mod:`introspect.models`): a session has a user title iff a ``user_titles`` row with its
``session_uuid`` exists. There is only one verb -- ``PUT`` -- because "unset" is expressed by
PUTting an empty/whitespace title rather than a separate ``DELETE``: sending back the
archive-derived title (``ai_title``/``custom_title``) IS the delete. Both the upsert and the
delete branches are idempotent (a second identical PUT updates ``updated_at`` but stays one
row; PUTting empty against an absent row deletes nothing), and both return a bare 204 with no
body either way.

The 200-char cap (spec Sec.14.3 critique #10) validates the RAW title string -- stripping is
used ONLY to decide whether the title counts as empty (and therefore a delete), never to
shrink what gets measured against the cap or what gets stored. This is why the empty-check
runs strictly before the length check: a long whitespace-only title is still a delete, not a
422.

User titles are read-model-adjacent state, never touched by import/reparse: neither
``run_import`` nor ``reparse_all`` writes to or deletes from ``user_titles`` (see
``introspect.ingest.reparse._delete_all_interpretation_rows``, which enumerates exactly the
tables reparse wipes and ``user_titles`` is not among them) -- spec Sec.4's "never touched"
invariant, proven end-to-end by ``test_user_title_survives_import_and_reparse``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.models import ChatSession, UserTitle

router = APIRouter(prefix="/api/v1")

_MAX_TITLE_LENGTH = 200


class TitleIn(BaseModel):
    title: str


def _require_session(db: Session, session_uuid: str) -> None:
    if db.get(ChatSession, session_uuid) is None:
        raise LookupError(f"session {session_uuid} not found")


@router.put("/sessions/{session_uuid}/title", status_code=204)
def set_title(session_uuid: str, body: TitleIn, db: Session = Depends(get_db)) -> Response:
    _require_session(db, session_uuid)

    if not body.title.strip():
        existing = db.get(UserTitle, session_uuid)
        if existing is not None:
            db.delete(existing)
            db.commit()
        return Response(status_code=204)

    if len(body.title) > _MAX_TITLE_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"title must be at most {_MAX_TITLE_LENGTH} characters"
        )

    existing = db.get(UserTitle, session_uuid)
    now = datetime.now(timezone.utc)
    if existing is None:
        db.add(UserTitle(session_uuid=session_uuid, title=body.title, updated_at=now))
    else:
        existing.title = body.title
        existing.updated_at = now
    db.commit()
    return Response(status_code=204)
