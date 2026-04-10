"""Add eu_items table.

Revision ID: 002_eu_items
Revises: 001_initial
Create Date: 2026-04-09

"""

from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002_eu_items"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dim() -> int:
    return int(os.environ.get("IMAN_EMBEDDING_DIMENSION", "1024"))


def upgrade() -> None:
    op.create_table(
        "eu_items",
        sa.Column("reference", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("identifier", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("start_date", sa.String(length=64), nullable=True),
        sa.Column("deadline_date", sa.String(length=64), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("embed_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(_dim()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("reference"),
    )
    op.create_index("ix_eu_items_kind", "eu_items", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_eu_items_kind", table_name="eu_items")
    op.drop_table("eu_items")
