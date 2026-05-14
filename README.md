# IMAN

Repositorio del proyecto **IMAN** para el **GenAI Challenge 2026** de CTIC.

IMAN ingiere licitaciones públicas españolas desde feeds **ATOM** de *Plataformas Agregadas Sin Menores* y oportunidades de financiación europea desde la **EU Funding & Tenders API** (Horizon Europe / CORDIS). Descarga documentación en PDF, persiste metadatos en **PostgreSQL**, enriquece cada registro con un **LLM** (análisis estructurado orientado a un centro tecnológico), genera **embeddings** con **pgvector** para búsqueda semántica, y expone todo a través de una **REST API** (FastAPI). La orquestación del pipeline corre en **Dagster**.

---

## Requisitos previos

- **Python ≥ 3.11**
- **Docker** y **Docker Compose**
- **poppler-utils** en el host o en la imagen (para `pdftoppm` en modo multimodal)
- Acceso a APIs **OpenAI-compatibles** para chat y embeddings

---

## Puesta en marcha (Docker — recomendado)

### 1. Configurar el entorno

```bash
cp env.example .env
```

Edita `.env` y ajusta como mínimo:

| Variable | Descripción |
|---|---|
| `IMAN_ATOM_SOURCE` | URL del feed ATOM de contratación pública |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | Endpoint OpenAI-compatible para chat/análisis |
| `EMBEDDINGS_API_BASE` / `EMBEDDINGS_MODEL` | Endpoint OpenAI-compatible para embeddings |
| `IMAN_EMBEDDING_DIMENSION` | Dimensión del vector (por defecto `1024` para Arctic Embed L v2.0) |

### 2. Arrancar todos los servicios

```bash
docker compose up --build
```

| Servicio | Puerto | Descripción |
|---|---|---|
| `postgres` | 5432 | PostgreSQL 16 + pgvector (bases `dagster` e `iman`) |
| `user_code` | 4000 (interno) | Código Dagster (gRPC) |
| `dagster_webserver` | **3000** | UI de Dagster |
| `dagster_daemon` | — | Ejecución de schedules/sensors |
| `api` | **8000** | REST API (FastAPI) |

Las migraciones de Alembic se ejecutan automáticamente al arrancar `user_code`.

### Datos de ejemplo (opcional)

El directorio `seeds/` contiene una muestra inicial de la base de datos. Al arrancar con `IMAN_APPLY_SEED=1` se cargan:

- `company_profile` — perfil de empresa (1 fila)
- `tenders` — todas las licitaciones ordenadas por `triage_score` desc
- `eu_items` — los 20 items EU con mayor puntuación
- `eu_organizations`, `eu_projects`, `eu_participations` — 100 filas por tabla desde los CSV de `data-sources/Europe/`

Para cargarlos al arrancar:

```bash
IMAN_APPLY_SEED=1 docker compose up --build
```

Deja `IMAN_APPLY_SEED` vacía (o sin definir) en producción — el arranque la omite sin error.

Para cargar las tablas CORDIS completas (sin límite) en cualquier momento:

```bash
docker compose exec user_code load-cordis-data           # completo (~276k participaciones)
docker compose exec user_code load-cordis-data --limit 500  # muestra personalizada
```

Para regenerar el seed desde una base de datos actualizada:

```bash
python seeds/generate_seed.py
```

### 3. Ejecutar el pipeline

- Abre **http://localhost:3000** y materializa el job que necesites, o activa el schedule diario.
- También puedes lanzarlo vía API REST (ver sección API más abajo).

---

## Instalación local (desarrollo)

```bash
pip install -e ".[dev]"

# Migraciones (requiere IMAN_DATABASE_URL apuntando a tu Postgres)
alembic upgrade head

# Tests unitarios (sin servicios externos)
pytest tests/

# Tests con LLM real
IMAN_RUN_LLM_INTEGRATION=1 pytest tests/test_tender_llm_integration.py -v
```

---

## CLI sin Dagster

```bash
download-aggregated-docs \
  https://<atom-feed-url> \
  --output downloads \
  --json-out licitaciones_extraidas.json \
  --cutoff-date 2026-03-01 \
  --try 5

# Carga de datos CORDIS
load-cordis-data

# Recomendador de partners (standalone)
recommend-partners
```

---

## Pipelines Dagster

### `iman_full_pipeline` — Licitaciones españolas

| Asset | Descripción |
|---|---|
| `raw_aggregated_ingestion` | Parsea el feed ATOM, filtra por tipo/estado, descarga PCAP.pdf y PPT.pdf en `downloads/<hash>/` |
| `persist_tenders` | Upsert de metadatos en la tabla `tenders` |
| `tender_llm_enrichment` | Analiza PCAP con el LLM (multimodal o texto) y guarda JSONB en `tenders.enrichment` |
| `document_embeddings` | Trocea PCAP + PPT, genera embeddings y guarda filas en `document_chunks` |

### `eu_full_pipeline` — Financiación europea

Ingesta y enriquecimiento de topics y calls de la EU Funding & Tenders API.

### `cordis_load_pipeline` — Proyectos y organizaciones CORDIS

Carga proyectos EU, organizaciones y participaciones desde datos CORDIS.

**Schedule por defecto:** `0 6 * * *` UTC, desactivada hasta habilitarla en la UI (`IMAN_CRON_SCHEDULE`).

---

## REST API

La API arranca en **http://localhost:8000**. Documentación interactiva en `/docs` (Swagger) y `/redoc`.

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/jobs/{job_name}/run` | Lanza un job de Dagster (`iman_full_pipeline`, `eu_full_pipeline`, `cordis_load_pipeline`) |
| `GET` | `/jobs/runs/{run_id}` | Estado de una ejecución |
| `GET` | `/tenders` | Lista licitaciones (filtros: `min_score`; orden: `sort_by`, `order`) |
| `GET` | `/tenders/{id}` | Detalle de una licitación |
| `PATCH` | `/tenders/{id}` | Actualiza `enrichment`, `summary`, `triage`, `triage_score` |
| `GET` | `/eu-items` | Lista items EU (filtros: `status`, `kind`, `min_score`; orden: `sort_by`, `order`) |
| `GET` | `/eu-items/{reference}` | Detalle de un item EU |
| `PATCH` | `/eu-items/{reference}` | Actualiza `triage`, `triage_score`, `embed_text`, `item_metadata` |
| `POST` | `/eu-items/{reference}/partner-recommendations` | Recomendaciones de partners por similitud semántica |
| `GET` | `/eu-organizations` | Lista organizaciones (filtro: `country`) |
| `GET` | `/eu-organizations/{id}` | Detalle de una organización |
| `PATCH` | `/eu-organizations/{id}` | Actualiza `interest`, `why` |
| `GET` | `/eu-projects` | Lista proyectos CORDIS |
| `GET` | `/eu-projects/{id}` | Detalle de un proyecto |
| `GET` | `/company-profile` | Perfil de empresa (singleton) |
| `PUT` | `/company-profile` | Crea o actualiza el perfil de empresa |

### Parámetros de ordenación (`/tenders` y `/eu-items`)

Ambos endpoints aceptan `sort_by` y `order=asc|desc` (por defecto `triage_score desc`).

Campos válidos para **`/tenders`**: `triage_score`, `created_at`, `updated_at`, `title`, `party_name`, `submission_deadline`

Campos válidos para **`/eu-items`**: `triage_score`, `created_at`, `updated_at`, `title`, `deadline_date`, `start_date`

```bash
# Licitaciones ordenadas por título
curl "http://localhost:8000/tenders?sort_by=title&order=asc&limit=20"

# Items EU con mayor puntuación, estado abierto
curl "http://localhost:8000/eu-items?status=open&min_score=0.7"

# Lanzar pipeline
curl -X POST http://localhost:8000/jobs/iman_full_pipeline/run
```

---

## Variables de entorno relevantes

| Variable | Descripción |
|---|---|
| `IMAN_ATOM_SOURCE` | **Requerida.** URL del feed ATOM agregado |
| `IMAN_DATABASE_URL` | Cadena de conexión PostgreSQL |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | Endpoint de chat (OpenAI-compatible) |
| `EMBEDDINGS_API_BASE` / `EMBEDDINGS_MODEL` | Endpoint de embeddings |
| `IMAN_EMBEDDING_DIMENSION` | Dimensión del vector (por defecto `1024`) |
| `IMAN_USE_MULTIMODAL_LLM` | Modo PDF→PNG multimodal (por defecto `true`) |
| `IMAN_SKIP_LLM_ENRICHMENT` | Omite el asset de enriquecimiento LLM |
| `IMAN_SKIP_EMBEDDINGS` | Omite el asset de embeddings |
| `IMAN_SKIP_TRIAGE` | Omite el triage automático |
| `IMAN_APPLY_SEED` | Carga `seeds/seed.sql` al arrancar (desarrollo; vacía en producción) |
| `IMAN_CUTOFF_DATE` | Detiene la paginación del feed antes de esta fecha (YYYY-MM-DD) |
| `IMAN_MAX_TRIES` | Máximo de intentos de descarga de PDF por ejecución |
| `DAGSTER_WEBSERVER_URL` | URL del webserver Dagster para la API REST (por defecto `http://dagster_webserver:3000`) |

Consulta `env.example` para la lista completa con valores de ejemplo y variables opcionales de multimodal, EU Search API y triage.

---

## Arquitectura del paquete

```
iman_ingestion/
├── aggregated/       # Ingesta feed ATOM y descarga de PDFs
├── api/              # REST API (FastAPI)
│   ├── app.py
│   ├── tenders.py, eu_items.py, eu_orgs.py, eu_projects.py, jobs.py, profile.py
│   └── schemas.py
├── assets/           # Assets Dagster (pipeline.py, eu_pipeline.py)
├── db/               # Modelos SQLAlchemy y session_scope
├── eu/               # Cliente EU Funding API y carga CORDIS
├── llm/              # Cliente LLM, prompt, validación de campos, PDF→imágenes
├── triage/           # Lógica de puntuación y cribado automático
├── partner_recommender.py
└── definitions.py    # Punto de entrada Dagster
alembic/              # Migraciones de base de datos
docker/               # Entrypoints, dagster_home, init de Postgres
tests/                # Pruebas unitarias e integración
```

---

*Proyecto IMAN — CTIC — GenAI Challenge 2026.*
