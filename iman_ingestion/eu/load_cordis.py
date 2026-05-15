"""CLI loader for CORDIS CSV data into eu_organizations / eu_projects / eu_participations."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from iman_ingestion.db.models import EuOrganization, EuParticipation, EuProject
from iman_ingestion.db.session import session_scope
from iman_ingestion.llm.client import embed_texts, get_embeddings_client

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data-sources" / "Europe"


def _parse_cost(raw: str) -> Optional[Decimal]:
    if not raw or not raw.strip():
        return None
    # CORDIS exports use European decimal comma: "3094041,25" → 3094041.25
    try:
        return Decimal(raw.strip().replace(",", "."))
    except InvalidOperation:
        return None


def _parse_geolocation(raw: str) -> tuple[Optional[float], Optional[float]]:
    if not raw or not raw.strip():
        return None, None
    parts = raw.strip().split(",")
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


def _load_organizations(path: Path, session, limit: int = 0) -> int:
    count = 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            if limit and count + len(rows) >= limit:
                break
            lat, lon = _parse_geolocation(row.get("geolocation", ""))
            rows.append(
                {
                    "organisation_id": row["organisationID"],
                    "name": row.get("name") or None,
                    "country": row.get("country") or None,
                    "lat": lat,
                    "lon": lon,
                    "interest": row.get("INTEREST") or None,
                    "why": row.get("WHY?") or None,
                }
            )
            if len(rows) >= 500:
                stmt = pg_insert(EuOrganization).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["organisation_id"],
                    set_={c: stmt.excluded[c] for c in ("name", "country", "lat", "lon", "interest", "why")},
                )
                session.execute(stmt)
                count += len(rows)
                rows = []
        if rows:
            stmt = pg_insert(EuOrganization).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["organisation_id"],
                set_={c: stmt.excluded[c] for c in ("name", "country", "lat", "lon", "interest", "why")},
            )
            session.execute(stmt)
            count += len(rows)
    return count


def _load_projects(path: Path, session, limit: int = 0) -> int:
    count = 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            if limit and count + len(rows) >= limit:
                break
            rows.append(
                {
                    "project_id": row["projectID"],
                    "acronym": row.get("projectAcronym") or None,
                    "title": row.get("title") or None,
                    "program": row.get("program") or None,
                    "keywords": row.get("keywords") or None,
                }
            )
            if len(rows) >= 500:
                stmt = pg_insert(EuProject).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["project_id"],
                    set_={c: stmt.excluded[c] for c in ("acronym", "title", "program", "keywords")},
                )
                session.execute(stmt)
                count += len(rows)
                rows = []
        if rows:
            stmt = pg_insert(EuProject).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id"],
                set_={c: stmt.excluded[c] for c in ("acronym", "title", "program", "keywords")},
            )
            session.execute(stmt)
            count += len(rows)
    return count


def _load_participations(path: Path, session, limit: int = 0) -> tuple[int, int]:
    from sqlalchemy import text as sa_text

    valid_projects = {
        r[0] for r in session.execute(sa_text("SELECT project_id FROM eu_projects")).fetchall()
    }
    valid_orgs = {
        r[0] for r in session.execute(sa_text("SELECT organisation_id FROM eu_organizations")).fetchall()
    }

    count = 0
    skipped = 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            if limit and count + len(rows) >= limit:
                break
            pid = row["projectID"]
            oid = row["organisationID"]
            if pid not in valid_projects or oid not in valid_orgs:
                skipped += 1
                continue
            rows.append(
                {
                    "project_id": pid,
                    "organisation_id": oid,
                    "role": row.get("role") or None,
                    "total_cost": _parse_cost(row.get("totalCost", "")),
                }
            )
            if len(rows) >= 500:
                stmt = pg_insert(EuParticipation).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["project_id", "organisation_id"],
                    set_={c: stmt.excluded[c] for c in ("role", "total_cost")},
                )
                session.execute(stmt)
                count += len(rows)
                rows = []
        if rows:
            stmt = pg_insert(EuParticipation).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "organisation_id"],
                set_={c: stmt.excluded[c] for c in ("role", "total_cost")},
            )
            session.execute(stmt)
            count += len(rows)
    return count, skipped


def embed_eu_projects_main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and store embeddings for all eu_projects rows."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("IMAN_EMBED_BATCH_SIZE", "16")),
        metavar="N",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Skip projects that already have an embedding.",
    )
    args = parser.parse_args()

    client = get_embeddings_client()

    with session_scope() as session:
        projects = session.scalars(select(EuProject)).all()
        if args.skip_existing:
            projects = [p for p in projects if p.embedding is None]
        embeddable = [p for p in projects if p.title or p.keywords]

    print(f"Embedding {len(embeddable):,} projects …")
    embedded = 0
    for off in range(0, len(embeddable), args.batch_size):
        batch = embeddable[off : off + args.batch_size]
        texts = [f"{p.title or ''} {p.keywords or ''}".strip() for p in batch]
        vecs = embed_texts(client, texts)
        with session_scope() as session:
            for project, vec in zip(batch, vecs):
                db_project = session.get(EuProject, project.project_id)
                if db_project is not None:
                    db_project.embedding = vec
                    embedded += 1
        if embedded % 1000 < args.batch_size:
            print(f"  {embedded:,} / {len(embeddable):,}")

    print(f"Done. {embedded:,} projects embedded.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load CORDIS organizations/projects/relations CSVs into PostgreSQL."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_DEFAULT_DATA_DIR,
        help="Directory containing organizations.csv, projects.csv, relations.csv",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Max rows to upsert per table (0 = no limit)",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    orgs_path = data_dir / "organizations.csv"
    projects_path = data_dir / "projects.csv"
    relations_path = data_dir / "relations.csv"

    for p in (orgs_path, projects_path, relations_path):
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    limit = args.limit
    limit_note = f" (limit {limit:,})" if limit else ""

    print(f"Loading organizations from {orgs_path}{limit_note} …")
    with session_scope() as session:
        n = _load_organizations(orgs_path, session, limit)
    print(f"  {n:,} organizations upserted")

    print(f"Loading projects from {projects_path}{limit_note} …")
    with session_scope() as session:
        n = _load_projects(projects_path, session, limit)
    print(f"  {n:,} projects upserted")

    print(f"Loading participations from {relations_path}{limit_note} …")
    with session_scope() as session:
        n, skipped = _load_participations(relations_path, session, limit)
    print(f"  {n:,} participations upserted ({skipped:,} skipped — missing project or org)")

    print("Done.")


if __name__ == "__main__":
    main()
