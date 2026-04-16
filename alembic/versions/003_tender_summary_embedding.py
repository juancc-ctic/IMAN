"""Add summary and summary_embedding columns to tenders.

Revision ID: 003_tender_summary_embedding
Revises: 002_eu_items
Create Date: 2026-04-16

"""

from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "003_tender_summary_embedding"
down_revision: Union[str, None] = "002_eu_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dim() -> int:
    return int(os.environ.get("IMAN_EMBEDDING_DIMENSION", "1024"))


def upgrade() -> None:
    op.add_column("tenders", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("tenders", sa.Column("summary_embedding", Vector(_dim()), nullable=True))


def downgrade() -> None:
    op.drop_column("tenders", "summary_embedding")
    op.drop_column("tenders", "summary")
