"""CRUD endpoints for EU organizations."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from iman_ingestion.api.schemas import EuOrganizationOut, EuOrganizationPatch
from iman_ingestion.db.models import EuOrganization
from iman_ingestion.db.session import session_scope

router = APIRouter(prefix="/eu-organizations", tags=["eu-organizations"])


def _org_to_out(org: EuOrganization) -> EuOrganizationOut:
    return EuOrganizationOut(
        organisation_id=org.organisation_id,
        name=org.name,
        country=org.country,
        lat=org.lat,
        lon=org.lon,
        interest=org.interest,
        why=org.why,
    )


@router.get("", response_model=list[EuOrganizationOut])
def list_eu_organizations(
    skip: int = 0,
    limit: int = 50,
    country: Optional[str] = None,
) -> list[EuOrganizationOut]:
    with session_scope() as session:
        stmt = select(EuOrganization)
        if country:
            stmt = stmt.where(EuOrganization.country == country)
        stmt = stmt.order_by(EuOrganization.name).offset(skip).limit(limit)
        rows = session.scalars(stmt).all()
        return [_org_to_out(o) for o in rows]


@router.get("/{organisation_id}", response_model=EuOrganizationOut)
def get_eu_organization(organisation_id: str) -> EuOrganizationOut:
    with session_scope() as session:
        org = session.get(EuOrganization, organisation_id)
        if org is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        return _org_to_out(org)


@router.patch("/{organisation_id}", response_model=EuOrganizationOut)
def patch_eu_organization(organisation_id: str, body: EuOrganizationPatch) -> EuOrganizationOut:
    with session_scope() as session:
        org = session.get(EuOrganization, organisation_id)
        if org is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        updates = body.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(org, field, value)
        session.flush()
        return _org_to_out(org)
