"""Add submission_deadline column to tenders.

Revision ID: 009_tender_submission_deadline
Revises: 008_tender_pdf_urls
Create Date: 2026-04-30

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009_tender_submission_deadline"
down_revision: Union[str, None] = "008_tender_pdf_urls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("submission_deadline", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("tenders", "submission_deadline")
