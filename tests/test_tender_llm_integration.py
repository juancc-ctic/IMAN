"""Live LLM integration tests for :func:`analyze_tender_proposal`.

These tests call your configured chat API (``LLM_BASE_URL`` / ``LLM_MODEL`` /
``OPENAI_*``). They are **skipped by default** so CI and local runs stay
offline.

Run (requires a reachable server and compatible model):

.. code-block:: bash

   export IMAN_RUN_LLM_INTEGRATION=1
   pytest tests/test_tender_llm_integration.py -v

Optional multimodal (vision) check — one extra HTTP call:

.. code-block:: bash

   export IMAN_RUN_LLM_INTEGRATION=1
   export IMAN_RUN_LLM_MULTIMODAL=1
   pytest tests/test_tender_llm_integration.py -v

Optional batched multimodal (two+ completion calls, merge + early exit path):

.. code-block:: bash

   export IMAN_RUN_LLM_INTEGRATION=1
   export IMAN_RUN_LLM_BATCH=1
   pytest tests/test_tender_llm_integration.py::test_live_llm_batched_multimodal_merge_path -v

If the chat API is temporarily unreachable (VPN, wrong host, server down), you can
**skip** instead of failing:

.. code-block:: bash

   export IMAN_LLM_INTEGRATION_SKIP_ON_ERROR=1
"""

from __future__ import annotations

import os

import pytest

from iman_ingestion.llm.client import analyze_tender_proposal, get_llm_client

from tests.llm_live_helpers import assert_enrichment_shape, skip_without_live_llm

# 1×1 PNG (grey pixel), valid for data:image/png;base64,...
_MINIMAL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_SAMPLE_PCAP_TEXT = """
=== PCAP (legal) (PCAP.pdf) ===
Pliego de cláusulas administrativas particulares

Objeto del contrato: Mantenimiento evolutivo y correctivo de la plataforma
de gestión documental del organismo, incluido soporte técnico.

Alcance: servicios de mantenimiento de software, resolución de incidencias
y pequeñas evoluciones funcionales descritas en el anexo técnico.

Lotes: el contrato se adjudica en un único lote sin división en paquetes.

Solvencia económica: acreditación mediante declaración responsable y
seguro de caución del 5% del presupuesto base de licitación.

Perfil requerido: al menos un técnico senior con 3 años de experiencia en
Java y entornos Linux.

Criterios de adjudicación: oferta económica 55 puntos; memoria técnica
45 puntos. La oferta económica no supera el 70% de la puntuación total.

Subcontratación: permitida hasta un máximo del 30% del importe del
contrato, con notificación previa al órgano de contratación.

Lugar de ejecución: Oviedo, Principado de Asturias, España.
Plazo de ejecución: 6 meses desde la formalización.
"""

pytestmark = pytest.mark.llm_integration


@skip_without_live_llm
def test_live_llm_text_only_analyze_tender_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full text path: chat completion + JSON parse (no images)."""
    monkeypatch.setenv("IMAN_USE_MULTIMODAL_LLM", "false")
    client = get_llm_client()
    result = analyze_tender_proposal(
        client,
        pdf_text=_SAMPLE_PCAP_TEXT,
        image_base64_pngs=None,
        title="Mantenimiento plataforma documental 2026",
        party_name="Ayuntamiento de Ejemplo",
        tender_link="https://contrataciondelestado.es/example",
    )
    assert_enrichment_shape(result)
    assert isinstance(result.get("object_of_the_contract"), str)
    assert len((result.get("object_of_the_contract") or "").strip()) > 0


@skip_without_live_llm
@pytest.mark.skipif(
    os.environ.get("IMAN_RUN_LLM_MULTIMODAL", "").lower()
    not in ("1", "true", "yes"),
    reason="Set IMAN_RUN_LLM_MULTIMODAL=1 to run vision multimodal call",
)
def test_live_llm_multimodal_single_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multimodal path with one PNG (model may return sparse fields)."""
    monkeypatch.setenv("IMAN_USE_MULTIMODAL_LLM", "true")
    monkeypatch.setenv("IMAN_MULTIMODAL_IMAGES_PER_REQUEST", "12")
    client = get_llm_client()
    result = analyze_tender_proposal(
        client,
        pdf_text="",
        image_base64_pngs=[_MINIMAL_PNG_B64],
        title="Smoke test tender",
        party_name="Test body",
        tender_link="https://example.invalid/tender",
    )
    assert_enrichment_shape(result)


@skip_without_live_llm
@pytest.mark.skipif(
    os.environ.get("IMAN_RUN_LLM_BATCH", "").lower() not in ("1", "true", "yes"),
    reason="Set IMAN_RUN_LLM_BATCH=1 (uses several API calls)",
)
def test_live_llm_batched_multimodal_merge_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forces image batching (>12 pages) and merge until satisfied or exhausted."""
    monkeypatch.setenv("IMAN_USE_MULTIMODAL_LLM", "true")
    monkeypatch.setenv("IMAN_MULTIMODAL_IMAGES_PER_REQUEST", "12")
    # 14 minimal pages → two chat completions on the multimodal branch.
    images = [_MINIMAL_PNG_B64] * 14
    client = get_llm_client()
    result = analyze_tender_proposal(
        client,
        pdf_text=_SAMPLE_PCAP_TEXT,
        image_base64_pngs=images,
        title="Batch smoke tender",
        party_name="Test body",
        tender_link="https://example.invalid/tender",
    )
    assert_enrichment_shape(result)
