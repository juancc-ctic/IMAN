"""FastAPI application for the IMAN REST API."""

from __future__ import annotations

from fastapi import FastAPI

from iman_ingestion.api import eu_items, eu_orgs, eu_projects, jobs, profile, tenders

app = FastAPI(
    title="IMAN API",
    description="REST interface for the IMAN procurement and EU funding pipeline",
    version="0.1.0",
)

app.include_router(jobs.router)
app.include_router(tenders.router)
app.include_router(eu_items.router)
app.include_router(eu_orgs.router)
app.include_router(eu_projects.router)
app.include_router(profile.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
