"""Add triage JSONB and triage_status columns to tenders.

Revision ID: 005_tender_triage
Revises: 004_shorten_tender_id
Create Date: 2026-04-21

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "005_tender_triage"
down_revision: Union[str, None] = "004_shorten_tender_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("triage", JSONB, nullable=True))
    op.add_column("tenders", sa.Column("triage_status", sa.String(32), nullable=True))
    op.create_index("ix_tenders_triage_status", "tenders", ["triage_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tenders_triage_status", table_name="tenders")
    op.drop_column("tenders", "triage_status")
    op.drop_column("tenders", "triage")
