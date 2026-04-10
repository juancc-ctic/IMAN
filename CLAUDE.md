# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**IMAN** is a data ingestion and AI pipeline for analyzing Spanish public procurement (contratos públicos). It ingests ATOM feeds from the Plataformas Agregadas Sin Menores portal, downloads PDFs, persists tender metadata in PostgreSQL with pgvector, enriches them via LLM analysis, and generates embeddings for RAG. Orchestration is handled by Dagster.

## Commands

### Local Development

```bash
# Install package in editable mode with dev extras
pip install -e ".[dev]"

# Run database migrations (requires PostgreSQL)
alembic upgrade head

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_tender_fields.py -v

# Run integration tests that hit a live LLM API
IMAN_RUN_LLM_INTEGRATION=1 pytest tests/test_tender_llm_integration.py -v
```

### Docker (Recommended for Full Stack)

```bash
# Copy and configure environment
cp env.example .env
# Edit .env: set IMAN_ATOM_SOURCE, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, etc.

# Build and start all services
docker compose up --build

# Dagster UI is at http://localhost:3000
```

### Standalone CLI (No Dagster)

```bash
download-aggregated-docs \
  https://<atom-feed-url> \
  --output downloads \
  --json-out licitaciones_extraidas.json \
  --cutoff-date 2026-03-01 \
  --try 5
```

## Architecture

### Dagster Pipeline (`iman_ingestion/assets/pipeline.py`)

Four assets run sequentially as the `iman_full_pipeline` job, scheduled daily at 06:00 UTC (disabled by default via `IMAN_CRON_SCHEDULE`):

1. **`raw_aggregated_ingestion`** — Parses ATOM feed pages, applies contract type/status filters, downloads PCAP.pdf and PPT.pdf per tender into `downloads/<tender_hash>/`. Outputs `licitaciones_extraidas.json`.

2. **`persist_tenders`** — Upserts tender metadata from the JSON into the `tenders` PostgreSQL table.

3. **`tender_llm_enrichment`** — For each tender, either rasterizes the PCAP.pdf into base64 PNGs (multimodal mode) or extracts text, then calls the LLM to produce structured JSONB analysis stored in `tenders.enrichment`. Skippable via `IMAN_SKIP_LLM_ENRICHMENT=1`.

4. **`document_embeddings`** — Chunks text from PCAP.pdf + PPT.pdf, calls the embeddings API, and stores `DocumentChunk` rows with pgvector vectors. Skippable via `IMAN_SKIP_EMBEDDINGS=1`.

### LLM Layer (`iman_ingestion/llm/`)

- **`client.py`** — `get_llm_client()` and `get_embeddings_client()` return OpenAI-SDK clients pointed at configurable base URLs. `analyze_tender_proposal()` orchestrates multimodal vs. text-only calls, batches large image sets, and returns a validated JSONB payload.
- **`tender_analysis.py`** — System prompt (technology center analyst role) and JSON schema defining the structured output fields: contract scope, packages, solvency requirements, required profiles, assessment criteria, outsourcing, and discard-review flags.
- **`tender_fields.py`** — Validates required fields are present, detects missing ones with human-readable labels, and merges partial results from batched calls (`first_wins` vs. `batch_overwrites` modes).
- **`pdf_to_images.py`** — Wraps `pdftoppm` (system dep) to rasterize PDF pages to base64 PNGs.

### Database (`iman_ingestion/db/`)

- **`models.py`** — Two ORM models: `Tender` (id = ATOM URI, enrichment JSONB, timestamps) and `DocumentChunk` (FK to Tender, source_kind, chunk_index, text, pgvector embedding).
- **`session.py`** — `session_scope()` context manager for transactional sessions; engine cached from `IMAN_DATABASE_URL`.
- Migrations live in `alembic/versions/`; run automatically on container startup via `docker/entrypoint-user-code.sh`.

### Feed Ingestion (`iman_ingestion/aggregated/ingestion.py`)

Parses paginated ATOM feeds (XML with Atom/CBC/CAC namespaces), follows `rel="next"` links until the cutoff date, applies status (`PRE/PUB/EV`) and type filters (`TypeCode=2`, specific `SubTypeCode` values), and downloads PDFs with retry logic.

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `IMAN_ATOM_SOURCE` | **Required.** URL to the aggregated ATOM feed |
| `IMAN_DATABASE_URL` | PostgreSQL connection string |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | OpenAI-compatible chat endpoint |
| `EMBEDDINGS_API_BASE` / `EMBEDDINGS_MODEL` | Embeddings endpoint |
| `IMAN_EMBEDDING_DIMENSION` | Vector dimension (default: 1024 for Arctic Embed L v2.0) |
| `IMAN_USE_MULTIMODAL_LLM` | PDF→PNG multimodal mode (default: true) |
| `IMAN_SKIP_LLM_ENRICHMENT` | Skip asset 3 (useful for smoke tests) |
| `IMAN_SKIP_EMBEDDINGS` | Skip asset 4 |
| `IMAN_CUTOFF_DATE` | Stop feed pagination at this date |

## Infrastructure

- **Docker services:** `postgres` (pgvector/pgvector:pg16 with two DBs: `dagster` + `iman`), `user_code` (gRPC on 4000), `dagster_webserver` (UI on 3000), `dagster_daemon`.
- **Dagster state** stored in the `dagster` PostgreSQL database (configured in `docker/dagster_home/dagster.yaml`).
- The `poppler-utils` system package is required for `pdftoppm` (PDF→image conversion in multimodal mode).

## Testing Notes

- Tests marked `llm_integration` require `IMAN_RUN_LLM_INTEGRATION=1` and a live LLM endpoint.
- Set `IMAN_LLM_INTEGRATION_SKIP_ON_ERROR=1` to skip rather than fail when the API is unavailable.
- Unit tests in `test_tender_fields.py` require no external services.

## Legacy Code

The `iman/`, `iman_dagster/`, and `iman_pipelines/` directories contain earlier iterations and are not part of the active pipeline. The active package is `iman_ingestion/`.
