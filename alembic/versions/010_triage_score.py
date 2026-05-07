"""Replace triage_status (String) with triage_score (Float) on tenders and eu_items.

Revision ID: 010_triage_score
Revises: 009_tender_submission_deadline
Create Date: 2026-05-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_triage_score"
down_revision: Union[str, None] = "009_tender_submission_deadline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_tenders_triage_status", table_name="tenders")
    op.drop_column("tenders", "triage_status")
    op.add_column("tenders", sa.Column("triage_score", sa.Float, nullable=True))
    op.create_index("ix_tenders_triage_score", "tenders", ["triage_score"])

    op.drop_index("ix_eu_items_triage_status", table_name="eu_items")
    op.drop_column("eu_items", "triage_status")
    op.add_column("eu_items", sa.Column("triage_score", sa.Float, nullable=True))
    op.create_index("ix_eu_items_triage_score", "eu_items", ["triage_score"])


def downgrade() -> None:
    op.drop_index("ix_eu_items_triage_score", table_name="eu_items")
    op.drop_column("eu_items", "triage_score")
    op.add_column("eu_items", sa.Column("triage_status", sa.String(32), nullable=True))
    op.create_index("ix_eu_items_triage_status", "eu_items", ["triage_status"])

    op.drop_index("ix_tenders_triage_score", table_name="tenders")
    op.drop_column("tenders", "triage_score")
    op.add_column("tenders", sa.Column("triage_status", sa.String(32), nullable=True))
    op.create_index("ix_tenders_triage_status", "tenders", ["triage_status"])
