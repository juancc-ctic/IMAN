"""Pydantic request/response schemas for the IMAN REST API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class RunLaunchResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str


# ---------------------------------------------------------------------------
# Tenders
# ---------------------------------------------------------------------------

class TenderOut(BaseModel):
    id: str
    link: Optional[str] = None
    title: Optional[str] = None
    party_name: Optional[str] = None
    tax_exclusive_amount: Optional[str] = None
    estimated_overall_contract_amount: Optional[str] = None
    pcap_url: Optional[str] = None
    ppt_url: Optional[str] = None
    submission_deadline: Optional[str] = None
    execution_period: Optional[str] = None
    enrichment: Optional[dict[str, Any]] = None
    summary: Optional[str] = None
    triage: Optional[dict[str, Any]] = None
    triage_score: Optional[float] = None
    status: Optional[str] = None
    tracked: bool = False
    tracked_by: Optional[str] = None
    tracked_since: Optional[str] = None
    tracking_ended_at: Optional[str] = None
    tracking_end_reason: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"from_attributes": True}


class TrackingRequest(BaseModel):
    """Who is starting or stopping tracking (free text)."""

    by: str = Field(min_length=1)


class TimelineEntryOut(BaseModel):
    kind: str
    occurred_at: str
    by: Optional[str] = None
    status_before: Optional[str] = None
    status_after: Optional[str] = None
    changes: Optional[dict[str, Any]] = None
    snapshot: Optional[dict[str, Any]] = None
    end_reason: Optional[str] = None


class AwardOut(BaseModel):
    lot_id: str
    lot_name: Optional[str] = None
    result_code: Optional[str] = None
    void: bool = False
    winner_name: Optional[str] = None
    winner_nif: Optional[str] = None
    award_amount: Optional[str] = None
    bids_received: Optional[int] = None
    observed_at: str


class TenderTimelineOut(BaseModel):
    tender_id: str
    entries: list[TimelineEntryOut]
    awards: list[AwardOut]


class TenderPatch(BaseModel):
    enrichment: Optional[dict[str, Any]] = None
    summary: Optional[str] = None
    triage: Optional[dict[str, Any]] = None
    triage_score: Optional[float] = None


# ---------------------------------------------------------------------------
# EU Items
# ---------------------------------------------------------------------------

class EuItemOut(BaseModel):
    reference: str
    kind: str
    url: Optional[str] = None
    identifier: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[str] = None
    deadline_date: Optional[str] = None
    framework_programme: Optional[str] = None
    programme_period: Optional[str] = None
    programme_division: Optional[str] = None
    programme_part: Optional[str] = None
    mission_group: Optional[str] = None
    item_metadata: Optional[dict[str, Any]] = None
    embed_text: Optional[str] = None
    triage: Optional[dict[str, Any]] = None
    triage_score: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"from_attributes": True}


class EuItemPatch(BaseModel):
    triage: Optional[dict[str, Any]] = None
    triage_score: Optional[float] = None
    embed_text: Optional[str] = None
    item_metadata: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# EU Organizations
# ---------------------------------------------------------------------------

class EuOrganizationOut(BaseModel):
    organisation_id: str
    name: Optional[str] = None
    country: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    interest: Optional[str] = None
    why: Optional[str] = None

    model_config = {"from_attributes": True}


class EuOrganizationPatch(BaseModel):
    interest: Optional[str] = None
    why: Optional[str] = None


# ---------------------------------------------------------------------------
# EU Projects
# ---------------------------------------------------------------------------

class EuProjectOut(BaseModel):
    project_id: str
    acronym: Optional[str] = None
    title: Optional[str] = None
    program: Optional[str] = None
    keywords: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Company Profile
# ---------------------------------------------------------------------------

class CompanyProfileOut(BaseModel):
    id: int
    interest_areas: Optional[Any] = None
    company_fields: Optional[Any] = None
    past_tender_categories: Optional[Any] = None
    triage_dimensions: Optional[Any] = None
    tender_filters: Optional[Any] = None
    action_plan_text: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"from_attributes": True}


class CompanyProfilePut(BaseModel):
    interest_areas: Optional[Any] = None
    company_fields: Optional[Any] = None
    past_tender_categories: Optional[Any] = None
    triage_dimensions: Optional[Any] = None
    tender_filters: Optional[Any] = None
    action_plan_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Partner recommendations
# ---------------------------------------------------------------------------

class PartnerRecommendRequest(BaseModel):
    coordinator: bool = False
    top_k: int = 50
    top_n: int = 5
