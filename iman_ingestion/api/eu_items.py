"""CRUD + partner recommendations endpoints for EU items."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, asc, desc

from iman_ingestion.api.schemas import EuItemOut, EuItemPatch, PartnerRecommendRequest
from iman_ingestion.db.models import EuItem
from iman_ingestion.db.session import session_scope
from iman_ingestion.partner_recommender import recommend_partners

router = APIRouter(prefix="/eu-items", tags=["eu-items"])


class EuItemSortField(str, Enum):
    triage_score = "triage_score"
    created_at = "created_at"
    updated_at = "updated_at"
    title = "title"
    deadline_date = "deadline_date"
    start_date = "start_date"


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


def _item_to_out(item: EuItem) -> EuItemOut:
    return EuItemOut(
        reference=item.reference,
        kind=item.kind,
        url=item.url,
        identifier=item.identifier,
        title=item.title,
        status=item.status,
        start_date=item.start_date,
        deadline_date=item.deadline_date,
        framework_programme=item.framework_programme,
        programme_period=item.programme_period,
        programme_division=item.programme_division,
        programme_part=item.programme_part,
        mission_group=item.mission_group,
        item_metadata=item.item_metadata,
        embed_text=item.embed_text,
        triage=item.triage,
        triage_score=item.triage_score,
        created_at=item.created_at.isoformat() if item.created_at else None,
        updated_at=item.updated_at.isoformat() if item.updated_at else None,
    )


@router.get("", response_model=list[EuItemOut])
def list_eu_items(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    min_score: Optional[float] = None,
    sort_by: EuItemSortField = EuItemSortField.triage_score,
    order: SortOrder = SortOrder.desc,
) -> list[EuItemOut]:
    with session_scope() as session:
        stmt = select(EuItem)
        if status:
            stmt = stmt.where(EuItem.status == status)
        if kind:
            stmt = stmt.where(EuItem.kind == kind)
        if min_score is not None:
            stmt = stmt.where(EuItem.triage_score >= min_score)
        col = getattr(EuItem, sort_by.value)
        direction = desc(col).nulls_last() if order == SortOrder.desc else asc(col).nulls_last()
        stmt = stmt.order_by(direction).offset(skip).limit(limit)
        rows = session.scalars(stmt).all()
        return [_item_to_out(r) for r in rows]


@router.get("/{reference}", response_model=EuItemOut)
def get_eu_item(reference: str) -> EuItemOut:
    with session_scope() as session:
        item = session.get(EuItem, reference)
        if item is None:
            raise HTTPException(status_code=404, detail="EU item not found")
        return _item_to_out(item)


@router.patch("/{reference}", response_model=EuItemOut)
def patch_eu_item(reference: str, body: EuItemPatch) -> EuItemOut:
    with session_scope() as session:
        item = session.get(EuItem, reference)
        if item is None:
            raise HTTPException(status_code=404, detail="EU item not found")
        updates = body.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(item, field, value)
        session.flush()
        return _item_to_out(item)


@router.post("/{reference}/partner-recommendations", response_model=list[dict[str, Any]])
def get_partner_recommendations(reference: str, body: PartnerRecommendRequest) -> list[dict[str, Any]]:
    with session_scope() as session:
        item = session.get(EuItem, reference)
        if item is None:
            raise HTTPException(status_code=404, detail="EU item not found")
        if item.embedding is None:
            raise HTTPException(status_code=422, detail="EU item has no embedding; run eu_full_pipeline first")
        results = recommend_partners(
            session=session,
            target_embedding=list(item.embedding),
            coordinator_search=body.coordinator,
            top_k_search=body.top_k,
            top_n_results=body.top_n,
        )
    return results
