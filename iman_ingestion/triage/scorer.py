"""Tender triage scorer: evaluates enrichment against the company profile."""

from __future__ import annotations

import logging
from typing import Any

from openai import OpenAI

from iman_ingestion.llm.client import _parse_llm_json_object, chat_model_name
from iman_ingestion.triage.company_profile import CompanyProfile
from iman_ingestion.triage.triage_prompt import TRIAGE_SYSTEM_PROMPT, build_triage_user_message

logger = logging.getLogger(__name__)

_HARD_BLOCKERS = frozenset(
    [
        "place_of_execution_not_asturias",
        "asks_technical_assistance_service",
    ]
)
_SOFT_FLAG_PENALTY = 1.5
_BASELINE = 1.5
_INTEREST_WEIGHT = 0.55
_SCOPE_WEIGHT = 0.30
_THRESHOLD_RECOMMENDED = 6.5
_THRESHOLD_NEUTRAL = 5.0
_TRIAGE_TEMPERATURE = 0.1


def _coerce_score(raw: Any, fallback: int = 5) -> int:
    try:
        val = int(float(raw))
        return max(0, min(10, val))
    except (TypeError, ValueError):
        return fallback


def _extract_flags(enrichment: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (hard_blockers_triggered, soft_flags_triggered)."""
    flags: dict = (enrichment.get("discard_review") or {}).get("criteria_flags") or {}
    hard: list[str] = []
    soft: list[str] = []
    for name, val in flags.items():
        if isinstance(val, dict) and val.get("applies") is True:
            if name in _HARD_BLOCKERS:
                hard.append(name)
            else:
                soft.append(name)
    return hard, soft


def _scope_score(scope_matches: bool, scope_fields: list[str]) -> float:
    """Graduated scope score: -3 (no match) → 10 (strong match, 5+ fields)."""
    n = len(scope_fields)
    if n == 0:
        return -3.0 if not scope_matches else 3.0
    if n <= 2:
        return 4.0
    if n <= 4:
        return 7.0
    return 10.0


def _compute_score(interest: int, scope_matches: bool, scope_fields: list[str], soft_flags: list[str]) -> float:
    raw = interest * _INTEREST_WEIGHT + _scope_score(scope_matches, scope_fields) * _SCOPE_WEIGHT + _BASELINE - len(soft_flags) * _SOFT_FLAG_PENALTY
    return round(max(0.0, min(10.0, raw)), 2)


def _status_from_score(score: float) -> str:
    if score >= _THRESHOLD_RECOMMENDED:
        return "recommended"
    if score >= _THRESHOLD_NEUTRAL:
        return "neutral"
    return "potential_discard"


def evaluate_tender(
    tender_id: str,
    title: str,
    party_name: str,
    tender_link: str,
    enrichment: dict[str, Any] | None,
    llm_client: OpenAI,
    company_profile: CompanyProfile,
) -> dict[str, Any]:
    """Evaluate a single tender and return a triage result dict.

    This function has no Dagster dependencies and is fully unit-testable.
    """
    if not enrichment or enrichment.get("parse_error"):
        return {
            "status": "neutral",
            "overall_score": None,
            "interest_match": {"score": None, "reasoning": "Sin análisis de enriquecimiento disponible."},
            "scope_match": {"matches": None, "matching_fields": [], "reasoning": ""},
            "discard_flags_triggered": [],
            "human_summary": "Licitación sin enriquecimiento LLM; requiere revisión manual.",
        }

    hard_blockers, soft_flags = _extract_flags(enrichment)

    if hard_blockers:
        blocker_str = ", ".join(hard_blockers)
        return {
            "status": "potential_discard",
            "overall_score": 0.0,
            "interest_match": {
                "score": None,
                "reasoning": f"Descarte automático por bloqueo duro: {blocker_str}.",
            },
            "scope_match": {"matches": None, "matching_fields": [], "reasoning": "No evaluado (bloqueo duro)."},
            "discard_flags_triggered": hard_blockers,
            "human_summary": f"Descartada automáticamente por: {blocker_str}.",
        }

    user_msg = build_triage_user_message(
        title=title,
        party_name=party_name,
        tender_link=tender_link,
        enrichment=enrichment,
        company_profile=company_profile,
    )

    raw_content = ""
    try:
        response = llm_client.chat.completions.create(
            model=chat_model_name(),
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=_TRIAGE_TEMPERATURE,
        )
        raw_content = response.choices[0].message.content or ""
        result = _parse_llm_json_object(raw_content)
    except Exception as exc:
        logger.warning("Triage LLM parse failed for %r: %s | raw=%r", tender_id, exc, raw_content[:300])
        result = {}

    interest_data: dict = result.get("interest_match") or {}
    scope_data: dict = result.get("scope_match") or {}

    scope_matches = bool(scope_data.get("matches"))
    scope_fields: list[str] = list(scope_data.get("matching_fields") or [])

    if not scope_matches and not scope_fields:
        scope_reasoning = (scope_data.get("reasoning") or "").strip()
        return {
            "status": "potential_discard",
            "overall_score": 0.0,
            "interest_match": {
                "score": _coerce_score(interest_data.get("score"), fallback=5),
                "reasoning": (interest_data.get("reasoning") or "").strip(),
            },
            "scope_match": {"matches": False, "matching_fields": [], "reasoning": scope_reasoning},
            "discard_flags_triggered": soft_flags,
            "human_summary": (result.get("human_summary") or "Sin alineación con el ámbito de la empresa.").strip(),
        }

    interest_score = _coerce_score(interest_data.get("score"), fallback=5)
    overall_score = _compute_score(interest_score, scope_matches, scope_fields, soft_flags)
    status = _status_from_score(overall_score)

    return {
        "status": status,
        "overall_score": overall_score,
        "interest_match": {
            "score": interest_score,
            "reasoning": (interest_data.get("reasoning") or "").strip(),
        },
        "scope_match": {
            "matches": scope_matches,
            "matching_fields": scope_fields,
            "reasoning": (scope_data.get("reasoning") or "").strip(),
        },
        "discard_flags_triggered": soft_flags,
        "human_summary": (result.get("human_summary") or "").strip(),
    }
