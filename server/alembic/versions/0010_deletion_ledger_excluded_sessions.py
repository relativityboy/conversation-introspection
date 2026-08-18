"""deletion ledger + excluded sessions

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-17 00:00:00.000000

Two tables for reasoned deletion (spec 2026-08-17 §3):

* ``deletion_ledger`` — the tombstone layer: one row per deliberate deletion (kind
  session|project, target, display label, optional reason, counts). The archive remembers
  THAT it forgot, never WHAT. Nothing ever deletes ledger rows.
* ``excluded_sessions`` — the session-level re-import wall (resurrection guard),
  symmetric with ``excluded_projects`` (0009). ``session_uuid`` is deliberately NOT an FK:
  its primary use is forbidding re-import AFTER deletion, when no session row exists.

Both are user-data-layer: import/reparse never write to or delete from them. Timestamps
are ``sa.String()`` per the 0001/0004 convention (UTCDateTime at the ORM layer).

No preflights: plain table adds; nothing can fail against a populated archive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'excluded_sessions',
        sa.Column('session_uuid', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('session_uuid'),
    )
    op.create_table(
        'deletion_ledger',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('target', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('sessions_deleted', sa.Integer(), nullable=False),
        sa.Column('records_deleted', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('deletion_ledger')
    op.drop_table('excluded_sessions')
