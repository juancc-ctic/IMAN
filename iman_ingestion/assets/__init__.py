"""Dagster assets."""

from iman_ingestion.assets.eu_pipeline import (
    eu_item_embeddings,
    persist_eu_items,
    raw_eu_ingestion,
)
from iman_ingestion.assets.pipeline import (
    document_embeddings,
    persist_tenders,
    raw_aggregated_ingestion,
    tender_llm_enrichment,
)

__all__ = [
    "document_embeddings",
    "eu_item_embeddings",
    "persist_eu_items",
    "persist_tenders",
    "raw_aggregated_ingestion",
    "raw_eu_ingestion",
    "tender_llm_enrichment",
]
