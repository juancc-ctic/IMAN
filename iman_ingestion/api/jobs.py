"""Endpoints to launch and inspect Dagster jobs via the GraphQL API."""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from iman_ingestion.api.schemas import RunLaunchResponse, RunStatusResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

VALID_JOBS = {"iman_full_pipeline", "eu_full_pipeline", "cordis_load_pipeline"}

_LAUNCH_MUTATION = """
mutation LaunchRun($jobName: String!) {
  launchRun(executionParams: {
    selector: {
      repositoryLocationName: "iman_user_code"
      repositoryName: "__repository__"
      jobName: $jobName
    }
    runConfigData: {}
  }) {
    __typename
    ... on LaunchRunSuccess {
      run { runId status }
    }
    ... on RunConfigValidationInvalid {
      errors { message }
    }
    ... on InvalidSubsetError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""

_RUN_QUERY = """
query RunStatus($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run {
      runId
      status
    }
    ... on RunNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""


def _webserver_url() -> str:
    return os.environ.get("DAGSTER_WEBSERVER_URL", "http://dagster_webserver:3000")


async def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    url = f"{_webserver_url()}/graphql"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json={"query": query, "variables": variables})
    resp.raise_for_status()
    return resp.json()


@router.post("/{job_name}/run", response_model=RunLaunchResponse)
async def launch_job(job_name: str) -> RunLaunchResponse:
    if job_name not in VALID_JOBS:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_name}'. Valid jobs: {sorted(VALID_JOBS)}")

    try:
        data = await _graphql(_LAUNCH_MUTATION, {"jobName": job_name})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Dagster webserver unreachable: {exc}") from exc

    result = data.get("data", {}).get("launchRun", {})
    typename = result.get("__typename")

    if typename == "LaunchRunSuccess":
        run = result["run"]
        return RunLaunchResponse(run_id=run["runId"], status=run["status"])

    message = result.get("message") or str(result.get("errors", result))
    raise HTTPException(status_code=400, detail=f"Dagster error ({typename}): {message}")


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str) -> RunStatusResponse:
    try:
        data = await _graphql(_RUN_QUERY, {"runId": run_id})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Dagster webserver unreachable: {exc}") from exc

    result = data.get("data", {}).get("runOrError", {})
    typename = result.get("__typename")

    if typename == "Run":
        return RunStatusResponse(run_id=result["runId"], status=result["status"])

    message = result.get("message", "Unknown error")
    raise HTTPException(status_code=404, detail=f"Run not found ({typename}): {message}")
