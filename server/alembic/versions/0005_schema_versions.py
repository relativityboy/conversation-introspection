"""schema versions

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-20 12:30:00.000000

Adds the ``schema_versions`` table (Task P4-F7): provenance history of the interpretation
schema GENERATION (the ``introspect-schema/N`` stamp). One row per version, keyed by
``version`` (PK): ``first_encountered_at`` (when this codebase version first ran an
import/reparse against the archive) and ``diff_note`` (human-readable old-vs-new summary).

This is NOT user data -- it is metadata about the archive's own interpretation lineage, so
(unlike favorites/user_titles/archived_sessions) it may be backfilled and self-populated
freely without touching anything the user authored.

Backfill: the historical generations (``introspect-schema/1`` .. ``/3``) predate this table,
so their rows are seeded here with FROZEN diff notes (copies of ``introspect.schema.DIFF_NOTES``
at the time of writing -- the migration stays self-contained and never imports app code, same
convention as 0002's frozen search snapshot). ``first_encountered_at`` is derived from the
earliest ``import_runs.started_at`` when the archive has import history, else the migration run
time. Reparse overwrites each raw record's per-version schema stamp, so precise per-version
first-encounter timing is not recoverable for /2 and /3 -- the earliest-import-run value is an
honest lower-bound floor, and each backfilled note says so. The row for the CURRENT running
version is NOT seeded here: it is stamped idempotently the first time import/reparse runs (see
``introspect.schema_versions.ensure_current_schema_version_recorded``).

``first_encountered_at`` is declared ``sa.String()`` here (same convention as 0001-0004): at
the ORM layer it is ``introspect.db.UTCDateTime`` (a TypeDecorator over String storing ISO-8601
UTC text), whose DDL is an ordinary text column.
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen copies of introspect.schema.DIFF_NOTES for the historical generations. Never import
# from src/introspect here (same rule as migration 0002): a migration is a point-in-time
# artifact and must not shift when app constants later evolve.
_FROZEN_NOTES: dict[str, str] = {
    "introspect-schema/1": (
        "Initial tolerant transcript-record registry: the conversational envelope family "
        "(user/assistant/system/attachment), the thin-meta records, and the never-raise "
        "parse_line contract (forward drift is an info anomaly, unknown record type a warn, "
        "bad JSON/validation an error)."
    ),
    "introspect-schema/2": (
        "First production-drift pass. Declared forward-drift fields at verified locations: "
        "agentId/slug and isMeta/session_id on the Envelope; attribution* and a string error "
        "on AssistantRecord; sourceTool*/origin on UserRecord; the message-level type echo; "
        "the tool_use caller; the SystemRecord subtype payloads (turn_duration, "
        "stop_hook_summary, api_error); the opaque attachment body; lastPrompt. Added "
        "AgentColorRecord for the retired agent-color type."
    ),
    "introspect-schema/3": (
        "Interpretation change only, no new fields. AttachmentRecord.blocks() rescues a "
        "human-origin queued_command attachment into one searchable text block; every other "
        "attachment shape stays zero-block. Anomaly floor unchanged."
    ),
}

_BACKFILL_CAVEAT = (
    " [first_encountered_at backfilled at migration 0005 from the earliest import run "
    "(a lower-bound floor -- reparse overwrote per-record schema stamps, so exact per-version "
    "first-encounter timing is not recoverable)]"
)
_BACKFILL_CAVEAT_NO_HISTORY = (
    " [first_encountered_at backfilled at migration 0005 to the migration run time "
    "(no import history was present to derive an earlier timestamp)]"
)


def upgrade() -> None:
    op.create_table(
        'schema_versions',
        sa.Column('version', sa.String(), nullable=False),
        sa.Column('first_encountered_at', sa.String(), nullable=False),
        sa.Column('diff_note', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('version'),
    )

    conn = op.get_bind()
    earliest = conn.execute(sa.text("SELECT MIN(started_at) FROM import_runs")).scalar()
    if earliest:
        first_at = earliest
        caveat = _BACKFILL_CAVEAT
    else:
        first_at = datetime.now(timezone.utc).isoformat()
        caveat = _BACKFILL_CAVEAT_NO_HISTORY

    conn.execute(
        sa.text(
            "INSERT INTO schema_versions (version, first_encountered_at, diff_note) "
            "VALUES (:version, :first_at, :diff_note)"
        ),
        [
            {"version": v, "first_at": first_at, "diff_note": note + caveat}
            for v, note in _FROZEN_NOTES.items()
        ],
    )


def downgrade() -> None:
    op.drop_table('schema_versions')
