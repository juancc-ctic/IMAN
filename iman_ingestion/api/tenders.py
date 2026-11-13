"""CRUD endpoints for the tenders table."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, asc, desc

from iman_ingestion import lifecycle
from iman_ingestion.api.schemas import (
    AwardOut,
    TenderOut,
    TenderPatch,
    TenderTimelineOut,
    TimelineEntryOut,
    TrackingRequest,
)
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
        status=t.status,
        tracked=bool(t.tracked),
        tracked_by=t.tracked_by,
        tracked_since=t.tracked_since.isoformat() if t.tracked_since else None,
        tracking_ended_at=t.tracking_ended_at.isoformat() if t.tracking_ended_at else None,
        tracking_end_reason=t.tracking_end_reason,
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


@router.get("/tracked", response_model=list[TenderOut])
def list_tracked_tenders(ended: bool = False) -> list[TenderOut]:
    """Tracked Tenders; with ``ended=true``, Tenders whose tracking has ended."""
    with session_scope() as session:
        states = lifecycle.list_tracked(session, ended=ended)
        return [_tender_to_out(session.get(Tender, s.id)) for s in states]


@router.post("/{tender_id}/tracking", response_model=TenderOut)
def start_tracking(tender_id: str, body: TrackingRequest) -> TenderOut:
    """Make a Tender a Tracked Tender (harmless if it already is one)."""
    with session_scope() as session:
        try:
            lifecycle.start_tracking(session, tender_id, by=body.by)
        except lifecycle.TenderNotFound:
            raise HTTPException(status_code=404, detail="Tender not found")
        except lifecycle.TrackingRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _tender_to_out(session.get(Tender, tender_id))


@router.post("/{tender_id}/tracking/stop", response_model=TenderOut)
def stop_tracking(tender_id: str, body: TrackingRequest) -> TenderOut:
    """Stop tracking a Tracked Tender."""
    with session_scope() as session:
        try:
            lifecycle.stop_tracking(session, tender_id, by=body.by)
        except lifecycle.TenderNotFound:
            raise HTTPException(status_code=404, detail="Tender not found")
        except lifecycle.NotTracked:
            raise HTTPException(status_code=409, detail="Tender is not being tracked")
        return _tender_to_out(session.get(Tender, tender_id))


@router.get("/{tender_id}/timeline", response_model=TenderTimelineOut)
def get_timeline(tender_id: str) -> TenderTimelineOut:
    """Tracking actions, Tender Changes and per-Lot Awards of a Tender."""
    with session_scope() as session:
        if lifecycle.get_tender(session, tender_id) is None:
            raise HTTPException(status_code=404, detail="Tender not found")
        entries = [
            TimelineEntryOut(
                kind=e.kind,
                occurred_at=e.occurred_at.isoformat(),
                by=e.by,
                status_before=e.status_before,
                status_after=e.status_after,
                changes=e.changes,
                snapshot=e.snapshot,
                end_reason=e.end_reason,
            )
            for e in lifecycle.timeline(session, tender_id)
        ]
        award_rows = [
            AwardOut(
                lot_id=a.lot_id,
                lot_name=a.lot_name,
                result_code=a.result_code,
                void=a.void,
                winner_name=a.winner_name,
                winner_nif=a.winner_nif,
                award_amount=a.award_amount,
                bids_received=a.bids_received,
                observed_at=a.observed_at.isoformat(),
            )
            for a in lifecycle.awards(session, tender_id)
        ]
        return TenderTimelineOut(tender_id=tender_id, entries=entries, awards=award_rows)


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
