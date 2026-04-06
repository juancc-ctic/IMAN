"""OpenAI-compatible HTTP clients (separate embedding vs chat endpoints)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Sequence

from openai import OpenAI

from iman_ingestion.llm.tender_analysis import (
    TENDER_ANALYSIS_SYSTEM_PROMPT,
    build_tender_analysis_user_message,
    build_tender_multimodal_batch_user_message,
    build_tender_multimodal_user_message,
    build_tender_text_gapfill_user_message,
    default_enrichment_on_error,
)
from iman_ingestion.llm.tender_fields import (
    all_required_satisfied,
    list_missing_field_labels,
    merge_tender_partial,
    multimodal_images_per_request,
    partial_json_for_prompt,
)

# Defaults aligned with local inference stack (override via env).
_DEFAULT_EMBEDDINGS_API_BASE = "http://192.168.4.32:8116/v1"
_DEFAULT_EMBEDDINGS_MODEL = "Snowflake/snowflake-arctic-embed-l-v2.0"
_DEFAULT_LLM_BASE_URL = "http://192.168.4.32:4000/v1"
_DEFAULT_LLM_MODEL = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"


def _api_key() -> str:
    """Bearer key for OpenAI-compatible servers (never hardcode secrets in code)."""
    return (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )


def get_embeddings_client() -> OpenAI:
    """Client for ``/v1/embeddings`` (``EMBEDDINGS_API_BASE``)."""
    base = (
        os.environ.get("EMBEDDINGS_API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or _DEFAULT_EMBEDDINGS_API_BASE
    )
    return OpenAI(base_url=base.rstrip("/"), api_key=_api_key())


def get_llm_client() -> OpenAI:
    """Client for chat/completions (``LLM_BASE_URL``)."""
    base = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or _DEFAULT_LLM_BASE_URL
    )
    return OpenAI(base_url=base.rstrip("/"), api_key=_api_key())


def get_openai_client() -> OpenAI:
    """Alias for :func:`get_llm_client` (backward compatibility)."""
    return get_llm_client()


def embedding_model_name() -> str:
    return (
        os.environ.get("EMBEDDINGS_MODEL")
        or os.environ.get("IMAN_EMBEDDING_MODEL")
        or _DEFAULT_EMBEDDINGS_MODEL
    )


def chat_model_name() -> str:
    return (
        os.environ.get("LLM_MODEL")
        or os.environ.get("IMAN_CHAT_MODEL")
        or _DEFAULT_LLM_MODEL
    )


def resolved_llm_base_url() -> str:
    """Chat API base URL after applying env fallbacks (no trailing slash)."""
    base = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or _DEFAULT_LLM_BASE_URL
    )
    return base.rstrip("/")


def embed_texts(client: OpenAI, texts: Sequence[str]) -> List[List[float]]:
    """Embed a batch of strings; returns one vector per input line."""
    if not texts:
        return []
    model = embedding_model_name()
    response = client.embeddings.create(model=model, input=list(texts))
    return [item.embedding for item in response.data]


def _parse_llm_json_object(raw: str) -> Dict[str, Any]:
    """Strip optional markdown fences and parse JSON."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text, count=1)
    return json.loads(text)


def _use_multimodal_llm() -> bool:
    """True unless ``IMAN_USE_MULTIMODAL_LLM`` is 0/false/no."""
    return os.environ.get("IMAN_USE_MULTIMODAL_LLM", "true").lower() not in (
        "0",
        "false",
        "no",
    )


def _return_tender_analysis(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Echo final JSON to stdout when ``IMAN_LLM_PRINT_JSON`` is truthy."""
    if os.environ.get("IMAN_LLM_PRINT_JSON", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return payload


def analyze_tender_proposal(
    client: OpenAI,
    *,
    pdf_text: str = "",
    image_base64_pngs: List[str] | None = None,
    title: str,
    party_name: str,
    tender_link: str,
) -> Dict[str, Any]:
    """Analyze one tender: multimodal (PCAP PNG pages) when enabled and images exist.

    Mirrors ``ai-controller-example.js``: ``pdftoppm`` PNGs as
    ``data:image/png;base64,...`` in chat ``content``. Falls back to plain
    PCAP text when multimodal is off or no images were built.

    Args:
        client: OpenAI-compatible client (chat).
        pdf_text: Extracted PCAP legal-file text (fallback / gap-fill path).
        image_base64_pngs: Base64-encoded PCAP page PNGs, one string per page.
        title: Tender title from Atom metadata.
        party_name: Contracting party name.
        tender_link: Detail URL.

    Returns:
        Parsed JSON object; on failure, a minimal structure with ``parse_error``.

    If ``IMAN_LLM_PRINT_JSON`` is ``1``/``true``/``yes``, the returned dict is
    also printed as indented JSON to stdout (visible in Dagster logs or ``pytest -s``).
    """
    images = list(image_base64_pngs or [])
    use_multimodal = _use_multimodal_llm() and bool(images)

    try:
        if use_multimodal:
            per_req = multimodal_images_per_request()
            if len(images) <= per_req:
                text_part = build_tender_multimodal_user_message(
                    title=title,
                    party_name=party_name,
                    tender_link=tender_link,
                )
                user_content: List[Dict[str, Any]] = [
                    {"type": "text", "text": text_part},
                ]
                for b64 in images:
                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    )
                messages = [
                    {"role": "system", "content": TENDER_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ]
            else:
                accumulated: Dict[str, Any] = {}
                start = 0
                batch_index = 0
                while start < len(images):
                    if all_required_satisfied(accumulated):
                        return _return_tender_analysis(accumulated)
                    chunk = images[start : start + per_req]
                    batch_index += 1
                    first_page = start + 1
                    last_page = start + len(chunk)
                    start += len(chunk)
                    missing = list_missing_field_labels(accumulated)
                    if not missing:
                        return _return_tender_analysis(accumulated)
                    text_part = build_tender_multimodal_batch_user_message(
                        title=title,
                        party_name=party_name,
                        tender_link=tender_link,
                        batch_index=batch_index,
                        batch_image_count=len(chunk),
                        global_total_pages=len(images),
                        first_page_1_indexed=first_page,
                        last_page_1_indexed=last_page,
                        missing_field_labels=missing,
                        partial_json_compact=partial_json_for_prompt(
                            accumulated,
                        ),
                    )
                    user_content = [{"type": "text", "text": text_part}]
                    for b64 in chunk:
                        user_content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64}",
                                },
                            },
                        )
                    messages = [
                        {
                            "role": "system",
                            "content": TENDER_ANALYSIS_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": user_content},
                    ]
                    completion = client.chat.completions.create(
                        model=chat_model_name(),
                        messages=messages,
                        temperature=0.15,
                    )
                    raw = completion.choices[0].message.content or "{}"
                    try:
                        partial = _parse_llm_json_object(raw)
                        if not isinstance(partial, dict):
                            partial = {}
                    except json.JSONDecodeError:
                        partial = {}
                    merge_tender_partial(
                        accumulated,
                        partial,
                        merge_mode="batch_overwrites",
                    )
                    if all_required_satisfied(accumulated):
                        return _return_tender_analysis(accumulated)

                if not all_required_satisfied(accumulated) and (
                    pdf_text or ""
                ).strip():
                    gap_missing = list_missing_field_labels(accumulated)
                    if gap_missing:
                        gap_user = build_tender_text_gapfill_user_message(
                            pdf_document_text=pdf_text,
                            title=title,
                            party_name=party_name,
                            tender_link=tender_link,
                            missing_field_labels=gap_missing,
                            partial_json_compact=partial_json_for_prompt(
                                accumulated,
                            ),
                        )
                        messages = [
                            {
                                "role": "system",
                                "content": TENDER_ANALYSIS_SYSTEM_PROMPT,
                            },
                            {"role": "user", "content": gap_user},
                        ]
                        completion = client.chat.completions.create(
                            model=chat_model_name(),
                            messages=messages,
                            temperature=0.15,
                        )
                        raw = completion.choices[0].message.content or "{}"
                        try:
                            partial = _parse_llm_json_object(raw)
                            if not isinstance(partial, dict):
                                partial = {}
                        except json.JSONDecodeError:
                            partial = {}
                        merge_tender_partial(accumulated, partial)
                return _return_tender_analysis(accumulated)
        if not use_multimodal:
            user = build_tender_analysis_user_message(
                pdf_document_text=pdf_text or "(No PDF text could be loaded.)",
                title=title,
                party_name=party_name,
                tender_link=tender_link,
            )
            messages = [
                {"role": "system", "content": TENDER_ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ]

        completion = client.chat.completions.create(
            model=chat_model_name(),
            messages=messages,
            temperature=0.15,
        )
        raw = completion.choices[0].message.content or "{}"
        try:
            return _return_tender_analysis(_parse_llm_json_object(raw))
        except json.JSONDecodeError as exc:
            return _return_tender_analysis(
                default_enrichment_on_error(
                    f"Invalid JSON from model: {exc}: {raw[:1500]}",
                ),
            )
    except Exception as exc:
        return _return_tender_analysis(default_enrichment_on_error(str(exc)))


def enrich_tender_summary(
    client: OpenAI,
    title: str,
    party_name: str,
) -> Dict[str, Any]:
    """Legacy helper: same analysis pipeline without PDFs (metadata only)."""
    return analyze_tender_proposal(
        client,
        pdf_text="",
        title=title,
        party_name=party_name,
        tender_link="",
    )
