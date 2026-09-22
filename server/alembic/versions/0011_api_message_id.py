"""messages.api_message_id

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-20 00:00:00.000000

The Claude Code transcript JSONL's assistant records carry the API message id
(``message.id``, e.g. ``"msg_..."``) -- already parsed into ``AssistantMessage.id`` at the
schema layer (``schema/v1.py``) but discarded by interpretation until now. One API message
can be split across multiple JSONL lines (observed in production transcripts: a text-block
line followed by a tool_use-block line sharing the same ``message.id``), so this is not a
per-row unique id -- it groups rows, hence the index rather than a uniqueness constraint.

Adds nullable ``api_message_id`` (``sa.String()``) to ``messages`` plus
``ix_messages_api_message_id``, in house style with ``ix_content_blocks_message_id`` (0006).

No preflight: nullable column add is safe on a populated archive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('api_message_id', sa.String(), nullable=True))
    op.create_index(
        'ix_messages_api_message_id', 'messages', ['api_message_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_messages_api_message_id', table_name='messages')
    op.drop_column('messages', 'api_message_id')
