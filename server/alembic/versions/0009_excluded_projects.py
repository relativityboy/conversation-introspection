"""excluded projects

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-17 00:00:00.000000

Adds the ``excluded_projects`` table (spec 2026-08-17 §2): one row per project the owner
has walled off from capture — discovery skips the slug's directory before reading anything
beneath it. Existence-based and user-data-layer like ``favorites``/``archived_sessions``:
import/reparse never write to or delete from it.

``dir_slug`` is the PK and deliberately NOT a foreign key to ``projects``: the whole point
of prevention is excluding projects that have never been captured, so no project row may
exist yet. ``reason`` is nullable free text (relativityboy 2026-08-17: "a spot for a
reason"). ``created_at`` is ``sa.String()`` per the 0001/0004 convention (UTCDateTime at
the ORM layer).

No preflights: adds a plain table; nothing can fail against a populated archive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'excluded_projects',
        sa.Column('dir_slug', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('dir_slug'),
    )


def downgrade() -> None:
    op.drop_table('excluded_projects')
