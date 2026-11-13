"""Tender Lifecycle rules applied to the database through a caller-owned Session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from iman_ingestion.aggregated.ingestion import parse_atom_datetime
from iman_ingestion.db.models import Tender, TenderAward, TenderTimelineEntry

DETAIL_FIELDS = (
    "link",
    "title",
    "party_name",
    "tax_exclusive_amount",
    "estimated_overall_contract_amount",
    "submission_deadline",
    "pcap_url",
    "ppt_url",
)


TERMINAL_STATUSES = frozenset({"RES", "ANUL"})
_END_REASON_BY_STATUS = {"RES": "resolved", "ANUL": "cancelled"}


class TenderNotFound(LookupError):
    """No Tender with that id exists in IMAN."""


class TrackingRefused(ValueError):
    """The Tender cannot be tracked (it already reached a terminal Tender Status)."""


class NotTracked(ValueError):
    """The Tender is not currently a Tracked Tender."""


@dataclass(frozen=True)
class TimelineEntry:
    kind: str  # tracking_started | tracking_stopped | change | tracking_ended
    occurred_at: datetime
    by: Optional[str]
    status_before: Optional[str]
    status_after: Optional[str]
    changes: Optional[dict]
    snapshot: Optional[dict]
    end_reason: Optional[str]


@dataclass(frozen=True)
class IngestionScope:
    """Which Tender ids ingestion must treat specially."""

    tracked_ids: frozenset
    known_ids: frozenset


@dataclass
class ApplySummary:
    tenders_created: int = 0
    tracked_versions: int = 0
    changes_recorded: int = 0


@dataclass(frozen=True)
class TenderState:
    id: str
    status: Optional[str]
    tracked: bool
    tracked_by: Optional[str]
    tracked_since: Optional[datetime]
    tracking_ended_at: Optional[datetime]
    tracking_end_reason: Optional[str]
    details: Dict[str, Any]
    enrichment: Optional[dict]
    triage: Optional[dict]
    triage_score: Optional[float]


def _state(t: Tender) -> TenderState:
    return TenderState(
        id=t.id,
        status=t.status,
        tracked=bool(t.tracked),
        tracked_by=t.tracked_by,
        tracked_since=t.tracked_since,
        tracking_ended_at=t.tracking_ended_at,
        tracking_end_reason=t.tracking_end_reason,
        details={f: getattr(t, f) for f in DETAIL_FIELDS},
        enrichment=t.enrichment,
        triage=t.triage,
        triage_score=t.triage_score,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


AWARD_FIELDS = (
    "lot_id",
    "lot_name",
    "result_code",
    "void",
    "winner_name",
    "winner_nif",
    "award_amount",
    "bids_received",
)


@dataclass(frozen=True)
class Award:
    lot_id: str
    lot_name: Optional[str]
    result_code: Optional[str]
    void: bool
    winner_name: Optional[str]
    winner_nif: Optional[str]
    award_amount: Optional[str]
    bids_received: Optional[int]
    observed_at: datetime


def _lot_sort_key(lot_id: str) -> tuple:
    return (0, int(lot_id), "") if lot_id.isdigit() else (1, 0, lot_id)


def awards(session: Session, tender_id: str) -> List[Award]:
    """Latest observed Award of each Lot of a Tender, ordered by Lot."""
    rows = session.scalars(select(TenderAward).where(TenderAward.tender_id == tender_id)).all()
    rows = sorted(rows, key=lambda r: _lot_sort_key(r.lot_id))
    return [Award(**{f: getattr(r, f) for f in AWARD_FIELDS}, observed_at=r.observed_at) for r in rows]


def _snapshot(session: Session, t: Tender) -> Dict[str, Any]:
    """The Tender's current observable state, as compared between versions."""
    snap: Dict[str, Any] = {f: getattr(t, f) for f in DETAIL_FIELDS}
    snap["status"] = t.status
    snap["lots"] = list(t.lots or [])
    snap["awards"] = [
        {f: getattr(a, f) for f in AWARD_FIELDS} for a in awards(session, t.id)
    ]
    return snap


def _store_awards(session: Session, t: Tender, v: Dict[str, Any], at: datetime) -> None:
    lot_names = {lot.get("id"): lot.get("name") for lot in (v.get("lots") or [])}
    for a in v.get("awards") or []:
        row = session.get(TenderAward, (t.id, a["lot_id"]))
        if row is None:
            row = TenderAward(tender_id=t.id, lot_id=a["lot_id"])
            session.add(row)
        row.lot_name = lot_names.get(a["lot_id"])
        for f in AWARD_FIELDS[2:]:
            setattr(row, f, a.get(f))
        row.observed_at = at


def _add_entry(session: Session, tender_id: str, kind: str, occurred_at: datetime, **fields: Any) -> None:
    session.add(TenderTimelineEntry(tender_id=tender_id, kind=kind, occurred_at=occurred_at, **fields))


def start_tracking(session: Session, tender_id: str, *, by: str) -> TenderState:
    """Make a Tender a Tracked Tender; harmless if it already is one."""
    t = session.get(Tender, tender_id)
    if t is None:
        raise TenderNotFound(tender_id)
    if t.tracked:
        return _state(t)
    if t.status in TERMINAL_STATUSES:
        raise TrackingRefused(
            f"Tender {tender_id} is already {t.status}; there is nothing left to track."
        )
    now = _now()
    t.tracked = True
    t.tracked_by = by
    t.tracked_since = now
    t.tracking_ended_at = None
    t.tracking_end_reason = None
    _add_entry(session, t.id, "tracking_started", now, by=by, snapshot=_snapshot(session, t))
    session.flush()
    return _state(t)


def stop_tracking(session: Session, tender_id: str, *, by: str) -> TenderState:
    """Stop tracking a Tracked Tender at a person's request."""
    t = session.get(Tender, tender_id)
    if t is None:
        raise TenderNotFound(tender_id)
    if not t.tracked:
        raise NotTracked(tender_id)
    now = _now()
    _end_tracking(session, t, "manual", now, kind="tracking_stopped", by=by)
    session.flush()
    return _state(t)


def _end_tracking(
    session: Session,
    t: Tender,
    reason: str,
    at: datetime,
    *,
    kind: str = "tracking_ended",
    by: Optional[str] = None,
) -> None:
    t.tracked = False
    t.tracking_ended_at = at
    t.tracking_end_reason = reason
    _add_entry(session, t.id, kind, at, by=by, end_reason=reason)


def timeline(session: Session, tender_id: str) -> List[TimelineEntry]:
    """Timeline of tracking actions and Tender Changes, in the order IMAN observed them.

    ``occurred_at`` is the feed's date for feed-driven entries (and wall-clock
    time for human actions); ordering by observation keeps "tracking started"
    before changes first seen afterwards even if the feed dated them earlier.
    """
    rows = session.scalars(
        select(TenderTimelineEntry)
        .where(TenderTimelineEntry.tender_id == tender_id)
        .order_by(TenderTimelineEntry.id)
    ).all()
    return [
        TimelineEntry(
            kind=r.kind,
            occurred_at=r.occurred_at,
            by=r.by,
            status_before=r.status_before,
            status_after=r.status_after,
            changes=r.changes,
            snapshot=r.snapshot,
            end_reason=r.end_reason,
        )
        for r in rows
    ]


def list_tracked(session: Session, *, ended: bool = False) -> List[TenderState]:
    """Currently Tracked Tenders, or (``ended=True``) those whose tracking ended."""
    stmt = select(Tender)
    if ended:
        stmt = stmt.where(Tender.tracked.is_(False), Tender.tracking_ended_at.is_not(None))
        stmt = stmt.order_by(Tender.tracking_ended_at.desc(), Tender.id)
    else:
        stmt = stmt.where(Tender.tracked.is_(True)).order_by(Tender.tracked_since.desc(), Tender.id)
    return [_state(t) for t in session.scalars(stmt).all()]


def ingestion_scope(session: Session) -> IngestionScope:
    """Tracked and known Tender ids, for configuring an ingestion run."""
    rows = session.execute(select(Tender.id, Tender.tracked)).all()
    return IngestionScope(
        tracked_ids=frozenset(r.id for r in rows if r.tracked),
        known_ids=frozenset(r.id for r in rows),
    )


def get_tender(session: Session, tender_id: str) -> Optional[TenderState]:
    t = session.get(Tender, tender_id)
    return _state(t) if t is not None else None


def _version_time(v: Dict[str, Any]) -> datetime:
    raw = v.get("updated")
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parse_atom_datetime(raw)


def _refresh(t: Tender, v: Dict[str, Any], at: datetime) -> None:
    for f in DETAIL_FIELDS:
        setattr(t, f, v.get(f))
    t.status = v.get("status")
    t.lots = v.get("lots") or []
    t.feed_updated_at = at


def apply_feed_versions(session: Session, versions: Iterable[Dict[str, Any]]) -> ApplySummary:
    """Apply one ingestion run's feed versions, oldest first."""
    summary = ApplySummary()
    ordered: List[Dict[str, Any]] = sorted(
        (v for v in versions if v.get("id")), key=_version_time
    )
    for v in ordered:
        at = _version_time(v)
        tender = session.get(Tender, v["id"])
        if v.get("deleted"):
            if tender is not None and tender.tracked:
                _end_tracking(session, tender, "withdrawn", at)
            continue
        if tender is None:
            if not v.get("discovered"):
                continue
            tender = Tender(id=v["id"], tracked=False)
            session.add(tender)
            _refresh(tender, v, at)
            summary.tenders_created += 1
            continue
        if tender.feed_updated_at is not None and at <= tender.feed_updated_at:
            continue
        if not tender.tracked:
            _refresh(tender, v, at)
            continue
        summary.tracked_versions += 1
        if _apply_tracked_version(session, tender, v, at):
            summary.changes_recorded += 1
    session.flush()
    return summary


def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        k: {"before": before.get(k), "after": after.get(k)}
        for k in after
        if before.get(k) != after.get(k)
    }


def _apply_tracked_version(session: Session, t: Tender, v: Dict[str, Any], at: datetime) -> bool:
    """Apply one version to a Tracked Tender; return True if a Tender Change was recorded."""
    before = _snapshot(session, t)
    _refresh(t, v, at)
    _store_awards(session, t, v, at)
    session.flush()
    after = _snapshot(session, t)
    changes = _diff(before, after)
    if changes:
        _add_entry(
            session,
            t.id,
            "change",
            at,
            status_before=before["status"],
            status_after=after["status"],
            changes=changes,
        )
    if t.status in TERMINAL_STATUSES:
        _end_tracking(session, t, _END_REASON_BY_STATUS[t.status], at)
    return bool(changes)
