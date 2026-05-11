"""Add company_profile singleton table with action-plan embedding.

Revision ID: 016_company_profile
Revises: 015_cordis_ids_as_text
Create Date: 2026-05-11

"""

from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "016_company_profile"
down_revision: Union[str, None] = "015_cordis_ids_as_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIM = int(os.environ.get("IMAN_EMBEDDING_DIMENSION", "1024"))


def upgrade() -> None:
    op.create_table(
        "company_profile",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("interest_areas", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("company_fields", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("past_tender_categories", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("triage_dimensions", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("tender_filters", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("action_plan_text", sa.Text, nullable=True),
        sa.Column("action_plan_embedding", Vector(_DIM), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("company_profile")
