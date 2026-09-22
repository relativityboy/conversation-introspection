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

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.models import (
    ArchivedSession,
    ChatSession,
    Message,
    Project,
    RawRecord,
    Transcript,
)

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


class RecordMeta(BaseModel):
    """Reverse lookup (2026-08-23): where a bare record_uuid lives.

    A journal entry or an old note cites a record_uuid; this names the conversation around
    it -- session, project, transcript -- so the citation can be dereferenced (fetch a
    window via ``/transcripts/{id}/messages?around=|from=|until=``) without knowing the
    session first. Describes the LIVE interpreted record: a raw line that never produced a
    Message row (malformed capture) has bytes at ``/raw`` but no place in a conversation,
    so it 404s here -- that asymmetry is honest, not a gap.
    """

    record_uuid: str
    session_uuid: str
    project_slug: str
    transcript_id: int
    transcript_kind: str
    type: str
    timestamp: datetime | None


@router.get("/records/{record_uuid}", response_model=RecordMeta)
def get_record_meta(record_uuid: str, db: Session = Depends(get_db)) -> RecordMeta:
    row = db.execute(
        select(
            Message.type,
            Message.timestamp,
            Transcript.id,
            Transcript.kind,
            Transcript.session_id,
            Project.dir_slug,
        )
        .join(Transcript, Message.transcript_id == Transcript.id)
        .join(ChatSession, Transcript.session_id == ChatSession.session_uuid)
        .join(Project, ChatSession.project_id == Project.id)
        .where(Message.record_uuid == record_uuid)
        # Diverged files can leave the same uuid in two generations; the newest Message row
        # is the live one (same reasoning as /raw's highest-id preference above).
        .order_by(Message.id.desc())
    ).first()
    if row is None:
        raise LookupError(f"record {record_uuid} not found")
    msg_type, timestamp, transcript_id, kind, session_id, dir_slug = row
    # Archived session -> same 404 as unknown (§15.1), indistinguishable by design.
    if db.get(ArchivedSession, session_id) is not None:
        raise LookupError(f"record {record_uuid} not found")
    return RecordMeta(
        record_uuid=record_uuid,
        session_uuid=session_id,
        project_slug=dir_slug,
        transcript_id=transcript_id,
        transcript_kind=kind,
        type=msg_type,
        timestamp=timestamp,
    )


class MessageIdRecords(BaseModel):
    """Reverse lookup by API message id (Task T2): every LIVE record for one ``message.id``.

    One API message can be split across multiple JSONL lines (text + tool_use as separate
    records sharing one ``message.id``), so this is a list, not a single ``RecordMeta`` --
    see ``Message.api_message_id`` (migration 0011). Defined here beside ``RecordMeta``
    rather than in ``api/models.py`` (where every other response model lives) because it
    composes ``RecordMeta`` directly: this route module would need to import
    ``MessageIdRecords`` back from ``api/models.py`` for its ``response_model=``, and
    ``api/models.py`` importing ``RecordMeta`` from here to build it would make that a
    circular import (models -> routes.records -> models). Keeping the composition local
    avoids that entirely. See the T2 write-up for the fuller reasoning.
    """

    api_message_id: str
    records: list[RecordMeta]


@router.get("/records/by-message-id/{api_message_id}", response_model=MessageIdRecords)
def get_records_by_message_id(
    api_message_id: str, db: Session = Depends(get_db)
) -> MessageIdRecords:
    rows = db.execute(
        select(
            Message.id,
            Message.record_uuid,
            Message.type,
            Message.timestamp,
            Transcript.id,
            Transcript.kind,
            Transcript.session_id,
            Project.dir_slug,
        )
        .join(Transcript, Message.transcript_id == Transcript.id)
        .join(ChatSession, Transcript.session_id == ChatSession.session_uuid)
        .join(Project, ChatSession.project_id == Project.id)
        .where(Message.api_message_id == api_message_id)
    ).all()
    if not rows:
        raise LookupError(f"api message {api_message_id} not found")

    # Dedupe per record_uuid, keeping the highest Message.id (newest generation) -- the
    # same cross-generation tie-break get_record_meta uses above, applied per group here
    # since one api_message_id can legitimately span several DISTINCT record_uuids too
    # (the multi-line-per-message case, kept as separate entries -- not deduped away).
    newest_by_uuid: dict[str, tuple] = {}
    for row in rows:
        message_id, record_uuid = row[0], row[1]
        current = newest_by_uuid.get(record_uuid)
        if current is None or message_id > current[0]:
            newest_by_uuid[record_uuid] = row

    records: list[RecordMeta] = []
    for (
        message_id,
        record_uuid,
        msg_type,
        timestamp,
        transcript_id,
        kind,
        session_id,
        dir_slug,
    ) in sorted(newest_by_uuid.values(), key=lambda r: r[0]):
        # Archived session -> excluded from the list, same fold as get_record_meta's 404.
        if db.get(ArchivedSession, session_id) is not None:
            continue
        records.append(
            RecordMeta(
                record_uuid=record_uuid,
                session_uuid=session_id,
                project_slug=dir_slug,
                transcript_id=transcript_id,
                transcript_kind=kind,
                type=msg_type,
                timestamp=timestamp,
            )
        )
    if not records:
        # No live records survived the archived-session fold -- 404, not an empty list
        # (same "unknown vs archived is indistinguishable" rule as get_record_meta).
        raise LookupError(f"api message {api_message_id} not found")
    return MessageIdRecords(api_message_id=api_message_id, records=records)
