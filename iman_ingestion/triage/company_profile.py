"""Load and expose the company profile used for tender triage."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


_DEFAULT_STATUSES: frozenset[str] = frozenset({"PRE", "PUB"})


@dataclass(frozen=True)
class TenderFilters:
    contract_folder_statuses: frozenset[str] = field(default_factory=lambda: _DEFAULT_STATUSES)
    contract_type_codes: Optional[frozenset[str]] = None
    contract_subtype_codes: Optional[frozenset[str]] = None
    cpv_filters: Optional[frozenset[str]] = None


@dataclass(frozen=True)
class TriageDimension:
    name: str
    description: str
    weight: float = 1.0


@dataclass(frozen=True)
class CpvProfile:
    """Per-CPV-prefix overrides for triage evaluation."""

    cpv_prefixes: tuple[str, ...]
    interest_areas: Optional[list[str]] = None
    company_fields: Optional[list[str]] = None
    past_tender_categories: Optional[list[str]] = None
    triage_dimensions: Optional[list[TriageDimension]] = None


@dataclass(frozen=True)
class CompanyProfile:
    interest_areas: list[str]
    company_fields: list[str]
    past_tender_categories: list[str]
    triage_dimensions: list[TriageDimension] = field(default_factory=list)
    tender_filters: TenderFilters = field(default_factory=TenderFilters)
    action_plan_text: str = ""
    cpv_profiles: list[CpvProfile] = field(default_factory=list)

    def resolve_for_cpv_codes(self, cpv_codes: list[str]) -> "CompanyProfile":
        """Return a profile with overrides applied for the first matching CpvProfile.

        Matching: the CpvProfile.cpv_prefix is a prefix of any code in cpv_codes.
        Falls back to self if no match or cpv_profiles is empty.
        """
        for cpv_profile in self.cpv_profiles:
            if any(code.startswith(p) for p in cpv_profile.cpv_prefixes for code in cpv_codes):
                return CompanyProfile(
                    interest_areas=cpv_profile.interest_areas if cpv_profile.interest_areas is not None else self.interest_areas,
                    company_fields=cpv_profile.company_fields if cpv_profile.company_fields is not None else self.company_fields,
                    past_tender_categories=cpv_profile.past_tender_categories if cpv_profile.past_tender_categories is not None else self.past_tender_categories,
                    triage_dimensions=cpv_profile.triage_dimensions if cpv_profile.triage_dimensions is not None else self.triage_dimensions,
                    tender_filters=self.tender_filters,
                    action_plan_text=self.action_plan_text,
                    cpv_profiles=self.cpv_profiles,
                )
        return self


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

    type_codes_raw = raw.get("contract_type_codes")
    type_codes: Optional[frozenset[str]] = frozenset(str(c) for c in type_codes_raw) if type_codes_raw else None

    subtypes_raw = raw.get("contract_subtype_codes")
    subtypes: Optional[frozenset[str]] = frozenset(str(s) for s in subtypes_raw) if subtypes_raw else None

    cpv_filters_raw = raw.get("cpv_filters")
    cpv_filters: Optional[frozenset[str]] = frozenset(str(f) for f in cpv_filters_raw) if cpv_filters_raw else None

    return TenderFilters(
        contract_folder_statuses=statuses,
        contract_type_codes=type_codes,
        contract_subtype_codes=subtypes,
        cpv_filters=cpv_filters,
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


def _parse_cpv_profiles(data: dict) -> list[CpvProfile]:
    raw = data.get("cpv_profiles") or []
    profiles: list[CpvProfile] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("cpv_prefixes"):
            prefixes = tuple(str(p) for p in item["cpv_prefixes"])
        elif item.get("cpv_prefix"):
            prefixes = (str(item["cpv_prefix"]),)
        else:
            continue
        dims_raw = item.get("triage_dimensions")
        dims = _parse_triage_dimensions({"triage_dimensions": dims_raw}) if dims_raw is not None else None
        profiles.append(CpvProfile(
            cpv_prefixes=prefixes,
            interest_areas=list(item["interest_areas"]) if item.get("interest_areas") is not None else None,
            company_fields=list(item["company_fields"]) if item.get("company_fields") is not None else None,
            past_tender_categories=list(item["past_tender_categories"]) if item.get("past_tender_categories") is not None else None,
            triage_dimensions=dims,
        ))
    return profiles


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
        cpv_profiles=_parse_cpv_profiles(data),
    )
