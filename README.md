# IMAN

Repositorio del proyecto **IMAN** para el **GenAI Challenge 2026** de CTIC.

IMAN ingiere licitaciones públicas españolas desde feeds **ATOM** de *Plataformas Agregadas Sin Menores* y oportunidades de financiación europea desde la **EU Funding & Tenders API** (Horizon Europe / CORDIS). Descarga documentación en PDF, persiste metadatos en **PostgreSQL**, enriquece cada registro con un **LLM** (análisis estructurado orientado a cCTIC), genera **embeddings** con **pgvector** para búsqueda semántica, y expone todo a través de una **REST API** (FastAPI). La orquestación del pipeline corre en **Dagster**.

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

| Servicio | Puerto (host) | Descripción |
|---|---|---|
| `postgres` | `POSTGRES_PORT` (defecto **5432**) | PostgreSQL 16 + pgvector (bases `dagster` e `iman`) |
| `user_code` | 4000 (interno) | Código Dagster (gRPC) |
| `dagster_webserver` | `DAGSTER_PORT` (defecto **3000**) | UI de Dagster |
| `dagster_daemon` | — | Ejecución de schedules/sensors |
| `api` | `API_PORT` (defecto **8000**) | REST API (FastAPI) |

Los puertos del host son configurables mediante variables de entorno (en `.env` o en la línea de comandos):

```bash
DAGSTER_PORT=3001 API_PORT=8001 docker compose up
```

Las migraciones de Alembic se ejecutan automáticamente al arrancar `user_code`.

### Datos de ejemplo (opcional)

El directorio `seeds/` contiene una muestra inicial de la base de datos. Al arrancar con `IMAN_APPLY_SEED=1` se cargan:

- `company_profile` — perfil de empresa (1 fila)
- `tenders` — todas las licitaciones ordenadas por `triage_score` desc
- `eu_items` — los 20 items EU con mayor puntuación
- `eu_organizations`, `eu_projects`, `eu_participations` — todas las filas desde los CSV de `data-sources/Europe/` - Este proceso tarda unos minutos

Para cargarlos al arrancar:

```bash
IMAN_APPLY_SEED=1 docker compose up --build
```

Deja `IMAN_APPLY_SEED` vacía (o sin definir) en producción — el arranque la omite sin error.

Para regenerar el seed desde una base de datos actualizada:

```bash
python seeds/generate_seed.py
```

### 3. Ejecutar el pipeline

- Abre **http://localhost:3000** y materializa el job que necesites:
- iman_full_pipeline: Licitaciones españolas diarias
- eu_full_pipeline: Topics y Open calls europeas abiertas actualmente
- También se puede lanzar vía API REST (ver sección API más abajo).

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

**Schedule por defecto:** `0 6 * * *` UTC, desactivada hasta habilitarla en la UI (`IMAN_CRON_SCHEDULE`).

### `iman_full_pipeline` — Licitaciones españolas

Procesa las licitaciones públicas españolas desde el feed ATOM hasta su puntuación final. Los assets se ejecutan en secuencia:

1. **`raw_aggregated_ingestion`** — Recorre las páginas del feed ATOM siguiendo los enlaces `rel="next"` hasta la fecha de corte (`IMAN_CUTOFF_DATE`). Filtra por estado (`PRE`, `PUB`, `EV`) y tipo de contrato (código 2 con subcódigos específicos). Por cada licitación que pasa el filtro descarga `PCAP.pdf` (pliego de cláusulas administrativas) y `PPT.pdf` (prescripciones técnicas) en `downloads/<hash>/`. El resultado es un fichero JSON con los metadatos de todas las licitaciones descargadas.

2. **`persist_tenders`** — Lee el JSON anterior y hace un upsert de cada licitación en la tabla `tenders` (id, título, organismo, importes, URLs de PDF, plazo de presentación).

3. **`tender_llm_enrichment`** — Para cada licitación, si `IMAN_USE_MULTIMODAL_LLM=true` rasteriza el `PCAP.pdf` a imágenes PNG (vía `pdftoppm`) y las envía al LLM en bloques; si no, extrae el texto del PDF. El LLM devuelve un JSONB estructurado con: alcance del contrato, lotes, requisitos de solvencia, perfiles requeridos, criterios de valoración, posibilidad de subcontratación y flags de descarte. Este resultado se guarda en `tenders.enrichment`.

4. **`tender_embeddings`** — Incrusta el resumen generado por el LLM (`enrichment.summary`) y guarda el vector en `tenders.summary_embedding` para búsqueda semántica posterior.

5. **`tender_triage`** — Evalúa cada licitación enriquecida frente al perfil de empresa (`company_profile.yaml`). Combina similitud semántica entre el embedding de la licitación y el de la empresa con una puntuación LLM multi-dimensión (interés estratégico, adecuación técnica, viabilidad, etc.). El resultado se guarda en `tenders.triage` (JSONB con desglose) y `tenders.triage_score` (0–5).

### `eu_full_pipeline` — Financiación europea

Procesa topics y open calls de la EU Funding & Tenders API:

1. **`raw_eu_ingestion`** — Consulta la EU Search API y recupera todos los topics y calls activos que coincidan con el texto de búsqueda configurado (`EU_SEARCH_TEXT`). Devuelve una lista normalizada de items con su referencia, título, estado, fechas y texto para embedding.

2. **`persist_eu_items`** — Upsert de cada item en la tabla `eu_items` (referencia, tipo, URL, programa marco, fechas de inicio y cierre).

3. **`eu_item_embeddings`** — Genera un embedding por item a partir de su descripción (`embed_text`, truncada a 16 000 caracteres) y lo guarda en `eu_items.embedding`.

4. **`eu_item_triage`** — Para cada item activo con embedding, evalúa su relevancia frente al perfil de empresa combinando similitud vectorial y puntuación LLM. Almacena el resultado en `eu_items.triage` y `eu_items.triage_score`.

### `cordis_load_pipeline` — Proyectos y organizaciones CORDIS

Carga los datos de referencia de proyectos europeos financiados (Horizon Europe y anteriores):

1. **`load_cordis_data`** — Lee los CSV de `data-sources/Europe/` (`organizations.csv`, `projects.csv`, `relations.csv`) y hace upsert en las tablas `eu_organizations`, `eu_projects` y `eu_participations`. Idempotente.

2. **`eu_project_embeddings`** — Incrusta el título y palabras clave de cada proyecto y guarda el vector en `eu_projects.embedding`, habilitando recomendaciones de partners por similitud semántica.

---

## Recomendación de partners

El sistema de recomendación de partners sugiere organizaciones europeas con las que colaborar en una convocatoria EU, basándose en su historial de participación en proyectos CORDIS similares.

### Cómo funciona

Dado el embedding de un EU item (topic o call), el algoritmo ejecuta tres pasos:

1. **Búsqueda por similitud vectorial (ANN)** — Recupera los `top_k` proyectos CORDIS más similares a la convocatoria usando el índice HNSW de pgvector (`eu_projects.embedding <=> target`).

2. **Agregación por organización** — Para cada organización que participó en esos proyectos, acumula las puntuaciones de similitud de los proyectos en los que apareció y su rol (coordinador o participante).

3. **Puntuación final** — Combina tres factores:
   - **Dominio técnico** (`s_exp`): suma de similitudes al cuadrado, premiando organizaciones presentes en varios proyectos afines.
   - **Afinidad de rol** (`m_role`): multiplicador de hasta ×1.2 si se busca coordinador y la organización tiene historial como tal.
   - **Confianza interna** (`m_int`): multiplicador basado en la nota de interés de la organización en `eu_organizations.interest` (escala 0–5). Organizaciones sin nota reciben factor neutro (×1.0).

El resultado final es `score = s_exp × m_role × m_int`, y se devuelven los `top_n` más altos con una explicación desglosada en los tres factores.

### Prerrequisitos

- Datos CORDIS cargados (`cordis_load_pipeline` o `load-cordis-data`).
- Embeddings de proyectos generados (`eu_project_embeddings`).
- Embedding del EU item generado (`eu_item_embeddings`).

### Uso vía API REST

```bash
# Recomendaciones para una convocatoria (defaults: top_k=50, top_n=5)
curl -X POST http://localhost:8000/eu-items/HORIZON-CL4-2026-DATA-01-01/partner-recommendations \
  -H "Content-Type: application/json" \
  -d '{"coordinator": true, "top_k": 50, "top_n": 5}'
```

Respuesta (ejemplo):
```json
[
  {
    "organisationID": "999507401",
    "name": "FUNDACION CTIC CENTRO TECNOLOGICO",
    "score": 1.47,
    "explicacion": {
      "1_dominio_tecnico": "3 proyectos afines encontrados (Similitud media: 0.83).",
      "2_afinidad_rol": "67% de veces como coordinador.",
      "3_confianza": "Nota interna de confianza: 4.5/5."
    }
  }
]
```

### Uso vía CLI

```bash
# Requiere IMAN_DATABASE_URL configurada
recommend-partners HORIZON-CL4-2026-DATA-01-01
recommend-partners HORIZON-CL4-2026-DATA-01-01 --coordinator --top-k 100 --top-n 10
```

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
| `DAGSTER_PORT` | Puerto del host para el webserver Dagster (por defecto `3000`) |
| `API_PORT` | Puerto del host para la REST API (por defecto `8000`) |
| `POSTGRES_PORT` | Puerto del host para PostgreSQL (por defecto `5432`) |

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
