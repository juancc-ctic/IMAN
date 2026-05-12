"""Add title column to eu_projects.

Revision ID: 002_eu_project_title
Revises: 001_initial
Create Date: 2026-05-12

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_eu_project_title"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("eu_projects", sa.Column("title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("eu_projects", "title")
