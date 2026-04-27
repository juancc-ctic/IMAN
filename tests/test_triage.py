"""Unit tests for the triage module (no live LLM, no database)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iman_ingestion.triage.company_profile import CompanyProfile, TriageDimension, load_company_profile
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
        triage_dimensions=[
            TriageDimension(name="Alineación temática", description="Encaje con áreas de interés.", weight=2.0),
            TriageDimension(name="Encaje de ámbito", description="Pertenece a campos de negocio.", weight=1.0),
            TriageDimension(name="Condiciones de ejecución", description="Plazo, lugar, certificaciones.", weight=1.0),
            TriageDimension(name="Criterios de valoración", description="Peso técnico vs precio.", weight=1.0),
        ],
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
        flags.update(extra_flags)
    return {
        "summary": "Plataforma de IA para clasificación de documentos.",
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


def _good_llm_response(scores: list[int] | None = None) -> dict:
    dims = [
        "Alineación temática",
        "Encaje de ámbito",
        "Condiciones de ejecución",
        "Criterios de valoración",
    ]
    if scores is None:
        scores = [5, 5, 4, 4]
    return {
        "dimensions": [
            {"name": name, "score": score, "reasoning": "Razonamiento de prueba."}
            for name, score in zip(dims, scores)
        ],
        "human_summary": "Licitación de IA alineada con los intereses de la empresa.",
    }


# ---------------------------------------------------------------------------
# company_profile.py tests
# ---------------------------------------------------------------------------


def test_load_company_profile_valid(tmp_path: Path) -> None:
    yaml_file = tmp_path / "profile.yaml"
    yaml_file.write_text(
        "interest_areas:\n  - IA\ncompany_fields:\n  - TIC\npast_tender_categories:\n  - Plataformas\n"
        "triage_dimensions:\n  - name: Alineación\n    description: Encaje temático.\n",
        encoding="utf-8",
    )
    profile = load_company_profile(path=yaml_file)
    assert profile.interest_areas == ["IA"]
    assert profile.company_fields == ["TIC"]
    assert profile.past_tender_categories == ["Plataformas"]
    assert len(profile.triage_dimensions) == 1
    assert profile.triage_dimensions[0].name == "Alineación"


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
    assert profile.triage_dimensions == []


# ---------------------------------------------------------------------------
# triage_prompt.py tests
# ---------------------------------------------------------------------------


def test_build_triage_user_message_contains_key_fields() -> None:
    msg = build_triage_user_message(
        title="Sistema de IA para AAPP",
        party_name="Ayuntamiento de Gijón",
        tender_link="https://example.com/tender/1",
        enrichment=_base_enrichment(),
        company_profile=_profile(),
    )
    assert "Inteligencia artificial" in msg
    assert "TIC" in msg
    assert "Sistema de IA para AAPP" in msg
    assert "Ayuntamiento de Gijón" in msg
    assert "Alineación temática" in msg
    assert "Encaje de ámbito" in msg


def test_build_triage_user_message_contains_dimensions() -> None:
    profile = _profile()
    msg = build_triage_user_message(
        title="Test",
        party_name="Test",
        tender_link="https://example.com",
        enrichment=_base_enrichment(),
        company_profile=profile,
    )
    for dim in profile.triage_dimensions:
        assert dim.name in msg


def test_build_triage_user_message_caps_long_fields() -> None:
    enrichment = _base_enrichment()
    enrichment["scope_of_the_work"] = "X" * 5000
    msg = build_triage_user_message(
        title="Test",
        party_name="Test",
        tender_link="https://example.com",
        enrichment=enrichment,
        company_profile=_profile(),
    )
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
    assert result["dimensions"] == []


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
    assert result["overall_score"] is None


def test_scorer_recommended_high_scores() -> None:
    client = _make_llm_client(_good_llm_response(scores=[5, 5, 5, 5]))
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
    assert result["overall_score"] == 5.0
    assert len(result["dimensions"]) == 4


def test_scorer_potential_discard_low_scores() -> None:
    # Weighted avg: (1*2 + 1*1 + 1*1 + 1*1) / 5 = 1.0 < _THRESHOLD_NEUTRAL
    client = _make_llm_client(_good_llm_response(scores=[1, 1, 1, 1]))
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


def test_scorer_neutral_mid_scores() -> None:
    # Weighted avg: (2*2 + 3*1 + 2*1 + 3*1) / 5 = 12/5 = 2.4
    client = _make_llm_client(_good_llm_response(scores=[2, 3, 2, 3]))
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["status"] == "neutral"
    assert _THRESHOLD_NEUTRAL <= result["overall_score"] < _THRESHOLD_RECOMMENDED


def test_scorer_overall_score_is_weighted_average() -> None:
    # weights: Alineación=2, rest=1 → total weight=5
    # scores [4, 2, 3, 5] → (4*2 + 2*1 + 3*1 + 5*1) / 5 = 18/5 = 3.6
    scores = [4, 2, 3, 5]
    client = _make_llm_client(_good_llm_response(scores=scores))
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["overall_score"] == 3.6


def test_scorer_zero_score_discards_automatically() -> None:
    client = _make_llm_client(_good_llm_response(scores=[0, 4, 4, 4]))
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
    assert result["overall_score"] == 0.0


def test_scorer_zero_score_on_any_dimension_discards() -> None:
    client = _make_llm_client(_good_llm_response(scores=[5, 5, 0, 5]))
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
    assert result["overall_score"] == 0.0


def test_scorer_score_clamped_to_0_5() -> None:
    response = _good_llm_response(scores=[10, -3, 7, 2])
    client = _make_llm_client(response)
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    for dim in result["dimensions"]:
        assert 0 <= dim["score"] <= 5


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
    assert "status" in result
    assert "overall_score" in result
    assert "dimensions" in result


def test_scorer_empty_dimensions_from_llm_gives_neutral() -> None:
    client = _make_llm_client({"dimensions": [], "human_summary": "Sin dimensiones."})
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["status"] == "neutral"
    assert result["overall_score"] is None


def test_scorer_missing_dimensions_key_gives_neutral() -> None:
    client = _make_llm_client({"human_summary": "Sin clave dimensions."})
    result = evaluate_tender(
        tender_id="t1",
        title="Test",
        party_name="Test",
        tender_link="",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert result["status"] == "neutral"
    assert result["overall_score"] is None


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
    result = evaluate_tender(
        tender_id="live-test",
        title="Desarrollo de plataforma de IA para clasificación de expedientes",
        party_name="Gobierno del Principado de Asturias",
        tender_link="https://example.com/tender/live",
        enrichment=_base_enrichment(),
        llm_client=client,
        company_profile=_profile(),
    )
    assert isinstance(result, dict)
    assert result["status"] in ("recommended", "neutral", "potential_discard")
    assert isinstance(result["dimensions"], list)
    assert len(result["dimensions"]) > 0
    for dim in result["dimensions"]:
        assert "name" in dim
        assert "score" in dim
        assert 0 <= dim["score"] <= 5
        assert "reasoning" in dim
    assert isinstance(result["human_summary"], str)
