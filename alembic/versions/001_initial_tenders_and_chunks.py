"""Initial tenders and document_chunks with pgvector.

Revision ID: 001_initial
Revises:
Create Date: 2026-04-01

"""

from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dim() -> int:
    return int(os.environ.get("IMAN_EMBEDDING_DIMENSION", "1024"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "tenders",
        sa.Column("id", sa.String(length=2048), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("party_name", sa.Text(), nullable=True),
        sa.Column("tax_exclusive_amount", sa.String(length=64), nullable=True),
        sa.Column("estimated_overall_contract_amount", sa.String(length=64), nullable=True),
        sa.Column("enrichment", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tender_id", sa.String(length=2048), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(_dim()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_chunks_tender_id",
        "document_chunks",
        ["tender_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_tender_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_table("tenders")
