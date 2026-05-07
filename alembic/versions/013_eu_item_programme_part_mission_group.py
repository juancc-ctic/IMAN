"""Add programme_part and mission_group columns to eu_items table.

Revision ID: 013_eu_item_programme_part_mission_group
Revises: 012_eu_item_programme_fields
Create Date: 2026-05-07

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013_eu_programme_parts"
down_revision: Union[str, None] = "012_eu_item_programme_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("eu_items", sa.Column("programme_part", sa.Text, nullable=True))
    op.add_column("eu_items", sa.Column("mission_group", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("eu_items", "mission_group")
    op.drop_column("eu_items", "programme_part")
