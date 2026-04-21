"""Load and expose the company profile used for tender triage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CompanyProfile:
    interest_areas: list[str]
    company_fields: list[str]
    past_tender_categories: list[str]


def _default_profile_path() -> Path:
    env = os.environ.get("IMAN_COMPANY_PROFILE_PATH", "").strip()
    if env:
        return Path(env)
    # repo root: iman_ingestion/triage/ → ../../..
    return Path(__file__).parent.parent.parent / "company_profile.yaml"


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
    )
