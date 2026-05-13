"""Read-only endpoints for EU projects (CORDIS data)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from iman_ingestion.api.schemas import EuProjectOut
from iman_ingestion.db.models import EuProject
from iman_ingestion.db.session import session_scope

router = APIRouter(prefix="/eu-projects", tags=["eu-projects"])


def _project_to_out(p: EuProject) -> EuProjectOut:
    return EuProjectOut(
        project_id=p.project_id,
        acronym=p.acronym,
        title=p.title,
        program=p.program,
        keywords=p.keywords,
    )


@router.get("", response_model=list[EuProjectOut])
def list_eu_projects(skip: int = 0, limit: int = 50) -> list[EuProjectOut]:
    with session_scope() as session:
        stmt = select(EuProject).order_by(EuProject.title).offset(skip).limit(limit)
        rows = session.scalars(stmt).all()
        return [_project_to_out(p) for p in rows]


@router.get("/{project_id}", response_model=EuProjectOut)
def get_eu_project(project_id: str) -> EuProjectOut:
    with session_scope() as session:
        p = session.get(EuProject, project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return _project_to_out(p)
