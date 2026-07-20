"""archived sessions

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-20 00:00:00.000000

Adds the ``archived_sessions`` table (§15.1): one row per hidden session, keyed by
``session_uuid`` (PK + FK to ``sessions``), existence-based like ``favorites``/``user_titles``
(a row present means the session is excluded from every API read path; absence means visible).
Import/reparse never touch it -- the same user-data invariant favorites/user_titles carry. See
``ArchivedSession`` in ``introspect.models`` and the archive API in
``introspect.api.routes.archive``.

``created_at`` is declared ``sa.String()`` here, same convention as 0001/0002/0003: at the ORM
layer it is ``introspect.db.UTCDateTime`` (a TypeDecorator over String storing ISO-8601 UTC
text), whose DDL is an ordinary text column -- this migration stays self-contained and decoupled
from app code.

No preflights: like 0003, this migration only adds a plain table with a foreign key -- nothing
here can fail against a populated archive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'archived_sessions',
        sa.Column('session_uuid', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['session_uuid'], ['sessions.session_uuid']),
        sa.PrimaryKeyConstraint('session_uuid'),
    )


def downgrade() -> None:
    op.drop_table('archived_sessions')
