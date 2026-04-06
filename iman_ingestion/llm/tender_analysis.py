"""System and user prompts for technology-center tender proposal analysis."""

from __future__ import annotations

import os
from typing import Any, Dict

# ---------------------------------------------------------------------------
# System prompt (identity + mission)
# ---------------------------------------------------------------------------

TENDER_ANALYSIS_SYSTEM_PROMPT = """You are an assistant deployed at a technology center that specializes in digital services and innovation. The center's focus areas include, among others: software / IT development, digital transformation, artificial intelligence, quantum computing, and related technology domains.

Your role is to help the organization analyze, sort, and grade project proposals (public procurement notices and technical / legal documents) according to the center's strategic interests. You must be precise, grounded in the documents provided, and explicit when information is missing or unclear.

Always respond with a single valid JSON object only, no markdown fences, no commentary before or after the JSON. Use null for unknown or not found values where a structured field is expected; use empty strings only when the schema asks for strings and the value is truly empty."""


# Shared JSON schema instructions (text or image input).
_TENDER_JSON_SCHEMA_AND_RULES = """
Extract and return a JSON object with exactly this structure (all string values in the language of the documents when possible, or Spanish/English as appropriate):

{{
  "object_of_the_contract": "string: what is being contracted",
  "scope_of_the_work": "string: main scope and deliverables",
  "packages": null | [{{ "label": "string", "description": "string or null", "budget": "string or null" }}],
  "economic_solvency": "string: requirements or guarantees mentioned",
  "required_profiles": "string: roles, qualifications, team requirements",
  "assessment_criteria": "string: how the bid will be evaluated; include point breakdown if present",
  "outsourcing": {{
    "exists": true | false | null,
    "percentage": "string or null (e.g. max % subcontracting allowed)",
    "notes": "string"
  }},
  "discard_review": {{
    "summary": "string: short rationale for a human reviewer",
    "potential_discard": true | false | null,
    "reasons_for_manual_review": ["string"],
    "criteria_flags": {{
      "place_of_execution_not_asturias": {{ "applies": true | false | null, "evidence": "string" }},
      "execution_period_under_2_months": {{ "applies": true | false | null, "evidence": "string" }},
      "maintenance_longer_than_1_year": {{ "applies": true | false | null, "evidence": "string" }},
      "asks_technical_assistance_service": {{ "applies": true | false | null, "evidence": "string" }},
      "iso_certification_required": {{ "applies": true | false | null, "evidence": "string" }},
      "ens_certification_required": {{ "applies": true | false | null, "evidence": "string" }},
      "pmi_certified_profile_required": {{ "applies": true | false | null, "evidence": "string" }},
      "economic_offer_weight_over_70_points": {{ "applies": true | false | null, "evidence": "string" }}
    }}
  }}
}}

For "criteria_flags", set "applies" to true only if the documents clearly indicate that condition; false if clearly not; null if you cannot determine.

Special attention for discard-related screening (a professional will review final decisions):
- Execution place not in Asturias (Principality of Asturias, Spain).
- Total execution period strictly under 2 months (if stated).
- Maintenance or warranty obligation beyond 1 year (if stated).
- The contract asks for a technical assistance / helpdesk-type service (as opposed to pure development/delivery).
- ISO certification is required from bidders.
- ENS (Esquema Nacional de Seguridad) certification is required.
- PMI-certified profile is explicitly required.
- The economic offer / price component has more than 70 points (or 70% of technical-economic score) in the assessment criteria — flag if the weighting table shows this.
"""


def _metadata_block(title: str, party_name: str, tender_link: str) -> str:
    return f"""Metadata:
- Title: {title or "(not provided)"}
- Contracting party: {party_name or "(not provided)"}
- Link: {tender_link or "(not provided)"}
"""


def build_tender_multimodal_user_message(
    *,
    title: str,
    party_name: str,
    tender_link: str,
) -> str:
    """User message when the model sees rasterized PDF pages (OpenAI-style image_url parts)."""
    return f"""Analyze this procurement proposal using the attached document images.

Each image is one page of the legal / administrative tender file (PCAP) rendered as PNG (similar to pdftoppm), in page order. Only PCAP is provided; there is no separate technical file in the images.

{_metadata_block(title, party_name, tender_link)}
{_TENDER_JSON_SCHEMA_AND_RULES}

Base every field only on what you can read in the images. If something is illegible or absent, use null or "" as appropriate.
"""


def build_tender_multimodal_batch_user_message(
    *,
    title: str,
    party_name: str,
    tender_link: str,
    batch_index: int,
    batch_image_count: int,
    global_total_pages: int,
    first_page_1_indexed: int,
    last_page_1_indexed: int,
    missing_field_labels: list[str],
    partial_json_compact: str,
) -> str:
    """Build user text for one batched multimodal request.

    Args:
        title: Tender title.
        party_name: Contracting party.
        tender_link: Detail URL.
        batch_index: 1-based batch number for this tender.
        batch_image_count: Number of images attached to this message.
        global_total_pages: Total rasterized pages for the tender.
        first_page_1_indexed: First page index in this batch (1-based).
        last_page_1_indexed: Last page index in this batch (1-based).
        missing_field_labels: Human-readable list of gaps to fill.
        partial_json_compact: Prior merged JSON (may be truncated).

    Returns:
        User message string (images added separately by the client).
    """
    missing_bullets = "\n".join(f"- {lab}" for lab in missing_field_labels) or "- (none)"
    partial_block = partial_json_compact.strip() or "{}"
    return f"""You are analyzing a procurement proposal from rasterized PDF pages (PNG images attached to this message).

Page order: PCAP (legal / administrative tender file) only, in page order.

This is batch {batch_index} of this tender ({batch_image_count} page image(s) attached). The images are pages {first_page_1_indexed}–{last_page_1_indexed} of {global_total_pages} total rasterized pages.

{_metadata_block(title, party_name, tender_link)}

Fields that are still missing or incomplete — only extract or update information relevant to these from THIS batch of pages:
{missing_bullets}

Prior extractions so far (JSON, may be truncated; do not contradict without evidence from the current pages):
{partial_block}

Respond with a single JSON object containing ONLY keys you can fill or update from this batch. Omit keys you have nothing new for; you may use null inside nested objects where appropriate. Do not repeat the full schema if unchanged — partial updates only. No markdown fences, no commentary outside JSON.

The same schema rules apply as for a full extraction (see structure reference below); nested objects may be partial (e.g. only "outsourcing" or only "discard_review.criteria_flags" keys you resolved).

{_TENDER_JSON_SCHEMA_AND_RULES}
"""


def build_tender_text_gapfill_user_message(
    *,
    pdf_document_text: str,
    title: str,
    party_name: str,
    tender_link: str,
    missing_field_labels: list[str],
    partial_json_compact: str,
) -> str:
    """Build user message for a text-only gap-fill after image batches.

    Args:
        pdf_document_text: Extracted PCAP text (truncated per env).
        title: Tender title.
        party_name: Contracting party.
        tender_link: Detail URL.
        missing_field_labels: Fields still missing after multimodal batches.
        partial_json_compact: Prior merged JSON for consistency.

    Returns:
        Full user message string for a text-only completion.
    """
    max_chars = int(os.environ.get("IMAN_LLM_MAX_PDF_CHARS", "120000"))
    body = pdf_document_text.strip()
    if len(body) > max_chars:
        body = (
            body[:max_chars]
            + "\n\n[TRUNCATED: document exceeded IMAN_LLM_MAX_PDF_CHARS]"
        )
    missing_bullets = "\n".join(f"- {lab}" for lab in missing_field_labels) or "- (none)"
    partial_block = partial_json_compact.strip() or "{}"
    return f"""The following procurement text was already partially analyzed from PCAP page images. Complete ONLY the fields that are still missing, using the PCAP text below.

{_metadata_block(title, party_name, tender_link)}

Still missing (focus on these):
{missing_bullets}

Known partial JSON so far (do not contradict without evidence):
{partial_block}

Return a single JSON object with ONLY keys you can now fill or update. No markdown fences.

{_TENDER_JSON_SCHEMA_AND_RULES}

--- PCAP-derived document text ---

{body}
"""


def build_tender_analysis_user_message(
    *,
    pdf_document_text: str,
    title: str,
    party_name: str,
    tender_link: str,
) -> str:
    """Build the user message with metadata plus PDF-derived plain text (possibly truncated)."""
    max_chars = int(os.environ.get("IMAN_LLM_MAX_PDF_CHARS", "120000"))
    body = pdf_document_text.strip()
    if len(body) > max_chars:
        body = (
            body[:max_chars]
            + "\n\n[TRUNCATED: document exceeded IMAN_LLM_MAX_PDF_CHARS]"
        )

    return f"""Analyze the following procurement proposal using the PCAP (legal tender file) text below (and the metadata for context).

{_metadata_block(title, party_name, tender_link)}
{_TENDER_JSON_SCHEMA_AND_RULES}

--- PCAP-derived document text ---

{body}
"""


def default_enrichment_on_error(message: str) -> Dict[str, Any]:
    """Minimal structure when parsing fails."""
    return {
        "object_of_the_contract": "",
        "scope_of_the_work": "",
        "packages": None,
        "economic_solvency": "",
        "required_profiles": "",
        "assessment_criteria": "",
        "outsourcing": {"exists": None, "percentage": None, "notes": message},
        "discard_review": {
            "summary": message,
            "potential_discard": None,
            "reasons_for_manual_review": [],
            "criteria_flags": {},
        },
        "parse_error": True,
    }
