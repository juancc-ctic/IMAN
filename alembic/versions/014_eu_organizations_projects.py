"""Add eu_organizations, eu_projects, and eu_participations tables (CORDIS data).

Revision ID: 014_eu_organizations_projects
Revises: 013_eu_programme_parts
Create Date: 2026-05-11

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014_eu_organizations_projects"
down_revision: Union[str, None] = "013_eu_programme_parts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eu_organizations",
        sa.Column("organisation_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("country", sa.Text, nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lon", sa.Float, nullable=True),
        sa.Column("interest", sa.Text, nullable=True),
        sa.Column("why", sa.Text, nullable=True),
    )

    op.create_table(
        "eu_projects",
        sa.Column("project_id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("acronym", sa.Text, nullable=True),
        sa.Column("program", sa.String(10), nullable=True),
        sa.Column("keywords", sa.Text, nullable=True),
    )

    op.create_table(
        "eu_participations",
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("eu_projects.project_id"),
            primary_key=True,
        ),
        sa.Column(
            "organisation_id",
            sa.Integer,
            sa.ForeignKey("eu_organizations.organisation_id"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("total_cost", sa.Numeric(18, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("eu_participations")
    op.drop_table("eu_projects")
    op.drop_table("eu_organizations")
