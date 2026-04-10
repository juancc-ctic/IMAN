"""Dagster :class:`~dagster.Definitions` for IMAN ingestion (loaded by ``dagster dev`` / gRPC)."""

from __future__ import annotations

import os

from dagster import (
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
)

from iman_ingestion.assets.eu_pipeline import (
    eu_item_embeddings,
    persist_eu_items,
    raw_eu_ingestion,
)
from iman_ingestion.assets.pipeline import (
    document_embeddings,
    persist_tenders,
    raw_aggregated_ingestion,
    tender_llm_enrichment,
)
from iman_ingestion.resources import ImanIngestionResource


def _iman_ingestion_resource() -> ImanIngestionResource:
    """Build resource config from env so Dagster UI matches Docker / shell (gRPC host).

    :class:`ImanIngestionResource` defaults are empty; :meth:`to_ingestion_config`
    already falls back to ``os.environ``, but the UI only shows configured fields.
    """
    cutoff_raw = (os.environ.get("IMAN_CUTOFF_DATE") or "").strip()
    cutoff_date = cutoff_raw or None
    try:
        max_tries = int(os.environ.get("IMAN_MAX_TRIES", "0"))
    except ValueError:
        max_tries = 0
    return ImanIngestionResource(
        atom_source=os.environ.get("IMAN_ATOM_SOURCE", ""),
        data_dir=os.environ.get("IMAN_DATA_DIR", "/data"),
        json_filename=os.environ.get(
            "IMAN_JSON_FILENAME",
            "licitaciones_extraidas.json",
        ),
        cutoff_date=cutoff_date,
        max_tries=max_tries,
    )


iman_full_job = define_asset_job(
    "iman_full_pipeline",
    selection=AssetSelection.groups("iman"),
)

eu_full_job = define_asset_job(
    "eu_full_pipeline",
    selection=AssetSelection.groups("eu"),
)

_cron = os.environ.get("IMAN_CRON_SCHEDULE", "0 6 * * *")

defs = Definitions(
    assets=[
        raw_aggregated_ingestion,
        persist_tenders,
        tender_llm_enrichment,
        document_embeddings,
        raw_eu_ingestion,
        persist_eu_items,
        eu_item_embeddings,
    ],
    resources={
        "iman_ingestion": _iman_ingestion_resource(),
    },
    jobs=[iman_full_job, eu_full_job],
    schedules=[
        ScheduleDefinition(
            name="daily_ingestion",
            cron_schedule=_cron,
            job=iman_full_job,
            default_status=DefaultScheduleStatus.STOPPED,
        ),
    ],
)
