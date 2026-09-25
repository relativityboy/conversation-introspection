"""project cwd from root sessions

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25 00:00:00.000000

Re-derives ``projects.resolved_cwd`` from root (main) transcripts only. Before this release,
capture named a project after the first captured record carrying a cwd -- which could be a
subagent's, when the subagent ran in another directory and its file was captured before the
main one. The result was a project wearing another project's name (two sidebar entries both
called ``claude-burnup``). Capture now only lets main-transcript records name a project
(``ingest/capture.py``); this migration converges archives captured under the old rule.

Rule (mirrors live capture): a project's resolved_cwd is the ``cwd`` of the first-captured
(lowest ``raw_records.id``) main-transcript record that carries one, or NULL when no root
record does -- a subagent-derived name is wrong, and NULL falls back to the slug-derived label.
Data-only: no raw bytes are touched and interpretation does not re-run.

Downgrade is a no-op: the prior values were the bug, and nothing depends on restoring them.
"""
import json
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _first_root_cwd(conn, project_id: int) -> str | None:
    rows = conn.exec_driver_sql(
        "SELECT r.raw_line FROM raw_records r "
        "JOIN transcripts t ON t.id = r.transcript_id "
        "JOIN sessions s ON s.session_uuid = t.session_id "
        "WHERE s.project_id = ? AND t.kind = 'main' "
        "ORDER BY r.id",
        (project_id,),
    )
    for (raw,) in rows:
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue  # malformed lines are recorded anomalies, never a name source
        cwd = record.get("cwd") if isinstance(record, dict) else None
        if isinstance(cwd, str) and cwd:
            return cwd
    return None


def upgrade() -> None:
    conn = op.get_bind()
    for project_id, current in conn.exec_driver_sql(
        "SELECT id, resolved_cwd FROM projects"
    ).all():
        derived = _first_root_cwd(conn, project_id)
        if derived != current:
            conn.exec_driver_sql(
                "UPDATE projects SET resolved_cwd = ? WHERE id = ?", (derived, project_id)
            )


def downgrade() -> None:
    pass
