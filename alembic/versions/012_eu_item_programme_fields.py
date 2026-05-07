"""Add programme fields to eu_items table.

Revision ID: 012_eu_item_programme_fields
Revises: 011_drop_document_chunks
Create Date: 2026-05-07

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012_eu_item_programme_fields"
down_revision: Union[str, None] = "011_drop_document_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("eu_items", sa.Column("framework_programme", sa.Text, nullable=True))
    op.add_column("eu_items", sa.Column("programme_period", sa.Text, nullable=True))
    op.add_column("eu_items", sa.Column("programme_division", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("eu_items", "programme_division")
    op.drop_column("eu_items", "programme_period")
    op.drop_column("eu_items", "framework_programme")
