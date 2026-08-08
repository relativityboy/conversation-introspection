"""SQLAlchemy 2.0 ORM models for the transcript archive.

Column names, types, unique constraints, and indexes are the binding contract that
later import/query tasks depend on (see task-2-brief.md §"Column contracts"). Every
datetime column uses :class:`introspect.db.UTCDateTime` (ISO-8601 UTC text).

Nullability convention: identity keys and structural foreign keys are NOT NULL;
descriptive metadata parsed out of external transcripts is nullable, because the
archive must ingest imperfect data without failing (correctness & safety first).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from introspect.db import UTCDateTime


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    dir_slug: Mapped[str] = mapped_column(unique=True)
    resolved_cwd: Mapped[str | None]
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ChatSession(Base):
    # NOTE(claude): ORM class is ChatSession, table is "sessions". Named to avoid
    # colliding with sqlalchemy.orm.Session (which the plan's `db:` params refer to).
    __tablename__ = "sessions"

    session_uuid: Mapped[str] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ai_title: Mapped[str | None]
    custom_title: Mapped[str | None]


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("session_id", "kind", "agent_hex_id", name="uq_transcript_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_uuid"))
    kind: Mapped[str]  # 'main' | 'subagent'
    agent_hex_id: Mapped[str | None]
    agent_type: Mapped[str | None]
    agent_description: Mapped[str | None]
    parent_tool_use_id: Mapped[str | None]


class SourceFile(Base):
    # NOTE(claude): uniqueness is composite (path, generation), NOT bare path.
    # When a file at a path is replaced ('diverged') or disappears ('gone_at_source')
    # and a new file later takes the same path, `generation` is bumped so the old
    # row is preserved (this is an archive — history is never overwritten).
    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("path", "generation", name="uq_source_files_path_generation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    # NOTE(claude): nullable — a file is discovered (and stat'd) before it is parsed,
    # and the transcript identity is only known after reading the file's header.
    transcript_id: Mapped[int | None] = mapped_column(ForeignKey("transcripts.id"))
    path: Mapped[str]
    generation: Mapped[int] = mapped_column(default=0)
    kind: Mapped[str]
    is_primary: Mapped[bool] = mapped_column(default=False)
    byte_offset_checkpoint: Mapped[int] = mapped_column(default=0)
    last_size: Mapped[int]
    prefix_hash: Mapped[str]
    status: Mapped[str]  # 'active' | 'gone_at_source' | 'diverged'
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    gone_detected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RawRecord(Base):
    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint("source_file_id", "line_number", name="uq_raw_records_file_line"),
        Index("ix_raw_records_transcript_uuid", "transcript_id", "record_uuid"),
        Index("ix_raw_records_transcript_sha", "transcript_id", "line_sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id"))
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id"))
    line_number: Mapped[int]
    byte_offset: Mapped[int]
    raw_line: Mapped[bytes] = mapped_column(LargeBinary)
    line_sha256: Mapped[str]
    record_type: Mapped[str | None]
    record_uuid: Mapped[str | None]
    detected_cli_version: Mapped[str | None]
    parsed_with_schema_version: Mapped[str | None]
    parse_status: Mapped[str]  # 'ok' | 'partial' | 'anomaly'
    reassembled: Mapped[bool] = mapped_column(default=False, server_default=false())  # spec §2 provenance marker
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str]  # 'cli' | 'api' | 'recapture'
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    files_seen: Mapped[int] = mapped_column(default=0)
    records_added: Mapped[int] = mapped_column(default=0)
    records_skipped_duplicate: Mapped[int] = mapped_column(default=0)
    anomaly_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str]  # 'running' | 'ok' | 'errors' | 'fatal'


class ParseAnomaly(Base):
    __tablename__ = "parse_anomalies"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_record_id: Mapped[int | None] = mapped_column(ForeignKey("raw_records.id"))
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id"))
    severity: Mapped[str]  # 'info' | 'warn' | 'error'
    kind: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSON)
    schema_version: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_records.id"), unique=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id"))
    record_uuid: Mapped[str]
    parent_uuid: Mapped[str | None]
    timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime)
    type: Mapped[str]
    model: Mapped[str | None]
    cwd: Mapped[str | None]
    git_branch: Mapped[str | None]
    request_id: Mapped[str | None]
    authorship_kind: Mapped[str | None]
    authorship_basis: Mapped[str | None]
    authorship_detail: Mapped[str | None]


class ContentBlock(Base):
    __tablename__ = "content_blocks"
    __table_args__ = (
        Index("ix_content_blocks_message_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    block_index: Mapped[int]
    block_kind: Mapped[str]
    text_content: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None]
    tool_use_id: Mapped[str | None]
    is_error: Mapped[bool | None]
    payload: Mapped[dict | None] = mapped_column(JSON)


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), unique=True)
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cache_creation_input_tokens: Mapped[int | None]
    cache_read_input_tokens: Mapped[int | None]


class SessionEvent(Base):
    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_records.id"), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_uuid"))
    event_kind: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON)


class Favorite(Base):
    # NOTE(claude): session_uuid is both PK and FK — a session is favorited at most once
    # (added/removed by presence of the row, not a boolean flag). See migration 0002.
    __tablename__ = "favorites"

    session_uuid: Mapped[str] = mapped_column(ForeignKey("sessions.session_uuid"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class UserTitle(Base):
    # NOTE(claude): session_uuid is both PK and FK, existence-based like Favorite — a row
    # present means the user has overridden the archive-derived title (ai_title/custom_title);
    # absence means fall back to those. See migration 0003.
    __tablename__ = "user_titles"

    session_uuid: Mapped[str] = mapped_column(ForeignKey("sessions.session_uuid"), primary_key=True)
    title: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ArchivedSession(Base):
    # NOTE(claude): session_uuid is both PK and FK, existence-based like Favorite/UserTitle — a
    # row present means the session is HIDDEN from every API read path (list, detail, search,
    # messages, export) while capture/reparse keep syncing it untouched (§15.1 "only read is
    # prevented"). Restore is CLI-only (`introspect unarchive <uuid>`); no API/UI path reveals or
    # removes it. See migration 0004.
    __tablename__ = "archived_sessions"

    session_uuid: Mapped[str] = mapped_column(ForeignKey("sessions.session_uuid"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SchemaVersion(Base):
    # NOTE(claude): provenance history of the interpretation schema GENERATION (the
    # `introspect-schema/N` stamp), NOT user data. One row per version this codebase has run
    # against the archive: `first_encountered_at` is set once, idempotently, the first time an
    # import/reparse runs under that SCHEMA_VERSION (see introspect.schema_versions); migration
    # 0005 backfills the historical rows. `diff_note` is the human-readable old-vs-new summary
    # copied from introspect.schema.DIFF_NOTES. See migration 0005.
    __tablename__ = "schema_versions"

    version: Mapped[str] = mapped_column(primary_key=True)
    first_encountered_at: Mapped[datetime] = mapped_column(UTCDateTime)
    diff_note: Mapped[str | None] = mapped_column(Text)
