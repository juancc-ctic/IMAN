"""Load and expose the company profile used for tender triage."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


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


def _default_profile_path() -> Path:
    env = os.environ.get("IMAN_COMPANY_PROFILE_PATH", "").strip()
    if env:
        return Path(env)
    return Path(__file__).parent.parent.parent / "company_profile.yaml"


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
    )
