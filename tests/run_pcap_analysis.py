"""One-shot script: analyze tests/PCAP.pdf with the configured LLM and print result."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iman_ingestion.llm.client import analyze_tender_proposal, get_llm_client
from iman_ingestion.llm.pdf_to_images import (
    convert_pdf_to_base64_pngs,
    multimodal_dpi,
    multimodal_max_images_total,
    multimodal_max_pages_per_pdf,
)
from iman_ingestion.pdf_extract import extract_pdf_text
from iman_ingestion.triage.company_profile import load_company_profile
from iman_ingestion.triage.scorer import evaluate_tender

PDF = Path(__file__).parent / "PCAP.pdf"

title = os.environ.get("TENDER_TITLE", "")
party_name = os.environ.get("TENDER_PARTY", "")
tender_link = os.environ.get("TENDER_LINK", "")

use_multimodal = os.environ.get("IMAN_USE_MULTIMODAL_LLM", "true").lower() not in ("0", "false", "no")

pdf_text = f"=== PCAP (legal) (PCAP.pdf) ===\n{extract_pdf_text(PDF).strip()}"
print(f"Extracted {len(pdf_text)} chars of text", file=sys.stderr)

images: list[str] | None = None
if use_multimodal:
    try:
        images = convert_pdf_to_base64_pngs(PDF, max_pages=multimodal_max_pages_per_pdf(), dpi=multimodal_dpi())
        images = images[: multimodal_max_images_total()]
        print(f"Rasterized {len(images)} pages", file=sys.stderr)
    except RuntimeError as exc:
        print(f"pdftoppm unavailable, falling back to text-only: {exc}", file=sys.stderr)

client = get_llm_client()

print("--- Running LLM enrichment ---", file=sys.stderr)
enrichment = analyze_tender_proposal(
    client,
    pdf_text=pdf_text,
    image_base64_pngs=images,
    title=title,
    party_name=party_name,
    tender_link=tender_link,
)

print("--- Running triage evaluation ---", file=sys.stderr)
company_profile = load_company_profile()
triage = evaluate_tender(
    tender_id="tests/PCAP.pdf",
    title=title,
    party_name=party_name,
    tender_link=tender_link,
    enrichment=enrichment,
    llm_client=client,
    company_profile=company_profile,
)

print(json.dumps({"enrichment": enrichment, "triage": triage}, ensure_ascii=False, indent=2))
