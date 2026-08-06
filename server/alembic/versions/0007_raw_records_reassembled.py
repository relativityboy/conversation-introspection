"""raw_records.reassembled — capture-provenance marker

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-05 00:00:00.000000

Adds the ``reassembled`` column to mark capture provenance (spec §2): every
``raw_records`` row initially gets ``False`` (native single-line capture);
Task 3 sets ``True`` for multi-line units reconstructed from fragmented archives;
Task 5 copies the bit through recapture. Census logic must always distinguish native
from reassembled records — this column is the provenance marker.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raw_records",
        sa.Column("reassembled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("raw_records", "reassembled")
