"""Add triage columns to eu_items table.

Revision ID: 007_eu_item_triage
Revises: 006_tender_execution_period
Create Date: 2026-04-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "007_eu_item_triage"
down_revision: Union[str, None] = "006_tender_execution_period"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("eu_items", sa.Column("triage", JSONB, nullable=True))
    op.add_column("eu_items", sa.Column("triage_status", sa.String(32), nullable=True))
    op.create_index("ix_eu_items_triage_status", "eu_items", ["triage_status"])


def downgrade() -> None:
    op.drop_index("ix_eu_items_triage_status", table_name="eu_items")
    op.drop_column("eu_items", "triage_status")
    op.drop_column("eu_items", "triage")
