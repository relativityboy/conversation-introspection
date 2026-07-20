"""Idempotent recorder for the interpretation-schema provenance table.

The ``schema_versions`` table (migration 0005) keeps one row per ``introspect-schema/N``
generation this codebase has run against a given archive. Migration 0005 backfills the
historical rows; this module records the row for the *currently running* ``SCHEMA_VERSION``
the first time an import or reparse runs against the DB.

Kept out of the ``introspect.schema`` package on purpose: that package is deliberately DB-free
(it must not import ``introspect.db`` / ``introspect.models``). The provenance stamp is a DB
concern, so it lives here and imports the version constant + notes FROM the schema package.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from introspect.ingest.capture import utcnow
from introspect.models import SchemaVersion
from introspect.schema import DIFF_NOTES, SCHEMA_VERSION


def ensure_current_schema_version_recorded(db: Session) -> None:
    """Stamp the running ``SCHEMA_VERSION`` into ``schema_versions`` once. Idempotent.

    If a row for the current version already exists (backfilled, or recorded by an earlier
    run), this is a no-op -- ``first_encountered_at`` is never moved, so the recorded time is
    genuinely the first encounter. Commits only when it inserts.
    """
    if db.get(SchemaVersion, SCHEMA_VERSION) is not None:
        return
    db.add(
        SchemaVersion(
            version=SCHEMA_VERSION,
            first_encountered_at=utcnow(),
            diff_note=DIFF_NOTES.get(SCHEMA_VERSION),
        )
    )
    db.commit()
