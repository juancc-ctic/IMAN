"""Tender Lifecycle: current Tender Status, tracking state, timeline and Awards.

Revision ID: 002_tender_lifecycle
Revises: 001_consolidated
Create Date: 2026-11-13

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002_tender_lifecycle"
down_revision: Union[str, None] = "001_consolidated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("status", sa.String(8), nullable=True))
    op.add_column("tenders", sa.Column("feed_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenders", sa.Column("lots", JSONB, nullable=True))
    op.add_column(
        "tenders",
        sa.Column("tracked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("tenders", sa.Column("tracked_by", sa.Text(), nullable=True))
    op.add_column("tenders", sa.Column("tracked_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenders", sa.Column("tracking_ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenders", sa.Column("tracking_end_reason", sa.String(16), nullable=True))
    op.create_index("ix_tenders_tracked", "tenders", ["tracked"])

    op.create_table(
        "tender_timeline",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tender_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by", sa.Text(), nullable=True),
        sa.Column("status_before", sa.String(8), nullable=True),
        sa.Column("status_after", sa.String(8), nullable=True),
        sa.Column("changes", JSONB, nullable=True),
        sa.Column("snapshot", JSONB, nullable=True),
        sa.Column("end_reason", sa.String(16), nullable=True),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tender_timeline_tender_id", "tender_timeline", ["tender_id"])

    op.create_table(
        "tender_awards",
        sa.Column("tender_id", sa.String(64), nullable=False),
        sa.Column("lot_id", sa.String(64), nullable=False),
        sa.Column("lot_name", sa.Text(), nullable=True),
        sa.Column("result_code", sa.String(8), nullable=True),
        sa.Column("void", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("winner_name", sa.Text(), nullable=True),
        sa.Column("winner_nif", sa.String(32), nullable=True),
        sa.Column("award_amount", sa.String(64), nullable=True),
        sa.Column("bids_received", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tender_id", "lot_id"),
    )


def downgrade() -> None:
    op.drop_table("tender_awards")
    op.drop_index("ix_tender_timeline_tender_id", table_name="tender_timeline")
    op.drop_table("tender_timeline")
    op.drop_index("ix_tenders_tracked", table_name="tenders")
    for col in (
        "tracking_end_reason",
        "tracking_ended_at",
        "tracked_since",
        "tracked_by",
        "tracked",
        "lots",
        "feed_updated_at",
        "status",
    ):
        op.drop_column("tenders", col)
