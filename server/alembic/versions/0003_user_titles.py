"""user titles

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19 00:00:00.000000

Adds the ``user_titles`` table: one optional user-authored title override per session, keyed
by ``session_uuid`` (PK + FK to ``sessions``), existence-based like ``favorites`` (a row
present means the user has set a custom title; absence means fall back to the archive-derived
``ai_title``/``custom_title``). See ``UserTitle`` in ``introspect.models`` and the title API
in ``introspect.api.routes.titles``.

Datetime columns are declared as ``sa.String()`` here, same convention as 0001/0002: at the
ORM layer ``updated_at`` is ``introspect.db.UTCDateTime`` (a TypeDecorator over String storing
ISO-8601 UTC text), whose DDL is an ordinary text column -- this migration stays self-contained
and decoupled from app code.

No preflights: unlike 0002 (FTS5 availability, primary-uniqueness), this migration only adds a
plain table with a foreign key -- nothing here can fail against a populated archive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_titles',
        sa.Column('session_uuid', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['session_uuid'], ['sessions.session_uuid']),
        sa.PrimaryKeyConstraint('session_uuid'),
    )


def downgrade() -> None:
    op.drop_table('user_titles')
