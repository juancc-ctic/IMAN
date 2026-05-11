"""Load and expose the company profile used for tender triage."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


_DEFAULT_STATUSES: frozenset[str] = frozenset({"PRE", "PUB"})
_DEFAULT_TYPE_CODE = "2"
_DEFAULT_SUBTYPE_CODES: frozenset[str] = frozenset(
    {"5", "7", "8", "9", "11", "12", "20", "23", "24", "25", "27"}
)
_DEFAULT_CPV_PREFIX = "72"


@dataclass(frozen=True)
class TenderFilters:
    contract_folder_statuses: frozenset[str] = field(default_factory=lambda: _DEFAULT_STATUSES)
    contract_type_code: str = _DEFAULT_TYPE_CODE
    contract_subtype_codes: frozenset[str] = field(default_factory=lambda: _DEFAULT_SUBTYPE_CODES)
    cpv_it_services_prefix: str = _DEFAULT_CPV_PREFIX


@dataclass(frozen=True)
class TriageDimension:
    name: str
    description: str
    weight: float = 1.0


@dataclass(frozen=True)
class CompanyProfile:
    interest_areas: list[str]
    company_fields: list[str]
    past_tender_categories: list[str]
    triage_dimensions: list[TriageDimension] = field(default_factory=list)
    tender_filters: TenderFilters = field(default_factory=TenderFilters)
    action_plan_text: str = ""


def _default_profile_path() -> Path:
    env = os.environ.get("IMAN_COMPANY_PROFILE_PATH", "").strip()
    if env:
        return Path(env)
    return Path(__file__).parent.parent.parent / "company_profile.yaml"


def _parse_tender_filters(data: dict) -> TenderFilters:
    raw = data.get("tender_filters") or {}
    if not isinstance(raw, dict):
        return TenderFilters()

    statuses_raw = raw.get("contract_folder_statuses")
    statuses = frozenset(str(s) for s in statuses_raw) if statuses_raw else _DEFAULT_STATUSES

    type_code = str(raw["contract_type_code"]).strip() if raw.get("contract_type_code") else _DEFAULT_TYPE_CODE

    subtypes_raw = raw.get("contract_subtype_codes")
    subtypes = frozenset(str(s) for s in subtypes_raw) if subtypes_raw else _DEFAULT_SUBTYPE_CODES

    cpv = str(raw.get("cpv_it_services_prefix") or "").strip()
    if not cpv and "cpv_it_services_prefix" not in raw:
        cpv = _DEFAULT_CPV_PREFIX

    return TenderFilters(
        contract_folder_statuses=statuses,
        contract_type_code=type_code,
        contract_subtype_codes=subtypes,
        cpv_it_services_prefix=cpv,
    )


def _parse_triage_dimensions(data: dict) -> list[TriageDimension]:
    raw = data.get("triage_dimensions") or []
    dims: list[TriageDimension] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            try:
                weight = float(item.get("weight") or 1.0)
            except (TypeError, ValueError):
                weight = 1.0
            dims.append(TriageDimension(
                name=str(item["name"]).strip(),
                description=str(item.get("description") or "").strip(),
                weight=weight,
            ))
    return dims


def load_company_profile(path: Path | None = None) -> CompanyProfile:
    """Load ``company_profile.yaml`` and return a :class:`CompanyProfile`.

    Raises:
        FileNotFoundError: if the YAML file does not exist at the resolved path.
    """
    resolved = path if path is not None else _default_profile_path()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Company profile not found at {resolved}. "
            "Create company_profile.yaml at the repo root or set IMAN_COMPANY_PROFILE_PATH."
        )
    data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    return CompanyProfile(
        interest_areas=list(data.get("interest_areas") or []),
        company_fields=list(data.get("company_fields") or []),
        past_tender_categories=list(data.get("past_tender_categories") or []),
        triage_dimensions=_parse_triage_dimensions(data),
        tender_filters=_parse_tender_filters(data),
        action_plan_text=str(data.get("action_plan_text") or "").strip(),
    )
