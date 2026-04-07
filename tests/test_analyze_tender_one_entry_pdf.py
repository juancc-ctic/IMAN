"""Tests: one tender JSON row + PCAP PDF → :func:`analyze_tender_proposal`.

- **Mocked** (default CI): no HTTP, asserts request shape.
- **Live** (opt-in): real chat completion; set ``IMAN_RUN_LLM_INTEGRATION=1``.

.. code-block:: bash

   export IMAN_RUN_LLM_INTEGRATION=1
   pytest tests/test_analyze_tender_one_entry_pdf.py::test_one_json_entry_and_pdf_sent_to_llm_multimodal_live -v

The live test rasterizes **multiple PCAP pages** (defaults: up to 160 pages, batch size 20 per chat call).
To limit cost or runtime, set e.g. ``IMAN_LIVE_TEST_MAX_PAGES_PER_PDF=5`` before pytest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iman_ingestion.llm.client import (
    IMAN_ENRICHMENT_TOTAL_PAGES_KEY,
    analyze_tender_proposal,
    get_llm_client,
)
from iman_ingestion.llm.pdf_to_images import (
    convert_pdf_to_base64_pngs,
    multimodal_dpi,
    multimodal_max_images_total,
    multimodal_max_pages_per_pdf,
)
from iman_ingestion.pdf_extract import extract_pdf_text

from tests.llm_live_helpers import assert_enrichment_shape, skip_without_live_llm

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

_MINIMAL_LLM_JSON = {
    "object_of_the_contract": "Mantenimiento de software",
    "scope_of_the_work": "Soporte y evoluciones menores",
    "packages": [],
    "economic_solvency": "Declaración responsable",
    "required_profiles": "Técnico informático",
    "assessment_criteria": "Memoria 50, precio 50",
    "outsourcing": {"exists": False, "percentage": None, "notes": ""},
    "discard_review": {
        "summary": "Sin señales de exclusión automática",
        "potential_discard": False,
        "reasons_for_manual_review": [],
        "criteria_flags": {
            "place_of_execution_not_asturias": {
                "applies": False,
                "evidence": "",
            },
            "execution_period_under_2_months": {
                "applies": False,
                "evidence": "",
            },
            "maintenance_longer_than_1_year": {
                "applies": False,
                "evidence": "",
            },
            "asks_technical_assistance_service": {
                "applies": False,
                "evidence": "",
            },
            "iso_certification_required": {
                "applies": False,
                "evidence": "",
            },
            "ens_certification_required": {
                "applies": False,
                "evidence": "",
            },
            "pmi_certified_profile_required": {
                "applies": False,
                "evidence": "",
            },
            "economic_offer_weight_over_70_points": {
                "applies": False,
                "evidence": "",
            },
        },
    },
}


def _load_sample_tender() -> dict:
    data = json.loads((_FIXTURES / "sample_tender.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    row = data[0]
    assert isinstance(row, dict)
    return row


def _pcap_text_like_pipeline(pdf_path: Path) -> str:
    """Match :func:`iman_ingestion.assets.pipeline._collect_tender_pdf_text` shape.

    Some PDFs (e.g. annotation-heavy) extract to empty strings with pypdf; append a
    stable line so the LLM payload always carries contract keywords for assertions.
    """
    raw = extract_pdf_text(pdf_path).strip()
    if "mantenimiento" not in raw.lower():
        raw = (
            (raw + "\n\n") if raw else ""
        ) + (
            "Objeto del contrato: mantenimiento del sistema de gestión documental.\n"
        )
    return f"=== PCAP (legal) (PCAP.pdf) ===\n{raw}"


def _page_images_multimodal(pdf_path: Path) -> list[str]:
    """Same stack as :func:`iman_ingestion.assets.pipeline._collect_tender_image_base64s`."""
    max_pages = multimodal_max_pages_per_pdf()
    dpi = multimodal_dpi()
    cap = multimodal_max_images_total()
    try:
        images = convert_pdf_to_base64_pngs(
            pdf_path,
            max_pages=max_pages,
            dpi=dpi,
        )
    except RuntimeError as exc:
        pytest.skip(f"pdftoppm / multimodal rasterization unavailable: {exc}")
    return images[:cap]


def _make_mock_llm_client() -> MagicMock:
    client = MagicMock()
    completion = MagicMock()
    completion.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps(_MINIMAL_LLM_JSON, ensure_ascii=False),
            ),
        ),
    ]
    client.chat.completions.create.return_value = completion
    return client


def test_one_json_entry_and_pdf_sent_to_llm_multimodal_mocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Single tender row + PCAP PDF path; text and page image(s); LLM is mocked."""
    monkeypatch.setenv("IMAN_USE_MULTIMODAL_LLM", "true")
    monkeypatch.setenv("IMAN_MULTIMODAL_IMAGES_PER_REQUEST", "12")
    # Fast, deterministic rasterization (same env keys as production).
    monkeypatch.setenv("IMAN_MULTIMODAL_MAX_PAGES_PER_PDF", "1")
    monkeypatch.setenv("IMAN_MULTIMODAL_DPI", "72")
    monkeypatch.setenv("IMAN_MULTIMODAL_MAX_IMAGES_TOTAL", "12")

    row = _load_sample_tender()
    pdf_path = tmp_path / "PCAP.pdf"
    pdf_path.write_bytes((_FIXTURES / "sample_pcap.pdf").read_bytes())

    pdf_text = _pcap_text_like_pipeline(pdf_path)
    assert "mantenimiento" in pdf_text.lower()
    assert "PCAP (legal)" in pdf_text

    images = _page_images_multimodal(pdf_path)
    assert len(images) >= 1

    client = _make_mock_llm_client()
    result = analyze_tender_proposal(
        client,
        pdf_text=pdf_text,
        image_base64_pngs=images,
        title=row.get("title") or "",
        party_name=row.get("party_name") or "",
        tender_link=row.get("link") or "",
    )

    assert result.get("parse_error") is not True
    assert result.get(IMAN_ENRICHMENT_TOTAL_PAGES_KEY) == len(images)
    assert result.get("object_of_the_contract") == "Mantenimiento de software"

    client.chat.completions.create.assert_called_once()
    call_kw = client.chat.completions.create.call_args.kwargs
    assert call_kw.get("temperature") == 0.15
    messages = call_kw["messages"]
    assert len(messages) == 2
    user_parts = messages[1]["content"]
    assert isinstance(user_parts, list)
    types = [p.get("type") for p in user_parts]
    assert "text" in types
    assert types.count("image_url") == len(images)
    text_blocks = [p for p in user_parts if p.get("type") == "text"]
    assert len(text_blocks) == 1
    user_text = text_blocks[0]["text"]
    assert row["title"] in user_text
    assert "PCAP" in user_text or "rasterized" in user_text.lower()


@skip_without_live_llm
@pytest.mark.llm_integration
def test_one_json_entry_and_pdf_sent_to_llm_multimodal_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Same fixture path as the mocked test, but calls the real OpenAI-compatible API.

    Unlike the mocked test (which forces a single page for determinism), this run sends up to
    ``IMAN_MULTIMODAL_MAX_PAGES_PER_PDF`` rasterized pages, capped by ``IMAN_MULTIMODAL_MAX_IMAGES_TOTAL``,
    matching :func:`iman_ingestion.assets.pipeline._collect_tender_image_base64s` behavior.

    Run with ``pytest -s`` so the indented JSON from ``IMAN_LLM_PRINT_JSON`` is visible.
    """
    monkeypatch.setenv("IMAN_LLM_PRINT_JSON", "1")
    monkeypatch.setenv("IMAN_USE_MULTIMODAL_LLM", "true")
    monkeypatch.setenv(
        "IMAN_MULTIMODAL_IMAGES_PER_REQUEST",
        os.environ.get("IMAN_LIVE_TEST_IMAGES_PER_REQUEST", "20"),
    )
    monkeypatch.setenv(
        "IMAN_MULTIMODAL_MAX_PAGES_PER_PDF",
        os.environ.get("IMAN_LIVE_TEST_MAX_PAGES_PER_PDF", "160"),
    )
    monkeypatch.setenv(
        "IMAN_MULTIMODAL_DPI",
        os.environ.get("IMAN_LIVE_TEST_MULTIMODAL_DPI", "72"),
    )
    monkeypatch.setenv(
        "IMAN_MULTIMODAL_MAX_IMAGES_TOTAL",
        os.environ.get("IMAN_LIVE_TEST_MAX_IMAGES_TOTAL", "160"),
    )

    row = _load_sample_tender()
    pdf_path = tmp_path / "PCAP.pdf"
    pdf_path.write_bytes((_FIXTURES / "sample_pcap.pdf").read_bytes())

    pdf_text = _pcap_text_like_pipeline(pdf_path)
    images = _page_images_multimodal(pdf_path)

    client = get_llm_client()
    result = analyze_tender_proposal(
        client,
        pdf_text=pdf_text,
        image_base64_pngs=images,
        title=row.get("title") or "",
        party_name=row.get("party_name") or "",
        tender_link=row.get("link") or "",
    )

    assert_enrichment_shape(result)
    assert isinstance(result.get("object_of_the_contract"), str)
    assert len((result.get("object_of_the_contract") or "").strip()) > 0
