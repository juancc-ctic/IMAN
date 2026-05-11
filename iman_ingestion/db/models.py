"""SQLAlchemy models for tender metadata and embedding chunks."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _embedding_dimensions() -> int:
    # Default matches Snowflake/snowflake-arctic-embed-l-v2.0 (override if your model differs).
    return int(os.environ.get("IMAN_EMBEDDING_DIMENSION", "1024"))


class Base(DeclarativeBase):
    """Declarative base."""

    pass


class Tender(Base):
    """One syndicated procurement notice (from Atom JSON)."""

    __tablename__ = "tenders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    party_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tax_exclusive_amount: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    estimated_overall_contract_amount: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    pcap_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ppt_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enrichment: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(_embedding_dimensions()),
        nullable=True,
    )
    submission_deadline: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    execution_period: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    triage: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    triage_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class EuItem(Base):
    """One EU Funding & Tenders item (topic or call) with a single embedding vector."""

    __tablename__ = "eu_items"

    reference: Mapped[str] = mapped_column(String(1024), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # horizon-topic | non-horizon-topic | horizon-call
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    identifier: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    start_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    deadline_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    framework_programme: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    programme_period: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    programme_division: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    programme_part: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mission_group: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    item_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB, nullable=True)
    embed_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(_embedding_dimensions()),
        nullable=True,
    )
    triage: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    triage_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class EuOrganization(Base):
    """A CORDIS-registered organisation (participant or coordinator in EU projects)."""

    __tablename__ = "eu_organizations"

    organisation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    interest: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    participations: Mapped[List["EuParticipation"]] = relationship(
        back_populates="organization"
    )


class EuProject(Base):
    """A CORDIS EU-funded project."""

    __tablename__ = "eu_projects"

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    acronym: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    program: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    participations: Mapped[List["EuParticipation"]] = relationship(
        back_populates="project"
    )


class EuParticipation(Base):
    """Membership of an organisation in an EU project (role + cost share)."""

    __tablename__ = "eu_participations"

    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("eu_projects.project_id"), primary_key=True
    )
    organisation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("eu_organizations.organisation_id"), primary_key=True
    )
    role: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    total_cost: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)

    project: Mapped["EuProject"] = relationship(back_populates="participations")
    organization: Mapped["EuOrganization"] = relationship(back_populates="participations")
