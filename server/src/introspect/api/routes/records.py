"""Raw-record inspector endpoint (Task P4-F4, spec §15.2): ``GET /records/{record_uuid}/raw``.

Returns the EXACT stored ``raw_records.raw_line`` bytes for a uuid-bearing record -- byte-faithful,
never re-parsed or re-serialized (pretty-printing is a client concern; a malformed line must come
back verbatim). Served as ``text/plain``, deliberately NOT ``application/json``: the bytes may not
be valid JSON at all, and labelling them ``application/json`` would be a lie the reader's raw view
must never tell.

Read-exclusion (§15.1): a record whose owning session is archived 404s, folded into the SAME
not-found as an unknown uuid -- mirroring ``list_messages``' archived probe (``transcript`` ->
``session_id`` -> ``archived_sessions``). ``record_uuid`` is nullable in ``raw_records``; this
endpoint only ever addresses uuid-bearing records, which are exactly the ones the reader shows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.models import ArchivedSession, RawRecord, Transcript

router = APIRouter(prefix="/api/v1")


@router.get("/records/{record_uuid}/raw")
def get_record_raw(record_uuid: str, db: Session = Depends(get_db)) -> Response:
    row = db.execute(
        select(RawRecord.raw_line, Transcript.session_id)
        .join(Transcript, RawRecord.transcript_id == Transcript.id)
        .where(RawRecord.record_uuid == record_uuid)
        # NOTE(claude): a diverged source file leaves an OLD generation's raw_record for the same
        # uuid alongside the new one (capture never deletes captured bytes; only the old
        # generation's Message rows are removed -- see capture._handle_divergence). The reader
        # shows the LIVE generation, so prefer the most recently ingested row (highest id) to stay
        # byte-faithful to exactly the row whose {} button was clicked. In the common
        # (non-diverged) case record_uuid is unique here and the ordering is a no-op.
        .order_by(RawRecord.id.desc())
    ).first()
    if row is None:
        raise LookupError(f"record {record_uuid} not found")
    raw_line, session_id = row
    # Archived session -> same 404 as unknown (§15.1); a direct probe, mirroring list_messages.
    if db.get(ArchivedSession, session_id) is not None:
        raise LookupError(f"record {record_uuid} not found")
    # Byte-faithful: hand back the stored bytes untouched. text/plain, never application/json.
    return Response(content=raw_line, media_type="text/plain; charset=utf-8")
