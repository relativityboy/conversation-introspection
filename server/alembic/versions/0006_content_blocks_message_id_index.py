"""content blocks message_id index

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-05 00:00:00.000000

Adds ``ix_content_blocks_message_id`` (walk-fix Task 9a). The 2026-08-05 refinements'
``chat_only`` trim (``_chat_only_filter`` in ``introspect.api.routes.sessions``) runs a
correlated ``EXISTS`` over ``content_blocks`` keyed on ``message_id`` at all four
``list_messages`` query sites. ``content_blocks`` had no indexes at all, so on a production
archive (~50k content_blocks rows) SQLite resolved each EXISTS probe with a full table
``SCAN content_blocks`` -- confirmed via ``EXPLAIN QUERY PLAN`` (``SCAN messages`` driving a
``CORRELATED SCALAR SUBQUERY`` that itself ``SCAN``s ``content_blocks``) and empirically
(the around+chat_only endpoint measured 10.6s, the count query alone 11.42s). This index lets
SQLite resolve the EXISTS with an index seek instead.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_content_blocks_message_id', 'content_blocks', ['message_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_content_blocks_message_id', table_name='content_blocks')
