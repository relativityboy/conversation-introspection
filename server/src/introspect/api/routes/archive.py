"""Archive endpoint (Task P4-F3, spec §15.1): ``PUT /api/v1/sessions/{uuid}/archive``.

An archived session is existence-based, not a boolean column (see ``ArchivedSession`` in
:mod:`introspect.models`): a session is archived iff an ``archived_sessions`` row with its
``session_uuid`` exists. There is only ONE verb -- ``PUT`` -- and it is naturally idempotent by
checking for the row first (a second PUT adds nothing), returning a bare 204 either way so the
client never needs to distinguish "archived" from "already archived".

There is deliberately no API ``DELETE`` / unarchive: restore is CLI-only
(``introspect unarchive <uuid>``), and no API/UI path lists or reveals archived sessions
(§15.1 "discovery requires direct DB query -- by design"). ``PUT`` still works on an
already-archived session because ``_require_session`` checks the ``sessions`` table directly,
which archiving never empties -- only the READ paths hide the row.

Archived state is read-model-adjacent user data, never touched by import/reparse: neither
``run_import`` nor ``reparse_all`` writes to or deletes from ``archived_sessions`` (see
``introspect.ingest.reparse._delete_all_interpretation_rows``, which enumerates exactly the
tables reparse wipes and ``archived_sessions`` is not among them) -- spec Sec.4's "never
touched" invariant, proven end-to-end by ``test_archived_session_survives_import_and_reparse``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.models import ArchivedSession, ChatSession

router = APIRouter(prefix="/api/v1")


def _require_session(db: Session, session_uuid: str) -> None:
    if db.get(ChatSession, session_uuid) is None:
        raise LookupError(f"session {session_uuid} not found")


@router.put("/sessions/{session_uuid}/archive", status_code=204)
def archive_session(session_uuid: str, db: Session = Depends(get_db)) -> Response:
    _require_session(db, session_uuid)
    if db.get(ArchivedSession, session_uuid) is None:
        db.add(ArchivedSession(session_uuid=session_uuid, created_at=datetime.now(timezone.utc)))
        db.commit()
    return Response(status_code=204)
