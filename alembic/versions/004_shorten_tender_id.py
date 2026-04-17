"""Shorten tender id from full URL to numeric trailing segment.

Revision ID: 004_shorten_tender_id
Revises: 003_tender_summary_embedding
Create Date: 2026-04-17

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_shorten_tender_id"
down_revision: Union[str, None] = "003_tender_summary_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop FK so we can update the referenced PK column first.
    op.drop_constraint(
        "document_chunks_tender_id_fkey", "document_chunks", type_="foreignkey"
    )

    # Strip URL prefix from both columns: keep only the segment after the last '/'.
    op.execute(
        "UPDATE tenders SET id = REVERSE(SPLIT_PART(REVERSE(id), '/', 1)) WHERE id LIKE '%/%'"
    )
    op.execute(
        "UPDATE document_chunks SET tender_id = REVERSE(SPLIT_PART(REVERSE(tender_id), '/', 1))"
        " WHERE tender_id LIKE '%/%'"
    )

    # Shrink column widths.
    op.alter_column("tenders", "id", type_=sa.String(64), existing_nullable=False)
    op.alter_column(
        "document_chunks", "tender_id", type_=sa.String(64), existing_nullable=False
    )

    # Re-create FK.
    op.create_foreign_key(
        "document_chunks_tender_id_fkey",
        "document_chunks",
        "tenders",
        ["tender_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "document_chunks_tender_id_fkey", "document_chunks", type_="foreignkey"
    )

    op.alter_column("tenders", "id", type_=sa.String(2048), existing_nullable=False)
    op.alter_column(
        "document_chunks", "tender_id", type_=sa.String(2048), existing_nullable=False
    )

    op.create_foreign_key(
        "document_chunks_tender_id_fkey",
        "document_chunks",
        "tenders",
        ["tender_id"],
        ["id"],
        ondelete="CASCADE",
    )
