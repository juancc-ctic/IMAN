"""Dagster assets: raw ingestion, DB upsert, LLM enrichment, embeddings."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from dagster import asset

from iman_ingestion.aggregated.ingestion import (
    folder_name_from_tender_id,
    run_ingestion,
)
from iman_ingestion.db.models import Tender
from iman_ingestion.db.session import session_scope
from iman_ingestion.llm.client import (
    IMAN_ENRICHMENT_TOTAL_PAGES_KEY,
    analyze_tender_proposal,
    chat_model_name,
    embed_texts,
    get_embeddings_client,
    get_llm_client,
    resolved_llm_base_url,
)
from iman_ingestion.llm.pdf_to_images import (
    convert_pdf_to_base64_pngs,
    multimodal_dpi,
    multimodal_max_images_total,
    multimodal_max_pages_per_pdf,
)
from iman_ingestion.pdf_extract import extract_pdf_text
from iman_ingestion.resources import ImanIngestionResource

_SKIP_VALUES = ("1", "true", "yes")


def _env_skip(var: str) -> bool:
    return os.environ.get(var, "").lower() in _SKIP_VALUES


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
                submission_deadline=row.get("submission_deadline"),
                pcap_url=row.get("pcap_url"),
                ppt_url=row.get("ppt_url"),
            )
            session.merge(tender)
            count += 1
    return count


@asset(group_name="iman", compute_kind="openai")
def tender_llm_enrichment(
    context,
    persist_tenders: int,
    raw_aggregated_ingestion: Dict[str, Any],
) -> int:
    """Fill ``tenders.enrichment`` via multimodal LLM (PCAP pages as PNG) or text fallback."""
    if _env_skip("IMAN_SKIP_LLM_ENRICHMENT"):
        context.log.info("IMAN_SKIP_LLM_ENRICHMENT is set; skipping LLM enrichment.")
        return 0
    downloads = Path(raw_aggregated_ingestion["downloads_dir"])
    mm_env = os.environ.get("IMAN_USE_MULTIMODAL_LLM", "true")
    context.log.info(
        "tender_llm_enrichment: model=%r base_url=%r IMAN_USE_MULTIMODAL_LLM=%r "
        "downloads=%s",
        chat_model_name(),
        resolved_llm_base_url(),
        mm_env,
        downloads,
    )
    llm_client = get_llm_client()
    updated = 0
    pipeline_start = time.perf_counter()
    rows: List[dict] = json.loads(
        Path(raw_aggregated_ingestion["json_path"]).read_text(encoding="utf-8")
    )
    n = len(rows)
    context.log.info("Enriching %d tender row(s) from current run.", n)
    with session_scope() as session:
        for i, row in enumerate(rows, start=1):
            tid = row.get("id")
            if not tid:
                continue
            t = session.get(Tender, tid)
            if not t:
                continue
            folder = folder_name_from_tender_id(t.id)
            pcap_path = downloads / folder / "PCAP.pdf"
            t0 = time.perf_counter()
            pdf_text = _collect_tender_pdf_text(downloads, t.id)
            images = _collect_tender_image_base64s(downloads, t.id)
            use_mm = mm_env.lower() not in ("0", "false", "no") and bool(images)
            context.log.info(
                "[%d/%d] folder=%s PCAP_exists=%s pdf_text_chars=%d raster_pages=%d "
                "use_multimodal=%s title=%r",
                i,
                n,
                folder,
                pcap_path.is_file(),
                len(pdf_text),
                len(images),
                use_mm,
                (t.title or "")[:120],
            )
            data = analyze_tender_proposal(
                llm_client,
                pdf_text=pdf_text,
                image_base64_pngs=images,
                title=t.title or "",
                party_name=t.party_name or "",
                tender_link=t.link or "",
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            pages_meta = data.get(IMAN_ENRICHMENT_TOTAL_PAGES_KEY)
            if data.get("parse_error"):
                note = (
                    (data.get("outsourcing") or {}).get("notes")
                    or (data.get("discard_review") or {}).get("summary")
                    or ""
                )
                context.log.warning(
                    "[%d/%d] completed in %.0f ms (pages_meta=%s) parse_error=True "
                    "detail=%r",
                    i,
                    n,
                    elapsed_ms,
                    pages_meta,
                    note[:500],
                )
            else:
                context.log.info(
                    "[%d/%d] completed in %.0f ms (pages_meta=%s)",
                    i,
                    n,
                    elapsed_ms,
                    pages_meta,
                )
            t.enrichment = data
            ep = data.get("execution_period")
            if ep and isinstance(ep, str):
                t.execution_period = ep.strip() or None
            updated += 1
            shutil.rmtree(downloads / folder, ignore_errors=True)
    total_s = time.perf_counter() - pipeline_start
    context.log.info(
        "tender_llm_enrichment finished: enriched=%d in %.2f s",
        updated,
        total_s,
    )
    context.add_output_metadata(
        {
            "tenders_enriched": updated,
            "total_seconds": round(total_s, 3),
        }
    )
    return updated


@asset(group_name="iman", compute_kind="openai")
def tender_triage(
    context,
    raw_aggregated_ingestion: Dict[str, Any],
    tender_llm_enrichment: int,
) -> int:
    """Evaluate each enriched tender against the company profile and assign a triage status."""
    if _env_skip("IMAN_SKIP_TRIAGE"):
        context.log.info("IMAN_SKIP_TRIAGE is set; skipping triage.")
        return 0

    from iman_ingestion.triage import evaluate_tender, load_company_profile

    company_profile = load_company_profile()
    llm_client = get_llm_client()
    pipeline_start = time.perf_counter()
    counters: Dict[str, int] = {"evaluated": 0, "skipped": 0}

    rows: List[dict] = json.loads(
        Path(raw_aggregated_ingestion["json_path"]).read_text(encoding="utf-8")
    )
    n = len(rows)
    context.log.info(
        "tender_triage: evaluating %d tender(s) from current run; model=%r",
        n,
        chat_model_name(),
    )
    with session_scope() as session:
        for i, row in enumerate(rows, start=1):
            tid = row.get("id")
            if not tid:
                continue
            t = session.get(Tender, tid)
            if not t:
                continue
            t0 = time.perf_counter()
            try:
                result = evaluate_tender(
                    tender_id=t.id,
                    title=t.title or "",
                    party_name=t.party_name or "",
                    tender_link=t.link or "",
                    enrichment=t.enrichment,
                    llm_client=llm_client,
                    company_profile=company_profile,
                )
            except Exception as exc:
                context.log.warning("[%d/%d] triage failed for %r: %s", i, n, t.id, exc)
                counters["skipped"] += 1
                continue
            t.triage = result
            t.triage_score = result.get("overall_score")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            context.log.info(
                "[%d/%d] id=%r score=%s dims=%d elapsed=%.0f ms",
                i,
                n,
                t.id,
                result.get("overall_score"),
                len(result.get("dimensions") or []),
                elapsed_ms,
            )
            counters["evaluated"] += 1

    total_s = time.perf_counter() - pipeline_start
    context.log.info("tender_triage finished in %.2f s: %s", total_s, counters)
    context.add_output_metadata(
        {
            "evaluated": counters["evaluated"],
            "skipped": counters["skipped"],
            "total_seconds": round(total_s, 3),
        }
    )
    return counters["evaluated"]


@asset(group_name="iman", compute_kind="openai")
def tender_embeddings(
    raw_aggregated_ingestion: Dict[str, Any],
    tender_llm_enrichment: int,
) -> int:
    """Embed each tender's LLM-generated summary and store it in ``tenders``."""
    if _env_skip("IMAN_SKIP_EMBEDDINGS"):
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
            tender_db = session.get(Tender, tid)
            if not (tender_db and isinstance(tender_db.enrichment, dict)):
                continue
            summary = tender_db.enrichment.get("summary", "").strip()
            if not summary:
                continue

            vecs = embed_texts(embeddings_client, [summary])
            tender_db.summary = summary
            tender_db.summary_embedding = vecs[0]
            total_chunks += 1

    return total_chunks
