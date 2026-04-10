"""SQLAlchemy models for tender metadata and embedding chunks."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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

    id: Mapped[str] = mapped_column(String(2048), primary_key=True)
    link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    party_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tax_exclusive_amount: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    estimated_overall_contract_amount: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    enrichment: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    chunks: Mapped[List["DocumentChunk"]] = relationship(
        "DocumentChunk",
        back_populates="tender",
        cascade="all, delete-orphan",
    )


class DocumentChunk(Base):
    """Text chunk with optional embedding vector."""

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(
        String(2048),
        ForeignKey("tenders.id", ondelete="CASCADE"),
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(32))
    source_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(_embedding_dimensions()),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    tender: Mapped["Tender"] = relationship("Tender", back_populates="chunks")


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
    item_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB, nullable=True)
    embed_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(_embedding_dimensions()),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
