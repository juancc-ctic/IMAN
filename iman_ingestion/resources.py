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
        description="ATOM URL or path; falls back to IMAN_ATOM_SOURCE.",
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
        atom = self.atom_source or os.environ.get("IMAN_ATOM_SOURCE", "")
        if not atom:
            raise ValueError("Set atom_source on the resource or IMAN_ATOM_SOURCE")

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
            # Default to the start of today (UTC) so the pipeline always ingests
            # only current-day entries and paginates through all of today's pages.
            now = datetime.now(tz=timezone.utc)
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)

        return IngestionConfig(
            atom_source=atom,
            output_dir=raw / "downloads",
            json_out=raw / json_name,
            cutoff_utc=cutoff,
            max_tries=max_tries,
            no_download=False,
        )
