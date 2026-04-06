# IMAN

Repositorio del proyecto **IMAN** para el **GenAI Challenge 2026** de CTIC.

IMAN ingiere licitaciones públicas españolas desde feeds **ATOM** de *Plataformas Agregadas Sin Menores*, descarga la documentación (PDF), persiste metadatos en **PostgreSQL**, enriquece cada licitación con un **LLM** (análisis estructurado orientado a un centro tecnológico) y genera **embeddings** almacenados con **pgvector** para búsqueda semántica. La orquestación del pipeline corre en **Dagster** (assets, job único y programación diaria opcional).

---

## Características principales

### Ingesta ATOM y descargas

- **Feed ATOM**: lectura de URLs o ficheros `.atom` locales, con cabeceras y timeouts acordes al portal de contratación pública.
- **Paginación**: sigue enlaces `rel="next"` entre páginas del feed hasta un **corte temporal** opcional (`IMAN_CUTOFF_DATE` / `--cutoff-date`), comparando `<updated>` del feed en UTC.
- **Filtrado de entradas**: solo ciertos estados de carpeta de contrato y códigos de tipo/subtipo admitidos (servicios relevantes para el caso de uso).
- **Descarga de PDFs** por licitación (p. ej. **PCAP** y **PPT**) en carpetas derivadas del identificador de la licitación.
- **Salida JSON** con la lista de licitaciones extraídas (`licitaciones_extraidas.json` por defecto), lista para persistencia y para el resto del pipeline.

### CLI sin Dagster

- Comando instalado: **`download-aggregated-docs`** (`iman_ingestion.aggregated.cli`).
- Wrapper en raíz: **`download_aggregated_docs.py`**.
- Opciones: fuente ATOM, directorio de salida, fichero JSON, `--no-download`, `--try` / límite de intentos, fecha de corte.

### Pipeline Dagster (`iman_ingestion.definitions`)

| Asset | Descripción |
|--------|-------------|
| **`raw_aggregated_ingestion`** | Ejecuta la ingesta completa (feed + PDFs + JSON) usando `ImanIngestionResource`. |
| **`persist_tenders`** | Lee el JSON y hace *upsert* en la tabla **`tenders`** (id, enlace, título, órgano, importes). |
| **`tender_llm_enrichment`** | Rellena **`tenders.enrichment`** (JSONB) analizando la **PCAP** con el LLM. |
| **`document_embeddings`** | Extrae texto de **PCAP** y **PPT**, trocea, llama al API de embeddings y guarda vectores en **`document_chunks`**. |

- **Job** `iman_full_pipeline`: selección de todos los assets del grupo `iman`.
- **Schedule** `daily_ingestion`: cron configurable (`IMAN_CRON_SCHEDULE`, por defecto `0 6 * * *`), **desactivada por defecto** hasta activarla en la UI de Dagster.

### Base de datos y migraciones

- **SQLAlchemy** + **Alembic**; al arrancar el contenedor de *user code*, si existe `IMAN_DATABASE_URL`, se ejecuta `alembic upgrade head`.
- **Modelos**:
  - **`Tender`**: metadatos de la licitación + `enrichment` (JSON).
  - **`DocumentChunk`**: texto por trozo, tipo de fuente (`metadata` / `pdf`), nombre de fichero, índice y **vector** (`pgvector`), FK a `tenders`.

### LLM: análisis de licitaciones

- Cliente **compatible con OpenAI** (`/v1/chat/completions`): `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (y alias legacy en `env.example`).
- **Modo multimodal** (por defecto): rasteriza páginas de **PCAP.pdf** a PNG (base64) vía **poppler** (`pdftoppm`), con límites por páginas totales, DPI, imágenes por petición y máximo de imágenes (variables `IMAN_MULTIMODAL_*`).
- **Respaldo en texto** si no hay imágenes o multimodal desactivado (`IMAN_USE_MULTIMODAL_LLM`), con techo de caracteres (`IMAN_LLM_MAX_PDF_CHARS`).
- Respuesta exigida en **JSON** con campos como objeto del contrato, alcance, paquetes, solvencia económica, perfiles, criterios de valoración, subcontratación y un bloque **`discard_review`** con criterios de cribado (p. ej. ejecución fuera de Asturias, plazos, mantenimiento, asistencia técnica, certificaciones ISO/ENS/PMI, peso de la oferta económica).
- **Batches** y fusión de resultados cuando hay muchas imágenes (`tender_fields`).

### Embeddings y RAG

- Cliente de **embeddings** compatible con OpenAI (`/v1/embeddings`): `EMBEDDINGS_API_BASE`, `EMBEDDINGS_MODEL`.
- Troceado de metadatos y PDFs con solapamiento configurable en código (`chunk_text`).
- Dimensión del vector alineada con el modelo (`IMAN_EMBEDDING_DIMENSION`, p. ej. 1024 para Arctic Embed L v2.0).
- **Saltos opcionales**: `IMAN_SKIP_LLM_ENRICHMENT`, `IMAN_SKIP_EMBEDDINGS` para pruebas rápidas.

### Infraestructura Docker

- **`postgres`**: imagen **pgvector** (PostgreSQL 16), init scripts en `docker/postgres/init` (bases y extensión vector).
- **`user_code`**: imagen de la app, **gRPC** Dagster en el puerto **4000**, `DAGSTER_HOME`, volumen de datos `/data`.
- **`dagster_webserver`**: UI en el puerto **3000**.
- **`dagster_daemon`**: ejecución de schedules/sensors.

Variables relevantes: `IMAN_DATABASE_URL`, `IMAN_DATA_DIR`, `IMAN_ATOM_SOURCE`, APIs de LLM/embeddings y toggles anteriores (ver **`env.example`**).

### Fuentes de datos UE (utilidad aparte)

En **`data-sources/Europe/`**:

- **`fetch_eu_search.py`**: consultas paginadas a la **EU Funding & Tenders Search API** (topics / calls), alineado con la colección Postman incluida.
- No forma parte del pipeline Dagster principal; es un script autónomo para otros flujos de datos.

### Referencia y pruebas

- **`ai-controller-example.js`**: ejemplo de referencia (patrones de integración con IA / PDF); no es el runtime del pipeline Python.
- **Tests** (`pytest`): campos del esquema LLM, integración opcional con API real (`IMAN_RUN_LLM_INTEGRATION=1`, marcador `llm_integration`).

---

## Requisitos

- **Python ≥ 3.11**
- **Docker** y **Docker Compose** para el entorno recomendado
- Acceso a APIs **OpenAI-compatibles** para chat y embeddings (o equivalentes en tu red)

## Puesta en marcha (Docker)

1. Copia **`env.example`** a **`.env`** y configura al menos `IMAN_ATOM_SOURCE` y las URLs/claves de LLM y embeddings si las usas.
2. Desde la raíz del repo:

   ```bash
   docker compose up --build
   ```

3. Abre la UI de Dagster en **http://localhost:3000** y materializa el job **`iman_full_pipeline`** o activa el schedule según necesites.

## Instalación local (desarrollo)

```bash
pip install -e ".[dev]"
```

Migraciones (con `IMAN_DATABASE_URL` apuntando a tu Postgres):

```bash
alembic upgrade head
```

## Estructura del paquete principal

- **`iman_ingestion/`** — definiciones Dagster, assets, ingesta agregada, cliente LLM/embeddings, modelos y sesión DB, extracción PDF.
- **`alembic/`** — migraciones.
- **`docker/`** — entrada del contenedor, `dagster_home`, init de Postgres.
- **`tests/`** — pruebas unitarias / integración.

---

*Proyecto IMAN — CTIC — GenAI Challenge 2026.*
