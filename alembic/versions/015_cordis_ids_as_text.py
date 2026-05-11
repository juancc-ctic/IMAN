"""Change eu_organizations.organisation_id and eu_projects.project_id from integer to text.

Revision ID: 015_cordis_ids_as_text
Revises: 014_eu_organizations_projects
Create Date: 2026-05-11

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015_cordis_ids_as_text"
down_revision: Union[str, None] = "014_eu_organizations_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop FKs before altering referenced columns.
    op.drop_constraint("eu_participations_project_id_fkey", "eu_participations", type_="foreignkey")
    op.drop_constraint("eu_participations_organisation_id_fkey", "eu_participations", type_="foreignkey")

    op.alter_column(
        "eu_organizations", "organisation_id",
        type_=sa.Text,
        postgresql_using="organisation_id::text",
    )
    op.alter_column(
        "eu_projects", "project_id",
        type_=sa.Text,
        postgresql_using="project_id::text",
    )
    op.alter_column(
        "eu_participations", "project_id",
        type_=sa.Text,
        postgresql_using="project_id::text",
    )
    op.alter_column(
        "eu_participations", "organisation_id",
        type_=sa.Text,
        postgresql_using="organisation_id::text",
    )

    op.create_foreign_key(
        "eu_participations_project_id_fkey",
        "eu_participations", "eu_projects",
        ["project_id"], ["project_id"],
    )
    op.create_foreign_key(
        "eu_participations_organisation_id_fkey",
        "eu_participations", "eu_organizations",
        ["organisation_id"], ["organisation_id"],
    )


def downgrade() -> None:
    op.drop_constraint("eu_participations_project_id_fkey", "eu_participations", type_="foreignkey")
    op.drop_constraint("eu_participations_organisation_id_fkey", "eu_participations", type_="foreignkey")

    op.alter_column(
        "eu_participations", "organisation_id",
        type_=sa.Integer,
        postgresql_using="organisation_id::integer",
    )
    op.alter_column(
        "eu_participations", "project_id",
        type_=sa.Integer,
        postgresql_using="project_id::integer",
    )
    op.alter_column(
        "eu_projects", "project_id",
        type_=sa.Integer,
        postgresql_using="project_id::integer",
    )
    op.alter_column(
        "eu_organizations", "organisation_id",
        type_=sa.Integer,
        postgresql_using="organisation_id::integer",
    )

    op.create_foreign_key(
        "eu_participations_project_id_fkey",
        "eu_participations", "eu_projects",
        ["project_id"], ["project_id"],
    )
    op.create_foreign_key(
        "eu_participations_organisation_id_fkey",
        "eu_participations", "eu_organizations",
        ["organisation_id"], ["organisation_id"],
    )
