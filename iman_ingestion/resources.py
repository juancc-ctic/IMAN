"""Dagster resources for ingestion paths and configuration."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dagster import ConfigurableResource
from pydantic import Field

from iman_ingestion.aggregated.ingestion import IngestionConfig


class ImanIngestionResource(ConfigurableResource):
    """Paths and feed parameters (defaults from environment in Docker)."""

    atom_source: str = Field(
        default="",
        description="Single ATOM URL or path; falls back to IMAN_ATOM_SOURCE.",
    )
    atom_sources: str = Field(
        default="",
        description="Comma-separated ATOM URLs; falls back to IMAN_ATOM_SOURCES.",
    )
    data_dir: str = Field(
        default="/data",
        description="Base directory for raw JSON and PDFs (IMAN_DATA_DIR).",
    )
    json_filename: str = Field(
        default="licitaciones_extraidas.json",
        description="Filename under raw/ for tender JSON.",
    )
    cutoff_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD or ISO datetime; IMAN_CUTOFF_DATE.",
    )
    max_tries: int = Field(
        default=0,
        description="Max PDF download attempts (0 = all). IMAN_MAX_TRIES.",
    )

    def raw_dir(self) -> Path:
        return Path(self.data_dir) / "raw"

    def to_ingestion_config(self) -> IngestionConfig:
        """Build :class:`IngestionConfig` using env fallbacks."""
        def _split(s: str) -> list:
            return [u.strip() for u in s.replace("\n", ",").split(",") if u.strip()]

        sources: list = (
            _split(self.atom_sources)
            or _split(os.environ.get("IMAN_ATOM_SOURCES", ""))
        )
        single = self.atom_source or os.environ.get("IMAN_ATOM_SOURCE", "")
        if single and single not in sources:
            sources.append(single)
        if not sources:
            raise ValueError(
                "Set atom_sources (comma-separated) or atom_source on the resource, "
                "or IMAN_ATOM_SOURCES / IMAN_ATOM_SOURCE env vars"
            )

        data_dir = os.environ.get("IMAN_DATA_DIR", self.data_dir)
        raw = Path(data_dir) / "raw"
        json_name = os.environ.get("IMAN_JSON_FILENAME", self.json_filename)
        cutoff_raw = os.environ.get("IMAN_CUTOFF_DATE", self.cutoff_date or "")
        max_tries = int(os.environ.get("IMAN_MAX_TRIES", str(self.max_tries)))

        if cutoff_raw:
            from iman_ingestion.aggregated.ingestion import parse_cutoff_datetime

            cu = parse_cutoff_datetime(cutoff_raw)
            if cu.tzinfo is None:
                cu = cu.replace(tzinfo=timezone.utc)
            else:
                cu = cu.astimezone(timezone.utc)
            cutoff = cu
        else:
            from datetime import timedelta
            now = datetime.now(tz=timezone.utc)
            cutoff = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        try:
            from iman_ingestion.triage.company_profile import load_company_profile
            tf = load_company_profile().tender_filters
            filter_kwargs = dict(
                allowed_statuses=tf.contract_folder_statuses,
                allowed_type_codes=tf.contract_type_codes,
                allowed_subtype_codes=tf.contract_subtype_codes,
                cpv_filters=tf.cpv_filters,
            )
        except Exception:
            filter_kwargs = {}

        return IngestionConfig(
            atom_sources=sources,
            output_dir=raw / "downloads",
            json_out=raw / json_name,
            cutoff_utc=cutoff,
            max_tries=max_tries,
            no_download=False,
            **filter_kwargs,
        )
