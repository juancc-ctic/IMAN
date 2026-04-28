"""Add pcap_url and ppt_url columns to tenders.

Revision ID: 008_tender_pdf_urls
Revises: 007_eu_item_triage
Create Date: 2026-04-28

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_tender_pdf_urls"
down_revision: Union[str, None] = "007_eu_item_triage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("pcap_url", sa.Text, nullable=True))
    op.add_column("tenders", sa.Column("ppt_url", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("tenders", "ppt_url")
    op.drop_column("tenders", "pcap_url")
