"""Add cpv_codes column to tenders.

Revision ID: 002_add_cpv_codes
Revises: 001_consolidated
Create Date: 2026-05-28

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_add_cpv_codes"
down_revision: Union[str, None] = "001_consolidated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("cpv_codes", sa.ARRAY(sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("tenders", "cpv_codes")
