"""Drop document_chunks table.

Revision ID: 011_drop_document_chunks
Revises: 010_triage_score
Create Date: 2026-05-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "011_drop_document_chunks"
down_revision: Union[str, None] = "010_triage_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_document_chunks_tender_id", table_name="document_chunks")
    op.drop_table("document_chunks")


def downgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tender_id", sa.String(64), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_filename", sa.String(512), nullable=True),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_chunks_tender_id", "document_chunks", ["tender_id"])
