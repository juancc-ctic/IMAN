"""Add execution_period column to tenders.

Revision ID: 006_tender_execution_period
Revises: 005_tender_triage
Create Date: 2026-04-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006_tender_execution_period"
down_revision: Union[str, None] = "005_tender_triage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("execution_period", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("tenders", "execution_period")
