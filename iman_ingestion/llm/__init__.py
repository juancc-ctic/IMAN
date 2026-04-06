"""OpenAI-compatible clients for embeddings and chat."""

from iman_ingestion.llm.client import (
    analyze_tender_proposal,
    embed_texts,
    enrich_tender_summary,
    get_embeddings_client,
    get_llm_client,
    get_openai_client,
)

__all__ = [
    "analyze_tender_proposal",
    "embed_texts",
    "enrich_tender_summary",
    "get_embeddings_client",
    "get_llm_client",
    "get_openai_client",
]
