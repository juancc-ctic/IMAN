"""Tender and EU item triage scorer: evaluates content against the company profile."""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import Any

import numpy as np
from openai import OpenAI

from iman_ingestion.llm.client import parse_llm_json_object, chat_model_name
from iman_ingestion.triage.company_profile import CompanyProfile
from iman_ingestion.triage.triage_prompt import (
    TRIAGE_SYSTEM_PROMPT,
    EU_TRIAGE_SYSTEM_PROMPT,
    build_triage_user_message,
    build_eu_triage_user_message,
)

logger = logging.getLogger(__name__)

_TRIAGE_TEMPERATURE = 0.1


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def _cosine_to_score(similarity: float) -> int:
    return max(0, min(5, round(similarity * 5)))


def _coerce_score(raw: Any, fallback: int = 3) -> int:
    try:
        val = int(float(raw))
        return max(0, min(5, val))
    except (TypeError, ValueError):
        return fallback


def _weighted_score(dimensions: list[dict], weight_map: dict[str, float]) -> float | None:
    items = [
        (d["score"], weight_map.get(d["name"], 1.0))
        for d in dimensions
        if isinstance(d.get("score"), (int, float))
    ]
    if not items:
        return None
    total_weight = sum(w for _, w in items)
    if total_weight == 0:
        return None
    return round(sum(s * w for s, w in items) / total_weight, 2)


def _prepare_cosine_dim(
    company_profile: CompanyProfile,
    item_embedding: list[float] | None,
    profile_embedding: list[float] | None,
) -> tuple[dict[str, Any] | None, CompanyProfile]:
    """Return (cosine_dim_result, profile_without_first_dim) when embeddings are available.

    Falls back to (None, original_profile) so the LLM handles all dimensions as usual.
    """
    dims = company_profile.triage_dimensions
    if not (item_embedding and profile_embedding and dims):
        return None, company_profile

    first_dim = dims[0]
    sim = _cosine_similarity(item_embedding, profile_embedding)
    score = _cosine_to_score(sim)
    cosine_dim_result: dict[str, Any] = {
        "name": first_dim.name,
        "score": score,
        "reasoning": f"Cosine similarity with action plan: {sim:.3f}",
    }
    profile_for_llm = replace(company_profile, triage_dimensions=list(dims[1:]))
    return cosine_dim_result, profile_for_llm


def _run_triage_llm_call(
    item_id: str,
    system_prompt: str,
    user_msg: str,
    llm_client: OpenAI,
    company_profile: CompanyProfile,
    cosine_dim_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the LLM, parse the JSON response, coerce scores, and return the result dict."""
    raw_content = ""
    try:
        response = llm_client.chat.completions.create(
            model=chat_model_name(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=_TRIAGE_TEMPERATURE,
        )
        raw_content = response.choices[0].message.content or ""
        logger.debug("Triage LLM raw for %r: %s", item_id, raw_content[:500])
        result = parse_llm_json_object(raw_content)
    except Exception as exc:
        logger.warning("Triage LLM parse failed for %r: %s | raw=%r", item_id, exc, raw_content[:300])
        result = {}

    if not result.get("dimensions"):
        logger.warning("Triage LLM returned no dimensions for %r | raw=%r", item_id, raw_content[:500])

    raw_dims: list = result.get("dimensions") or []
    dimensions: list[dict] = []
    for item in raw_dims:
        if not isinstance(item, dict):
            continue
        dimensions.append({
            "name": str(item.get("name") or "").strip(),
            "score": _coerce_score(item.get("score")),
            "reasoning": str(item.get("reasoning") or "").strip(),
        })

    if cosine_dim_result is not None:
        dimensions = [cosine_dim_result] + dimensions

    if any(d["score"] <= 1 for d in dimensions):
        return {
            "overall_score": 0.0,
            "dimensions": dimensions,
            "human_summary": str(result.get("human_summary") or "").strip(),
        }

    weight_map = {d.name: d.weight for d in company_profile.triage_dimensions}
    overall = _weighted_score(dimensions, weight_map)

    return {
        "overall_score": overall,
        "dimensions": dimensions,
        "human_summary": str(result.get("human_summary") or "").strip(),
    }


def evaluate_tender(
    tender_id: str,
    title: str,
    party_name: str,
    tender_link: str,
    enrichment: dict[str, Any] | None,
    llm_client: OpenAI,
    company_profile: CompanyProfile,
    tender_embedding: list[float] | None = None,
    profile_embedding: list[float] | None = None,
) -> dict[str, Any]:
    """Evaluate a single tender and return a triage result dict.

    If *tender_embedding* and *profile_embedding* are supplied, the first
    triage dimension is scored via cosine similarity instead of the LLM.

    This function has no Dagster dependencies and is fully unit-testable.
    """
    if not enrichment or enrichment.get("parse_error"):
        return {
            "overall_score": None,
            "dimensions": [],
            "human_summary": "Licitación sin enriquecimiento LLM; requiere revisión manual.",
        }

    cosine_dim_result, profile_for_prompt = _prepare_cosine_dim(
        company_profile, tender_embedding, profile_embedding
    )
    user_msg = build_triage_user_message(
        title=title,
        party_name=party_name,
        tender_link=tender_link,
        enrichment=enrichment,
        company_profile=profile_for_prompt,
    )
    return _run_triage_llm_call(
        tender_id, TRIAGE_SYSTEM_PROMPT, user_msg, llm_client, company_profile,
        cosine_dim_result=cosine_dim_result,
    )


def evaluate_eu_item(
    reference: str,
    title: str,
    kind: str,
    url: str | None,
    deadline_date: str | None,
    embed_text: str | None,
    llm_client: OpenAI,
    company_profile: CompanyProfile,
    item_embedding: list[float] | None = None,
    profile_embedding: list[float] | None = None,
) -> dict[str, Any]:
    """Evaluate a single EU item and return a triage result dict.

    If *item_embedding* and *profile_embedding* are supplied, the first
    triage dimension is scored via cosine similarity instead of the LLM.

    This function has no Dagster dependencies and is fully unit-testable.
    """
    if not embed_text:
        return {
            "overall_score": None,
            "dimensions": [],
            "human_summary": "EU item has no embed_text; requires manual review.",
        }

    cosine_dim_result, profile_for_prompt = _prepare_cosine_dim(
        company_profile, item_embedding, profile_embedding
    )
    user_msg = build_eu_triage_user_message(
        reference=reference,
        kind=kind,
        title=title,
        url=url,
        deadline_date=deadline_date,
        embed_text=embed_text,
        company_profile=profile_for_prompt,
    )
    return _run_triage_llm_call(
        reference, EU_TRIAGE_SYSTEM_PROMPT, user_msg, llm_client, company_profile,
        cosine_dim_result=cosine_dim_result,
    )
