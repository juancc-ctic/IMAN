"""Unit tests for the triage module (no live LLM, no database)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iman_ingestion.triage.company_profile import CompanyProfile, load_company_profile
from iman_ingestion.triage.scorer import (
    _THRESHOLD_NEUTRAL,
    _THRESHOLD_RECOMMENDED,
    evaluate_tender,
)
from iman_ingestion.triage.triage_prompt import build_triage_user_message


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _profile() -> CompanyProfile:
    return CompanyProfile(
        interest_areas=["Inteligencia artificial", "Ciberseguridad"],
        company_fields=["TIC", "I+D+i"],
        past_tender_categories=["Desarrollo de plataformas digitales"],
    )


def _base_enrichment(*, extra_flags: dict | None = None) -> dict:
    flags = {
        "place_of_execution_not_asturias": {"applies": False, "evidence": ""},
        "execution_period_under_2_months": {"applies": False, "evidence": ""},
        "maintenance_longer_than_1_year": {"applies": False, "evidence": ""},
        "asks_technical_assistance_service": {"applies": False, "evidence": ""},
        "iso_certification_required": {"applies": False, "evidence": ""},
        "ens_certification_required": {"applies": False, "evidence": ""},
        "pmi_certified_profile_required": {"applies": False, "evidence": ""},
        "economic_offer_weight_over_70_points": {"applies": False, "evidence": ""},
    }
    if extra_flags:
        for k, v in extra_flags.items():
            flags[k] = v
    return {
        "object_of_the_contract": "Desarrollo de plataforma de IA",
        "scope_of_the_work": "Implementar un sistema de ML para clasificación de documentos",
        "required_profiles": "Arquitecto de software, especialista en ML",
        "assessment_criteria": "Calidad técnica 60p, Precio 40p",
        "packages": [],
        "economic_solvency": "Volumen de negocio superior a 500.000€",
        "outsourcing": {"exists": False, "percentage": None, "notes": ""},
        "discard_review": {
            "summary": "Licitación de IA sin banderas de descarte.",
            "potential_discard": False,
            "reasons_for_manual_review": [],
            "criteria_flags": flags,
        },
    }


def _make_llm_client(response_dict: dict) -> MagicMock:
    client = MagicMock()
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content=json.dumps(response_dict)))]
    client.chat.completions.create.return_value = completion
    return client


def _good_llm_response(score: int = 8, matches: bool = True) -> dict:
    return {
        "interest_match": {"score": score, "reasoning": "Buena alineación con IA y plataformas."},
        "scope_match": {
            "matches": matches,
            "matching_fields": ["TIC"] if matches else [],
            "reasoning": "El ámbito es TIC.",
        },
        "human_summary": "Licitación de IA alineada con los intereses de la empresa.",
    }


# ---------------------------------------------------------------------------
# company_profile.py tests
# ---------------------------------------------------------------------------


def test_load_company_profile_valid(tmp_path: Path) -> None:
    yaml_file = tmp_path / "profile.yaml"
    yaml_file.write_text(
        "interest_areas:\n  - IA\ncompany_fields:\n  - TIC\npast_tender_categories:\n  - Plataformas\n",
        encoding="utf-8",
    )
    profile = load_company_profile(path=yaml_file)
    assert isinstance(profile.interest_areas, list)
    assert profile.interest_areas == ["IA"]
    assert profile.company_fields == ["TIC"]
    assert profile.past_tender_categories == ["Plataformas"]


def test_load_company_profile_missing() -> None:
    with pytest.raises(FileNotFoundError):
        load_company_profile(path=Path("/does/not/exist/profile.yaml"))


def test_load_company_profile_empty_file(tmp_path: Path) -> None:
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("", encoding="utf-8")
    profile = load_company_profile(path=yaml_file)
    assert profile.interest_areas == []
    assert profile.company_fields == []
    assert profile.past_tender_categories == []


# ---------------------------------------------------------------------------
# triage_prompt.py tests
# ---------------------------------------------------------------------------


def test_build_triage_user_message_contains_key_fields() -> None:
    enrichment = _base_enrichment()
    profile = _profile()
    msg = build_triage_user_message(
        title="Sistema de IA para AAPP",
        party_name="Ayuntamiento de Gijón",
        tender_link="https://example.com/tender/1",
        enrichment=enrichment,
        company_profile=profile,
    )
    assert "Inteligencia artificial" in msg
    assert "TIC" in msg
    assert "Sistema de IA para AAPP" in msg
    assert "Ayuntamiento de Gijón" in msg


def test_build_triage_user_message_caps_long_fields() -> None:
    enrichment = _base_enrichment()
    enrichment["scope_of_the_work"] = "X" * 5000
    profile = _profile()
    msg = build_triage_user_message(
        title="Test",
        party_name="Test",
        tender_link="https://example.com",
        enrichment=enrichment,
        company_profile=profile,
    )
    # 2000-char cap plus some surrounding text — total shouldn't explode
    assert len(msg) < 20_000


# ---------------------------------------------------------------------------
# scorer.py tests
# ---------------------------------------------------------------------------


def test_scorer_no_enrichment_returns_neutral() -> None:
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=None,
        llm_client=MagicMock(),
        company_profile=_profile(),
    )
    assert result["status"] == "neutral"
    assert result["overall_score"] is None
    MagicMock().chat.completions.create.assert_not_called()


def test_scorer_parse_error_enrichment_returns_neutral() -> None:
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment={"parse_error": True},
        llm_client=MagicMock(),
        company_profile=_profile(),
    )
    assert result["status"] == "neutral"


def test_scorer_hard_blocker_place_skips_llm() -> None:
    enrichment = _base_enrichment(
        extra_flags={"place_of_execution_not_asturias": {"applies": True, "evidence": "Madrid"}}
    )
    client = MagicMock()
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=enrichment,
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["status"] == "potential_discard"
    assert result["overall_score"] == 0.0
    assert "place_of_execution_not_asturias" in result["discard_flags_triggered"]
    client.chat.completions.create.assert_not_called()


def test_scorer_hard_blocker_technical_assistance_skips_llm() -> None:
    enrichment = _base_enrichment(
        extra_flags={"asks_technical_assistance_service": {"applies": True, "evidence": "helpdesk"}}
    )
    client = MagicMock()
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=enrichment,
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["status"] == "potential_discard"
    assert result["overall_score"] == 0.0
    client.chat.completions.create.assert_not_called()


def test_scorer_recommended_path() -> None:
    client = _make_llm_client(_good_llm_response(score=9, matches=True))
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["status"] == "recommended"
    assert result["overall_score"] is not None
    assert result["overall_score"] >= _THRESHOLD_RECOMMENDED


def test_scorer_potential_discard_low_score() -> None:
    client = _make_llm_client(_good_llm_response(score=1, matches=False))
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["status"] == "potential_discard"
    assert result["overall_score"] is not None
    assert result["overall_score"] < _THRESHOLD_NEUTRAL


def test_scorer_soft_flags_reduce_score() -> None:
    # 4 soft flags → penalty = 4 * 1.5 = 6.0
    extra = {
        "execution_period_under_2_months": {"applies": True, "evidence": "6 weeks"},
        "maintenance_longer_than_1_year": {"applies": True, "evidence": "2 years"},
        "iso_certification_required": {"applies": True, "evidence": "ISO 9001"},
        "ens_certification_required": {"applies": True, "evidence": "ENS alto"},
    }
    client_no_flags = _make_llm_client(_good_llm_response(score=7, matches=True))
    client_with_flags = _make_llm_client(_good_llm_response(score=7, matches=True))

    result_no_flags = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client_no_flags,
        company_profile=_profile(),
    )
    result_with_flags = evaluate_tender(
        tender_id="t2",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(extra_flags=extra),
        llm_client=client_with_flags,
        company_profile=_profile(),
    )
    assert result_with_flags["overall_score"] < result_no_flags["overall_score"]
    score_diff = result_no_flags["overall_score"] - result_with_flags["overall_score"]
    assert abs(score_diff - 6.0) < 0.01


def test_scorer_invalid_llm_score_clamped() -> None:
    bad_response = {
        "interest_match": {"score": 15, "reasoning": "..."},
        "scope_match": {"matches": True, "matching_fields": ["TIC"], "reasoning": "..."},
        "human_summary": "Test.",
    }
    client = _make_llm_client(bad_response)
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["overall_score"] is not None
    assert 0.0 <= result["overall_score"] <= 10.0


def test_scorer_discard_flags_triggered_list_correct() -> None:
    extra = {
        "execution_period_under_2_months": {"applies": True, "evidence": "5 weeks"},
        "maintenance_longer_than_1_year": {"applies": True, "evidence": "18 months"},
        "iso_certification_required": {"applies": True, "evidence": "ISO 27001"},
        "ens_certification_required": {"applies": False, "evidence": ""},
    }
    client = _make_llm_client(_good_llm_response())
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(extra_flags=extra),
        llm_client=client,
        company_profile=_profile(),
    )
    triggered = result["discard_flags_triggered"]
    assert len(triggered) == 3
    assert "execution_period_under_2_months" in triggered
    assert "maintenance_longer_than_1_year" in triggered
    assert "iso_certification_required" in triggered
    assert "ens_certification_required" not in triggered


def test_scorer_llm_parse_failure_does_not_crash() -> None:
    client = MagicMock()
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content="this is not json {{{"))]
    client.chat.completions.create.return_value = completion

    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    # Should not raise; should return a valid dict
    assert "status" in result
    assert "overall_score" in result


# ---------------------------------------------------------------------------
# Live LLM integration test (opt-in)
# ---------------------------------------------------------------------------

try:
    from tests.llm_live_helpers import skip_without_live_llm
except ImportError:
    import pytest as _pytest

    skip_without_live_llm = _pytest.mark.skip(reason="llm_live_helpers not found")


@skip_without_live_llm
def test_triage_live_llm_integration() -> None:
    from iman_ingestion.llm.client import get_llm_client

    client = get_llm_client()
    profile = _profile()
    enrichment = _base_enrichment()

    result = evaluate_tender(
        tender_id="live-test",
        title="Desarrollo de plataforma de IA para clasificación de expedientes",
        party_name="Gobierno del Principado de Asturias",
        tender_link="https://example.com/tender/live",
        enrichment=enrichment,
        llm_client=client,
        company_profile=profile,
    )

    assert isinstance(result, dict)
    assert result["status"] in ("recommended", "neutral", "potential_discard")
    assert isinstance(result["overall_score"], (int, float))
    assert isinstance(result["interest_match"], dict)
    assert isinstance(result["scope_match"], dict)
    assert isinstance(result["discard_flags_triggered"], list)
    assert isinstance(result["human_summary"], str)
