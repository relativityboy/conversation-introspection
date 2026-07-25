"""Pydantic response models for the API.

Field names and types are the binding contract Tasks 5-8 import against -- do not rename
or reshape without checking every route module that constructs these. Every model enables
``from_attributes`` so a route can hand it a SQLAlchemy ORM row directly via
``Model.model_validate(row)``; datetimes serialize to ISO-8601 strings (Pydantic v2's
default JSON encoding for ``datetime``, matching the ISO-8601 UTC text every datetime
column is stored as -- see :class:`introspect.db.UTCDateTime`).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# Shared pagination defaults/caps -- sessions.py, search.py, and admin.py each clamp their
# `limit` query param against these (`min(max(limit, 1), _MAX_LIMIT)`); hoisted here rather
# than to api/__init__.py since they're plain constants the route modules import, not part of
# the app-factory surface.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_uuid: str
    project_slug: str
    ai_title: str | None
    custom_title: str | None
    user_title: str | None = None
    started_at: datetime | None
    last_activity_at: datetime | None
    message_count: int
    favorite: bool
    # Populated by GET /sessions?q= ONLY for rows matched by conversational content and NOT by
    # uuid/title (a <mark>-wrapped best snippet); null on unfiltered lists, detail, and title/
    # uuid matches. See routes/sessions.py `list_sessions` for the match-attribution rule.
    match_snippet: str | None = None
    # Where the match_snippet's winning hit lives, so the sidebar can deep-link the snippet click
    # straight to the matched message. Both are null WHENEVER match_snippet is null (same
    # attribution rule); match_agent_hex_id is additionally null for a MAIN-transcript hit and
    # non-null only when the best hit sits in a subagent transcript (route via /a/{hex}/).
    match_record_uuid: str | None = None
    match_agent_hex_id: str | None = None


class TranscriptInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    agent_hex_id: str | None
    agent_type: str | None
    agent_description: str | None
    parent_tool_use_id: str | None


class SessionDetail(SessionSummary):
    transcripts: list[TranscriptInfo]


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    block_index: int
    block_kind: str
    text_content: str | None
    tool_name: str | None
    tool_use_id: str | None
    is_error: bool | None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_uuid: str
    parent_uuid: str | None
    type: str
    model: str | None
    timestamp: datetime | None
    blocks: list[BlockOut]


class HitOut(BaseModel):
    """One search hit. Field-compatible with ``introspect.search.SearchHit`` by attribute
    name, so routes build these via ``HitOut.model_validate(hit)`` rather than a manual
    field-by-field mapping.

    ``agent_hex_id`` is the one field NOT carried by ``SearchHit`` (it defaults to ``None`` on
    validate) -- the search route fills it from a single per-request transcript lookup: the
    subagent hex when the hit's transcript is a subagent, ``None`` for a main transcript. The
    reader's deep-link seam routes by it (a subagent hit must open the ``/a/{hex}/`` drill-in;
    linking it to the main-conversation path makes the arrival view fetch the MAIN transcript
    with a foreign record uuid and 404 -- the cross-layer bug this field closes, Task P3-10).
    """

    model_config = ConfigDict(from_attributes=True)

    record_uuid: str | None
    transcript_id: int
    block_index: int
    block_kind: str
    snippet: str
    timestamp: datetime | None
    agent_hex_id: str | None = None


class Problem(BaseModel):
    """The problem-details error shape every non-2xx API response uses. See errors.py."""

    status: int
    title: str
    detail: str


class ResumeResult(BaseModel):
    """POST /sessions/{uuid}/resume outcome (spec §17.2/§17.3). `mode` is the launch outcome
    only; `restored` is orthogonal."""

    restored: bool
    launched: bool
    mode: str
    command: str
    cwd: str | None
    live_path: str
    detail: str | None
