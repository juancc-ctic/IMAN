"""LLM prompt builders for tender triage evaluation."""

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
1. El perfil de la empresa: áreas de interés, campos de negocio (Ámbito) y categorías de licitaciones anteriores.
2. El análisis estructurado de una licitación ya extraído por otro asistente.

Debes evaluar dos dimensiones:
- Interés de la empresa: en qué medida el alcance, dominio y requisitos de la licitación se alinean \
con las áreas de interés activas de la empresa y sus categorías históricas de presentación.
- Ámbito: si el dominio de la licitación pertenece a uno o más campos de negocio declarados por la empresa.

Responde SIEMPRE con un único objeto JSON válido. Sin bloques de código markdown, sin comentarios antes o después \
del JSON. Usa null para valores desconocidos. Los campos de razonamiento pueden estar en español.\
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

    # Collect soft flags that are triggered
    flags: dict[str, Any] = (enrichment.get("discard_review") or {}).get("criteria_flags") or {}
    triggered_soft = [
        name
        for name, val in flags.items()
        if isinstance(val, dict) and val.get("applies") is True
        # Hard blockers are already filtered before this prompt is called
    ]
    triggered_str = ", ".join(triggered_soft) if triggered_soft else "ninguno"

    packages_raw = enrichment.get("packages")
    packages_str = ""
    if packages_raw:
        try:
            packages_str = json.dumps(packages_raw, ensure_ascii=False)[:_MAX_FIELD_CHARS]
        except Exception:
            packages_str = str(packages_raw)[:_MAX_FIELD_CHARS]

    discard_summary = _cap((enrichment.get("discard_review") or {}).get("summary"))

    return f"""\
## Perfil de empresa

Áreas de interés:
{interest_bullets}

Campos de negocio (Ámbito):
{field_bullets}

Categorías de licitaciones anteriores:
{category_bullets}

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
Flags de descarte activados (excluyendo bloqueantes duros): {triggered_str}

## Instrucciones

Evalúa las dos dimensiones y devuelve un único objeto JSON con esta estructura exacta:

{{
  "interest_match": {{
    "score": <entero 0–10>,
    "reasoning": "<2–4 frases explicando por qué esta licitación encaja o no con las áreas de interés y categorías históricas>"
  }},
  "scope_match": {{
    "matches": <true | false>,
    "matching_fields": ["<campo1>", "<campo2>"],
    "reasoning": "<1–2 frases>"
  }},
  "human_summary": "<1–2 frases para el revisor humano que capturan el aspecto más relevante de esta licitación para la empresa>"
}}

Reglas:
- interest_match.score: 0 = ninguna alineación; 10 = alineación perfecta.
- scope_match.matches: true si el ámbito pertenece a uno o más campos de negocio de la empresa.
- matching_fields: solo los campos de negocio que coincidan; lista vacía si ninguno.
- human_summary: orientado al revisor; menciona el aspecto más relevante para la decisión de concurrir.\
"""
