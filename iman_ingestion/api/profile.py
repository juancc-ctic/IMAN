"""Endpoints for the singleton company profile."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from iman_ingestion.api.schemas import CompanyProfileOut, CompanyProfilePut
from iman_ingestion.db.models import CompanyProfileRecord
from iman_ingestion.db.session import session_scope

router = APIRouter(prefix="/company-profile", tags=["company-profile"])


def _profile_to_out(p: CompanyProfileRecord) -> CompanyProfileOut:
    return CompanyProfileOut(
        id=p.id,
        interest_areas=p.interest_areas,
        company_fields=p.company_fields,
        past_tender_categories=p.past_tender_categories,
        triage_dimensions=p.triage_dimensions,
        tender_filters=p.tender_filters,
        action_plan_text=p.action_plan_text,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


@router.get("", response_model=CompanyProfileOut)
def get_company_profile() -> CompanyProfileOut:
    with session_scope() as session:
        p = session.get(CompanyProfileRecord, 1)
        if p is None:
            raise HTTPException(status_code=404, detail="Company profile not found — run company_profile_sync first")
        return _profile_to_out(p)


@router.put("", response_model=CompanyProfileOut)
def update_company_profile(body: CompanyProfilePut) -> CompanyProfileOut:
    with session_scope() as session:
        p = session.get(CompanyProfileRecord, 1)
        if p is None:
            p = CompanyProfileRecord(id=1)
            session.add(p)
        updates = body.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(p, field, value)
        p.updated_at = datetime.now(timezone.utc)
        session.flush()
        return _profile_to_out(p)
