"""CLI loader for CORDIS CSV data into eu_organizations / eu_projects / eu_participations."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

from iman_ingestion.db.models import EuOrganization, EuParticipation, EuProject
from iman_ingestion.db.session import session_scope

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


def _load_organizations(path: Path, session) -> int:
    count = 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
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


def _load_projects(path: Path, session) -> int:
    count = 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
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


def _load_participations(path: Path, session) -> tuple[int, int]:
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
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    orgs_path = data_dir / "organizations.csv"
    projects_path = data_dir / "projects.csv"
    relations_path = data_dir / "relations.csv"

    for p in (orgs_path, projects_path, relations_path):
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    print(f"Loading organizations from {orgs_path} …")
    with session_scope() as session:
        n = _load_organizations(orgs_path, session)
    print(f"  {n:,} organizations upserted")

    print(f"Loading projects from {projects_path} …")
    with session_scope() as session:
        n = _load_projects(projects_path, session)
    print(f"  {n:,} projects upserted")

    print(f"Loading participations from {relations_path} …")
    with session_scope() as session:
        n, skipped = _load_participations(relations_path, session)
    print(f"  {n:,} participations upserted ({skipped:,} skipped — missing project or org)")

    print("Done.")


if __name__ == "__main__":
    main()
