"""DB-backed partner recommender using pgvector ANN search."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _parse_interest(raw: str | None) -> float | None:
    """Cast the Text interest field to float; returns None if missing or non-numeric."""
    if not raw:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _vec_to_pg_literal(embedding: list[float]) -> str:
    """Convert a Python float list to pgvector literal format '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(v) for v in embedding) + "]"


def recommend_partners(
    session: Session,
    target_embedding: list[float],
    coordinator_search: bool,
    top_k_search: int = 50,
    top_n_results: int = 5,
) -> list[dict[str, Any]]:
    """Return top partner recommendations for a target embedding.

    Uses pgvector HNSW ANN search to find the most similar CORDIS projects,
    then scores participating organisations by technical fit, role history,
    and internal trust rating.

    Args:
        session: Active SQLAlchemy session.
        target_embedding: Embedding vector of the EU item / call.
        coordinator_search: True to boost organisations with a coordinator history.
        top_k_search: Number of similar projects to retrieve via ANN index.
        top_n_results: Number of partner organisations to return.

    Returns:
        List of dicts sorted by score descending, each with keys:
        ``organisationID``, ``name``, ``score``, ``explicacion``.
    """
    target_literal = _vec_to_pg_literal(target_embedding)

    # Step 1: ANN search — top_k_search most similar projects via HNSW index.
    # CAST(:target AS vector) is required; PostgreSQL cannot infer the vector
    # type from a bound string parameter without an explicit cast.
    top_rows = session.execute(
        text(
            """
            SELECT
                project_id,
                title,
                1.0 - (embedding <=> CAST(:target AS vector)) AS sim_score
            FROM eu_projects
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:target AS vector)
            LIMIT :top_k
            """
        ),
        {"target": target_literal, "top_k": top_k_search},
    ).fetchall()

    if not top_rows:
        return []

    project_ids = [r.project_id for r in top_rows]
    sim_by_project: dict[str, float] = {r.project_id: float(r.sim_score) for r in top_rows}

    # Step 2: participations + organisation metadata for the matched projects only.
    part_rows = session.execute(
        text(
            """
            SELECT
                ep.project_id,
                ep.organisation_id,
                ep.role,
                eo.name,
                eo.interest
            FROM eu_participations ep
            JOIN eu_organizations eo ON eo.organisation_id = ep.organisation_id
            WHERE ep.project_id = ANY(:project_ids)
            """
        ),
        {"project_ids": project_ids},
    ).fetchall()

    if not part_rows:
        return []

    # Step 3: group by organisation and compute scores.
    org_data: dict[str, dict] = {}
    for pr in part_rows:
        oid = pr.organisation_id
        if oid not in org_data:
            org_data[oid] = {
                "name": pr.name or "",
                "interest_raw": pr.interest,
                "sim_scores": [],
                "roles": [],
            }
        org_data[oid]["sim_scores"].append(sim_by_project.get(pr.project_id, 0.0))
        org_data[oid]["roles"].append(pr.role or "")

    recommendations: list[dict[str, Any]] = []

    for org_id, data in org_data.items():
        sim_scores = data["sim_scores"]
        roles = data["roles"]
        num_projects = len(sim_scores)
        avg_sim = sum(sim_scores) / num_projects

        # A. Base score: sum of squared similarities
        s_exp = sum(s ** 2 for s in sim_scores)

        # B. Role multiplier
        total_roles = len(roles)
        pct_coordinator = sum(1 for r in roles if r == "coordinator") / total_roles

        if coordinator_search:
            m_role = 1.0 + (0.2 * pct_coordinator)
            role_reason = f"{pct_coordinator * 100:.0f}% de veces como coordinador."
        else:
            m_role = 1.0
            role_reason = "Búsqueda sin preferencia de rol."

        # C. Interest/trust multiplier
        interest_val = _parse_interest(data["interest_raw"])
        if interest_val is None:
            m_int = 1.0
            trust_reason = "Socio nuevo (sin historial de interés)."
        else:
            m_int = 0.5 + (interest_val / 5.0)
            trust_reason = f"Nota interna de confianza: {interest_val:.1f}/5."

        # D. Final score
        final_score = s_exp * m_role * m_int

        recommendations.append({
            "organisationID": org_id,
            "name": data["name"],
            "score": round(final_score, 2),
            "explicacion": {
                "1_dominio_tecnico": (
                    f"{num_projects} proyectos afines encontrados "
                    f"(Similitud media: {avg_sim:.2f})."
                ),
                "2_afinidad_rol": role_reason,
                "3_confianza": trust_reason,
            },
        })

    recommendations.sort(key=lambda x: x["score"], reverse=True)
    return recommendations[:top_n_results]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recommend CORDIS partners for a given EU item."
    )
    parser.add_argument("reference", help="EU item reference (primary key in eu_items)")
    parser.add_argument(
        "--coordinator", action="store_true", help="Boost organisations with coordinator history"
    )
    parser.add_argument("--top-k", type=int, default=50, help="ANN candidate pool size (default: 50)")
    parser.add_argument("--top-n", type=int, default=5, help="Partners to return (default: 5)")
    args = parser.parse_args()

    from iman_ingestion.db.models import EuItem
    from iman_ingestion.db.session import session_scope

    with session_scope() as session:
        item = session.get(EuItem, args.reference)
        if item is None:
            print(f"ERROR: EU item not found: {args.reference!r}", file=sys.stderr)
            sys.exit(1)
        if item.embedding is None:
            print(f"ERROR: EU item has no embedding: {args.reference!r}", file=sys.stderr)
            sys.exit(1)

        results = recommend_partners(
            session=session,
            target_embedding=list(item.embedding),
            coordinator_search=args.coordinator,
            top_k_search=args.top_k,
            top_n_results=args.top_n,
        )

    print(json.dumps(results, indent=2, ensure_ascii=False))
