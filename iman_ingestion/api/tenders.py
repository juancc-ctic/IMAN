"""CRUD endpoints for the tenders table."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, asc, desc

from iman_ingestion.api.schemas import TenderOut, TenderPatch
from iman_ingestion.db.models import Tender
from iman_ingestion.db.session import session_scope

router = APIRouter(prefix="/tenders", tags=["tenders"])


class TenderSortField(str, Enum):
    triage_score = "triage_score"
    created_at = "created_at"
    updated_at = "updated_at"
    title = "title"
    party_name = "party_name"
    submission_deadline = "submission_deadline"


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


def _tender_to_out(t: Tender) -> TenderOut:
    return TenderOut(
        id=t.id,
        link=t.link,
        title=t.title,
        party_name=t.party_name,
        tax_exclusive_amount=t.tax_exclusive_amount,
        estimated_overall_contract_amount=t.estimated_overall_contract_amount,
        pcap_url=t.pcap_url,
        ppt_url=t.ppt_url,
        submission_deadline=t.submission_deadline,
        execution_period=t.execution_period,
        enrichment=t.enrichment,
        summary=t.summary,
        triage=t.triage,
        triage_score=t.triage_score,
        created_at=t.created_at.isoformat() if t.created_at else None,
        updated_at=t.updated_at.isoformat() if t.updated_at else None,
    )


@router.get("", response_model=list[TenderOut])
def list_tenders(
    skip: int = 0,
    limit: int = 50,
    min_score: Optional[float] = None,
    sort_by: TenderSortField = TenderSortField.triage_score,
    order: SortOrder = SortOrder.desc,
) -> list[TenderOut]:
    with session_scope() as session:
        stmt = select(Tender)
        if min_score is not None:
            stmt = stmt.where(Tender.triage_score >= min_score)
        col = getattr(Tender, sort_by.value)
        direction = desc(col).nulls_last() if order == SortOrder.desc else asc(col).nulls_last()
        stmt = stmt.order_by(direction).offset(skip).limit(limit)
        rows = session.scalars(stmt).all()
        return [_tender_to_out(t) for t in rows]


@router.get("/{tender_id}", response_model=TenderOut)
def get_tender(tender_id: str) -> TenderOut:
    with session_scope() as session:
        t = session.get(Tender, tender_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Tender not found")
        return _tender_to_out(t)


@router.patch("/{tender_id}", response_model=TenderOut)
def patch_tender(tender_id: str, body: TenderPatch) -> TenderOut:
    with session_scope() as session:
        t = session.get(Tender, tender_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Tender not found")
        updates = body.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(t, field, value)
        session.flush()
        return _tender_to_out(t)
