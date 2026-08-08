"""authorship columns + transcript index (spec §4)"""
from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.add_column("messages", sa.Column("authorship_kind", sa.String(), nullable=True))
    op.add_column("messages", sa.Column("authorship_basis", sa.String(), nullable=True))
    op.add_column("messages", sa.Column("authorship_detail", sa.String(), nullable=True))
    op.create_index("ix_messages_transcript_id", "messages", ["transcript_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_messages_transcript_id", table_name="messages")
    op.drop_column("messages", "authorship_detail")
    op.drop_column("messages", "authorship_basis")
    op.drop_column("messages", "authorship_kind")
