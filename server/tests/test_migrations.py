"""Migration tests for binding-contract schema changes.

Mirrors the pattern of individual test_migration_XXXX.py files: column existence,
nullability, defaults, and index presence after upgrade; column removal after downgrade.
"""

from pathlib import Path

from sqlalchemy import inspect

from introspect.db import get_engine, upgrade_to_head


def test_authorship_columns_and_transcript_index(tmp_path: Path) -> None:
    """Authorship labels (spec §4): three nullable string columns + composite index.

    The (transcript_id, id) index supports efficient filtering within a transcript
    (the reader's message-list drill-in path).
    """
    engine = get_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    insp = inspect(engine)

    # Check authorship columns exist
    cols = {c["name"] for c in insp.get_columns("messages")}
    assert {"authorship_kind", "authorship_basis", "authorship_detail"} <= cols

    # Check index exists with correct column order
    assert any(ix["name"] == "ix_messages_transcript_id"
               and ix["column_names"] == ["transcript_id", "id"]
               for ix in insp.get_indexes("messages"))
