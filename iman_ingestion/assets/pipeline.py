"""Dagster assets: raw ingestion, DB upsert, LLM enrichment, embeddings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dagster import asset
from sqlalchemy import delete, select

from iman_ingestion.aggregated.ingestion import (
    folder_name_from_tender_id,
    run_ingestion,
)
from iman_ingestion.db.models import DocumentChunk, Tender
from iman_ingestion.db.session import session_scope
from iman_ingestion.llm.client import (
    analyze_tender_proposal,
    embed_texts,
    get_embeddings_client,
    get_llm_client,
)
from iman_ingestion.llm.pdf_to_images import (
    convert_pdf_to_base64_pngs,
    multimodal_dpi,
    multimodal_max_images_total,
    multimodal_max_pages_per_pdf,
)
from iman_ingestion.pdf_extract import chunk_text, extract_pdf_text
from iman_ingestion.resources import ImanIngestionResource


def _collect_tender_pdf_text(downloads: Path, tender_id: str) -> str:
    """Load PCAP plain text from ``downloads/<folder>/`` (legal file only).

    Args:
        downloads: Base directory containing per-tender subfolders.
        tender_id: Atom tender id URL (folder name is the last path segment).

    Returns:
        Labeled PCAP text, or empty string if the file is missing.
    """
    folder = folder_name_from_tender_id(tender_id)
    base = downloads / folder
    path = base / "PCAP.pdf"
    if not path.is_file():
        return ""
    label, name = "PCAP (legal)", "PCAP.pdf"
    try:
        txt = extract_pdf_text(path)
        return f"=== {label} ({name}) ===\n{txt}"
    except Exception as exc:
        return f"=== {label} ({name}) ===\n[EXTRACTION_ERROR: {exc}]"


def _collect_tender_image_base64s(downloads: Path, tender_id: str) -> List[str]:
    """Rasterize PCAP only to base64 PNG pages (``pdftoppm``), capped in total."""
    folder = folder_name_from_tender_id(tender_id)
    base = downloads / folder
    max_pages = multimodal_max_pages_per_pdf()
    dpi = multimodal_dpi()
    cap = multimodal_max_images_total()
    path = base / "PCAP.pdf"
    if not path.is_file():
        return []
    try:
        imgs = convert_pdf_to_base64_pngs(path, max_pages=max_pages, dpi=dpi)
        return imgs[:cap]
    except Exception:
        return []


@asset(group_name="iman", compute_kind="python")
def raw_aggregated_ingestion(
    context,
    iman_ingestion: ImanIngestionResource,
) -> Dict[str, Any]:
    """Download filtered feed chain and PDFs; write tender JSON."""
    cfg = iman_ingestion.to_ingestion_config()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    result = run_ingestion(cfg, verbose=False)
    payload = {
        "json_path": str(cfg.json_out),
        "downloads_dir": str(cfg.output_dir),
        "tender_count": len(result.tenders_data),
        "total_downloads_attempted": result.total,
        "downloads_ok": result.ok,
    }
    context.add_output_metadata(
        {
            "tender_count": len(result.tenders_data),
            "json_path": str(cfg.json_out),
        }
    )
    return payload


@asset(group_name="iman", compute_kind="postgres")
def persist_tenders(
    raw_aggregated_ingestion: Dict[str, Any],
) -> int:
    """Upsert tender rows from the JSON produced by ingestion."""
    path = Path(raw_aggregated_ingestion["json_path"])
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Expected JSON array of tenders")

    count = 0
    with session_scope() as session:
        for row in raw:
            tid = row.get("id")
            if not tid:
                continue
            tender = Tender(
                id=tid,
                link=row.get("link"),
                title=row.get("title"),
                party_name=row.get("party_name"),
                tax_exclusive_amount=row.get("tax_exclusive_amount"),
                estimated_overall_contract_amount=row.get(
                    "estimated_overall_contract_amount"
                ),
            )
            session.merge(tender)
            count += 1
    return count


@asset(group_name="iman", compute_kind="openai")
def tender_llm_enrichment(
    persist_tenders: int,
    raw_aggregated_ingestion: Dict[str, Any],
) -> int:
    """Fill ``tenders.enrichment`` via multimodal LLM (PCAP pages as PNG) or text fallback."""
    if os.environ.get("IMAN_SKIP_LLM_ENRICHMENT", "").lower() in ("1", "true", "yes"):
        return 0
    downloads = Path(raw_aggregated_ingestion["downloads_dir"])
    llm_client = get_llm_client()
    updated = 0
    with session_scope() as session:
        tenders = list(session.scalars(select(Tender)).all())
        for t in tenders:
            pdf_text = _collect_tender_pdf_text(downloads, t.id)
            images = _collect_tender_image_base64s(downloads, t.id)
            data = analyze_tender_proposal(
                llm_client,
                pdf_text=pdf_text,
                image_base64_pngs=images,
                title=t.title or "",
                party_name=t.party_name or "",
                tender_link=t.link or "",
            )
            t.enrichment = data
            updated += 1
    return updated


@asset(group_name="iman", compute_kind="openai")
def document_embeddings(
    raw_aggregated_ingestion: Dict[str, Any],
    tender_llm_enrichment: int,
) -> int:
    """Extract PDF text, chunk, embed, and store rows in ``document_chunks``."""
    if os.environ.get("IMAN_SKIP_EMBEDDINGS", "").lower() in ("1", "true", "yes"):
        return 0
    downloads = Path(raw_aggregated_ingestion["downloads_dir"])
    json_path = Path(raw_aggregated_ingestion["json_path"])
    rows: List[dict] = json.loads(json_path.read_text(encoding="utf-8"))
    embeddings_client = get_embeddings_client()
    batch_size = int(os.environ.get("IMAN_EMBED_BATCH_SIZE", "16"))
    total_chunks = 0

    with session_scope() as session:
        for row in rows:
            tid = row.get("id")
            if not tid:
                continue
            folder = folder_name_from_tender_id(tid)
            base = downloads / folder
            pdf_parts: List[Tuple[str, str]] = []
            for name in ("PCAP.pdf", "PPT.pdf"):
                p = base / name
                if p.is_file():
                    try:
                        txt = extract_pdf_text(p)
                    except Exception as exc:
                        context_msg = f"pdf_extract_failed:{folder}:{name}:{exc}"
                        raise RuntimeError(context_msg) from exc
                    if txt.strip():
                        pdf_parts.append((name, txt))
            meta_lines = [
                row.get("title") or "",
                row.get("party_name") or "",
                row.get("tax_exclusive_amount") or "",
            ]
            meta_text = "\n".join(meta_lines)
            all_chunks: List[Tuple[str, Optional[str], int, str]] = []
            idx = 0
            if meta_text.strip():
                for c in chunk_text(meta_text, size=1500, overlap=100):
                    all_chunks.append(("metadata", None, idx, c))
                    idx += 1
            for fname, text in pdf_parts:
                for c in chunk_text(text, size=2000, overlap=200):
                    all_chunks.append(("pdf", fname, idx, c))
                    idx += 1

            session.execute(
                delete(DocumentChunk).where(DocumentChunk.tender_id == tid),
            )
            for kind, fname, chunk_i, text in all_chunks:
                session.add(
                    DocumentChunk(
                        tender_id=tid,
                        source_kind=kind,
                        source_filename=fname,
                        chunk_index=chunk_i,
                        text=text,
                    )
                )
                total_chunks += 1

            session.flush()
            pending = list(
                session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.tender_id == tid)
                    .order_by(DocumentChunk.chunk_index),
                ).all(),
            )
            texts = [c.text for c in pending]
            for off in range(0, len(texts), batch_size):
                batch = texts[off : off + batch_size]
                vecs = embed_texts(embeddings_client, batch)
                for chunk, vec in zip(pending[off : off + batch_size], vecs):
                    chunk.embedding = vec

    return total_chunks
