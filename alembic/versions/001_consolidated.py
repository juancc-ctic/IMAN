"""Consolidated initial schema.

Revision ID: 001_consolidated
Revises:
Create Date: 2026-05-12

"""

from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001_consolidated"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dim() -> int:
    return int(os.environ.get("IMAN_EMBEDDING_DIMENSION", "1024"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenders",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("party_name", sa.Text(), nullable=True),
        sa.Column("tax_exclusive_amount", sa.String(64), nullable=True),
        sa.Column("estimated_overall_contract_amount", sa.String(64), nullable=True),
        sa.Column("enrichment", JSONB, nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_embedding", Vector(_dim()), nullable=True),
        sa.Column("triage", JSONB, nullable=True),
        sa.Column("triage_score", sa.Float(), nullable=True),
        sa.Column("execution_period", sa.Text(), nullable=True),
        sa.Column("pcap_url", sa.Text(), nullable=True),
        sa.Column("ppt_url", sa.Text(), nullable=True),
        sa.Column("submission_deadline", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenders_triage_score", "tenders", ["triage_score"])

    op.create_table(
        "eu_items",
        sa.Column("reference", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("identifier", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("start_date", sa.String(64), nullable=True),
        sa.Column("deadline_date", sa.String(64), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("embed_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(_dim()), nullable=True),
        sa.Column("triage", JSONB, nullable=True),
        sa.Column("triage_score", sa.Float(), nullable=True),
        sa.Column("framework_programme", sa.Text(), nullable=True),
        sa.Column("programme_period", sa.Text(), nullable=True),
        sa.Column("programme_division", sa.Text(), nullable=True),
        sa.Column("programme_part", sa.Text(), nullable=True),
        sa.Column("mission_group", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("reference"),
    )
    op.create_index("ix_eu_items_kind", "eu_items", ["kind"])
    op.create_index("ix_eu_items_triage_score", "eu_items", ["triage_score"])

    op.create_table(
        "eu_organizations",
        sa.Column("organisation_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("interest", sa.Text(), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("organisation_id"),
    )

    op.create_table(
        "eu_projects",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("acronym", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("program", sa.String(10), nullable=True),
        sa.Column("keywords", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(_dim()), nullable=True),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.execute(
        """
        CREATE INDEX ix_eu_projects_embedding_hnsw
        ON eu_projects
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    op.create_table(
        "eu_participations",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("organisation_id", sa.Text(), nullable=False),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("total_cost", sa.Numeric(18, 2), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["eu_projects.project_id"]),
        sa.ForeignKeyConstraint(["organisation_id"], ["eu_organizations.organisation_id"]),
        sa.PrimaryKeyConstraint("project_id", "organisation_id"),
    )

    op.create_table(
        "company_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("interest_areas", JSONB, nullable=True),
        sa.Column("company_fields", JSONB, nullable=True),
        sa.Column("past_tender_categories", JSONB, nullable=True),
        sa.Column("triage_dimensions", JSONB, nullable=True),
        sa.Column("tender_filters", JSONB, nullable=True),
        sa.Column("action_plan_text", sa.Text(), nullable=True),
        sa.Column("action_plan_embedding", Vector(_dim()), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("company_profile")
    op.drop_table("eu_participations")
    op.execute("DROP INDEX IF EXISTS ix_eu_projects_embedding_hnsw")
    op.drop_table("eu_projects")
    op.drop_table("eu_organizations")
    op.drop_index("ix_eu_items_triage_score", table_name="eu_items")
    op.drop_index("ix_eu_items_kind", table_name="eu_items")
    op.drop_table("eu_items")
    op.drop_index("ix_tenders_triage_score", table_name="tenders")
    op.drop_table("tenders")
