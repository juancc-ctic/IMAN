"""Shared opt-in live-LLM skips and assertions (used by integration tests)."""

from __future__ import annotations

import os

import pytest

from iman_ingestion.llm.client import chat_model_name, resolved_llm_base_url

_RUN_LLM = os.environ.get("IMAN_RUN_LLM_INTEGRATION", "").lower() in (
    "1",
    "true",
    "yes",
)

skip_without_live_llm = pytest.mark.skipif(
    not _RUN_LLM,
    reason="Set IMAN_RUN_LLM_INTEGRATION=1 to run live LLM tests",
)

_SKIP_ON_LLM_ERROR = os.environ.get(
    "IMAN_LLM_INTEGRATION_SKIP_ON_ERROR", ""
).lower() in ("1", "true", "yes")


def _llm_error_detail(data: dict) -> str:
    dr = data.get("discard_review")
    if isinstance(dr, dict) and (dr.get("summary") or "").strip():
        return str(dr["summary"]).strip()
    out = data.get("outsourcing")
    if isinstance(out, dict) and (out.get("notes") or "").strip():
        return str(out["notes"]).strip()
    return "(no detail in discard_review.summary / outsourcing.notes)"


def _assert_no_llm_parse_error(data: dict) -> None:
    if data.get("parse_error") is not True:
        return
    detail = _llm_error_detail(data)
    base = resolved_llm_base_url()
    model = chat_model_name()
    msg = (
        "analyze_tender_proposal returned parse_error=True (LLM unreachable, "
        "HTTP error, or invalid JSON).\n"
        f"  Detail: {detail}\n"
        f"  Effective chat base URL: {base}\n"
        f"  Effective model id: {model}\n"
        "Override with LLM_BASE_URL / OPENAI_BASE_URL and LLM_MODEL if needed.\n"
        "Or set IMAN_LLM_INTEGRATION_SKIP_ON_ERROR=1 to skip."
    )
    if _SKIP_ON_LLM_ERROR:
        pytest.skip(msg)
    pytest.fail(msg)


def assert_enrichment_shape(data: dict) -> None:
    """Assert response looks like tender enrichment JSON (not error shell)."""
    assert isinstance(data, dict)
    _assert_no_llm_parse_error(data)
    for key in (
        "object_of_the_contract",
        "scope_of_the_work",
        "packages",
        "economic_solvency",
        "required_profiles",
        "assessment_criteria",
        "outsourcing",
        "discard_review",
    ):
        assert key in data, f"missing key {key!r}"
    assert isinstance(data["outsourcing"], dict)
    assert isinstance(data["discard_review"], dict)
    dr = data["discard_review"]
    assert "summary" in dr
    assert isinstance(dr.get("criteria_flags"), dict)
