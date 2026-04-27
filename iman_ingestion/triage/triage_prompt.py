"""LLM prompt builders for tender and EU item triage evaluation."""

from __future__ import annotations

import json
from typing import Any

from iman_ingestion.triage.company_profile import CompanyProfile

_MAX_FIELD_CHARS = 2000


def _cap(text: str | None) -> str:
    s = (text or "").strip()
    return s[:_MAX_FIELD_CHARS] if len(s) > _MAX_FIELD_CHARS else s


TRIAGE_SYSTEM_PROMPT = """\
Eres un asistente de triaje de licitaciones públicas desplegado en un centro tecnológico español. \
Tu misión es evaluar si una licitación pública encaja con los intereses estratégicos de la empresa, \
para que los expertos humanos puedan decidir con rapidez si presentar oferta.

Recibirás:
1. El perfil de la empresa: áreas de interés, campos de negocio y categorías de licitaciones anteriores.
2. Las dimensiones de evaluación definidas por la empresa, cada una con nombre y descripción.
3. El análisis estructurado de la licitación ya extraído por otro asistente.

Para cada dimensión debes asignar una puntuación entera de 0 a 5 y un razonamiento breve:
  0 — completamente inadecuado o descarte directo
  1 — muy débil alineación
  2 — alineación parcial o con reservas importantes
  3 — alineación razonable
  4 — buena alineación
  5 — encaje excelente

Responde SIEMPRE con un único objeto JSON válido. Sin bloques de código markdown, sin comentarios \
antes o después del JSON. Los campos de razonamiento deben estar en español.\
"""


def build_triage_user_message(
    *,
    title: str,
    party_name: str,
    tender_link: str,
    enrichment: dict[str, Any],
    company_profile: CompanyProfile,
) -> str:
    """Compose the user-turn message for the triage LLM call."""

    interest_bullets = "\n".join(f"  - {a}" for a in company_profile.interest_areas)
    field_bullets = "\n".join(f"  - {f}" for f in company_profile.company_fields)
    category_bullets = "\n".join(f"  - {c}" for c in company_profile.past_tender_categories)

    packages_raw = enrichment.get("packages")
    packages_str = ""
    if packages_raw:
        try:
            packages_str = json.dumps(packages_raw, ensure_ascii=False)[:_MAX_FIELD_CHARS]
        except Exception:
            packages_str = str(packages_raw)[:_MAX_FIELD_CHARS]

    discard_summary = _cap((enrichment.get("discard_review") or {}).get("summary"))

    # Build the criteria_flags block so the LLM has raw evidence for each dimension
    flags: dict[str, Any] = (enrichment.get("discard_review") or {}).get("criteria_flags") or {}
    flags_lines = []
    for flag_name, val in flags.items():
        if isinstance(val, dict):
            applies = val.get("applies")
            evidence = (val.get("evidence") or "").strip()
            flags_lines.append(f"  - {flag_name}: applies={applies}; {evidence}")
    flags_block = "\n".join(flags_lines) if flags_lines else "  (no flags extracted)"

    # Build dimension instructions block
    dims = company_profile.triage_dimensions
    if dims:
        dim_schema_lines = []
        for d in dims:
            dim_schema_lines.append(
                f'    {{"name": "{d.name}", "score": <0–5>, "reasoning": "<1 frase concisa>"}}'
            )
        dim_schema = ",\n".join(dim_schema_lines)
        dim_descriptions = "\n".join(
            f"  - **{d.name}**: {d.description}" for d in dims
        )
    else:
        dim_schema = '    {"name": "<dimensión>", "score": <0–5>, "reasoning": "<1–3 frases>"}'
        dim_descriptions = "  (no dimensions configured)"

    return f"""\
## Perfil de empresa

Áreas de interés:
{interest_bullets}

Campos de negocio (Ámbito):
{field_bullets}

Categorías de licitaciones anteriores:
{category_bullets}

## Dimensiones de evaluación

{dim_descriptions}

## Análisis de la licitación

Título: {title}
Entidad contratante: {party_name}
Enlace: {tender_link}
Resumen: {_cap(enrichment.get("summary"))}

Objeto del contrato: {_cap(enrichment.get("object_of_the_contract"))}
Alcance: {_cap(enrichment.get("scope_of_the_work"))}
Perfiles requeridos: {_cap(enrichment.get("required_profiles"))}
Criterios de valoración: {_cap(enrichment.get("assessment_criteria"))}
Lotes/paquetes: {packages_str or "no indicados"}
Análisis de descarte previo: {discard_summary or "no disponible"}
Flags de descarte (de la extracción inicial):
{flags_block}

## Instrucciones

Evalúa cada dimensión definida arriba y devuelve un único objeto JSON con esta estructura exacta:

{{
  "dimensions": [
{dim_schema}
  ],
  "human_summary": "<1–2 frases para el revisor humano que capturan el aspecto más relevante de esta licitación para la empresa>"
}}

Reglas:
- Devuelve exactamente una entrada por cada dimensión definida, en el mismo orden.
- El campo "name" debe coincidir exactamente con el nombre de la dimensión.
- "score" es un entero de 0 a 5 según la escala indicada en el sistema.
- "reasoning": máximo una frase corta y directa; solo el motivo principal del score, sin explicaciones adicionales.
- "human_summary": orientado al revisor; máximo una frase; menciona el aspecto más relevante para la decisión de concurrir.\
"""


# ---------------------------------------------------------------------------
# EU funding opportunity prompts
# ---------------------------------------------------------------------------

_EU_MAX_EMBED_CHARS = 4000

EU_TRIAGE_SYSTEM_PROMPT = """\
You are an EU funding opportunity evaluator deployed at a Spanish technology centre. \
Your mission is to assess whether a Horizon Europe (or other EU programme) topic or call \
aligns with the organisation's strategic interests, so that human experts can quickly decide \
whether to invest in preparing a proposal.

You will receive:
1. The company profile: interest areas, business fields, and past project categories.
2. The evaluation dimensions defined by the company, each with a name and description.
3. The EU funding opportunity content (title, kind, description and eligibility conditions).

For each dimension assign an integer score from 0 to 5 and a brief reasoning:
  0 — completely misaligned or direct discard
  1 — very weak alignment
  2 — partial alignment or with significant reservations
  3 — reasonable alignment
  4 — good alignment
  5 — excellent fit

Respond ALWAYS with a single valid JSON object. No markdown code fences, no text before or after the JSON. \
Reasoning fields should be in English.\
"""


def build_eu_triage_user_message(
    *,
    reference: str,
    kind: str,
    title: str | None,
    url: str | None,
    deadline_date: str | None,
    embed_text: str | None,
    company_profile: CompanyProfile,
) -> str:
    """Compose the user-turn message for the EU item triage LLM call."""
    interest_bullets = "\n".join(f"  - {a}" for a in company_profile.interest_areas)
    field_bullets = "\n".join(f"  - {f}" for f in company_profile.company_fields)
    category_bullets = "\n".join(f"  - {c}" for c in company_profile.past_tender_categories)

    dims = company_profile.triage_dimensions
    if dims:
        dim_schema_lines = [
            f'    {{"name": "{d.name}", "score": <0–5>, "reasoning": "<1 concise sentence>"}}'
            for d in dims
        ]
        dim_schema = ",\n".join(dim_schema_lines)
        dim_descriptions = "\n".join(f"  - **{d.name}**: {d.description}" for d in dims)
    else:
        dim_schema = '    {"name": "<dimension>", "score": <0–5>, "reasoning": "<1–3 sentences>"}'
        dim_descriptions = "  (no dimensions configured)"

    content = (embed_text or "").strip()
    if len(content) > _EU_MAX_EMBED_CHARS:
        content = content[:_EU_MAX_EMBED_CHARS]

    return f"""\
## Company Profile

Interest areas:
{interest_bullets}

Business fields:
{field_bullets}

Past project categories:
{category_bullets}

## Evaluation Dimensions

{dim_descriptions}

## EU Funding Opportunity

Reference: {reference}
Kind: {kind}
Title: {title or "(not provided)"}
URL: {url or "(not provided)"}
Deadline: {deadline_date or "(not provided)"}
Description / Conditions:
{content}

## Instructions

Evaluate each dimension defined above and return a single JSON object with this exact structure:

{{
  "dimensions": [
{dim_schema}
  ],
  "human_summary": "<1–2 sentences for the human reviewer capturing the most relevant aspect for the go/no-go decision>"
}}

Rules:
- Return exactly one entry per defined dimension, in the same order.
- "name" must match the dimension name exactly.
- "score" is an integer 0–5 per the scale above.
- "reasoning": at most one short direct sentence; only the main reason for the score.
- "human_summary": at most two sentences; mention the most relevant aspect for the funding decision.\
"""
