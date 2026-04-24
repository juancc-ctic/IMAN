"""Load and expose the company profile used for tender triage."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# All flag keys recognised by the enrichment LLM.
_ALL_FLAG_KEYS: frozenset[str] = frozenset(
    [
        "place_of_execution_not_asturias",
        "execution_period_under_2_months",
        "maintenance_longer_than_1_year",
        "asks_technical_assistance_service",
        "iso_certification_required",
        "ens_certification_required",
        "pmi_certified_profile_required",
        "economic_offer_weight_over_70_points",
    ]
)


@dataclass(frozen=True)
class TriageFilters:
    hard_blockers: frozenset[str]
    soft_flags: frozenset[str]
    soft_flag_penalty: float = 1.5


# Default: all known flags are hard blockers, none are soft.
_DEFAULT_TRIAGE_FILTERS = TriageFilters(
    hard_blockers=_ALL_FLAG_KEYS,
    soft_flags=frozenset(),
    soft_flag_penalty=1.5,
)


@dataclass(frozen=True)
class CompanyProfile:
    interest_areas: list[str]
    company_fields: list[str]
    past_tender_categories: list[str]
    triage_filters: TriageFilters = field(default_factory=lambda: _DEFAULT_TRIAGE_FILTERS)


def _default_profile_path() -> Path:
    env = os.environ.get("IMAN_COMPANY_PROFILE_PATH", "").strip()
    if env:
        return Path(env)
    return Path(__file__).parent.parent.parent / "company_profile.yaml"


def _parse_triage_filters(data: dict) -> TriageFilters:
    tf = data.get("triage_filters") or {}
    hard_raw = tf.get("hard_blockers") or []
    sf_data = tf.get("soft_flags") or {}
    soft_raw = sf_data.get("items") or []
    try:
        penalty = float(sf_data.get("penalty_per_flag") or 1.5)
    except (TypeError, ValueError):
        penalty = 1.5
    return TriageFilters(
        hard_blockers=frozenset(str(f) for f in hard_raw),
        soft_flags=frozenset(str(f) for f in soft_raw),
        soft_flag_penalty=penalty,
    )


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
    triage_filters = (
        _parse_triage_filters(data)
        if "triage_filters" in data
        else _DEFAULT_TRIAGE_FILTERS
    )
    return CompanyProfile(
        interest_areas=list(data.get("interest_areas") or []),
        company_fields=list(data.get("company_fields") or []),
        past_tender_categories=list(data.get("past_tender_categories") or []),
        triage_filters=triage_filters,
    )
