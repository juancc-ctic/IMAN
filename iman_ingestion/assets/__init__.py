"""Dagster assets."""

from iman_ingestion.assets.pipeline import (
    document_embeddings,
    persist_tenders,
    raw_aggregated_ingestion,
    tender_llm_enrichment,
)

__all__ = [
    "document_embeddings",
    "persist_tenders",
    "raw_aggregated_ingestion",
    "tender_llm_enrichment",
]
