---

## Phase 0: Environment Verification

- [x] **Task 0.1: Verify Root Workspace Environment and Existing Dependencies**
  - **Action:** Sync all workspace members defined in `pyproject.toml` (`services/person-api`, `services/aiops-sentinel`, `apps/openwindow`).
  - **Verification Command:** `uv sync --all-packages && uv run pytest --version`

---

## Phase 1: Existing Person API — OpenTelemetry Instrumentation & Trace Verification

- [x] **Task 1.1: Verify Existing Person API Test Suite & DB Setup**
  - **Action:** Run existing tests for `person-api` to establish a clean baseline under `uv`.
  - **Positive (+v) Test:** Existing test suite executes and passes.
  - **Verification Command:** `uv run pytest services/person-api/ -v`

- [x] **Task 1.2: Add OpenTelemetry Tracer Provider and FastAPI Middleware**
  - **Action:** Update existing `services/person-api/app/main.py` with OpenTelemetry `TracerProvider` using `InMemorySpanExporter` (for test mode) and OTLP exporter for live mode.
  - **Positive (+v) Test:** Making an API request to existing `/api/v1/active/workers` generates an active OTel span with `http.status_code = 200`.
  - **Negative (-v) Test:** Making an unhandled failing request generates an OTel span with `status_code = ERROR` and records exception attributes.
  - **Verification Command:** `uv run pytest services/person-api/tests/test_telemetry.py -v`

- [x] **Task 1.3: Inject Trace IDs into JSON Log Output**
  - **Action:** Update existing logger in `services/person-api/app/core/logging.py` to extract `trace_id` and `span_id` from current OTel context and append them to JSON log output.
  - **Positive (+v) Test:** Log emitted during active worker request contains valid 32-hex `trace_id`.
  - **Negative (-v) Test:** Log emitted outside request context defaults `trace_id` to `null` or `"0"`.
  - **Verification Command:** `uv run pytest services/person-api/tests/test_logging.py -v`

- [x] **Task 1.4: Verify Telemetry Attached to All Existing Worker Endpoints (+v & -v Coverage)**
  - **Action:** Verify existing worker endpoints attach valid telemetry context across CRUD operations:
  - **Positive (+v) Tests:**
    1. `POST /api/v1/active/workers` creates record and emits trace span.
    2. `GET /api/v1/active/workers/{id}` reads record (verifying 600s cache) with trace span attached.
    3. `PUT /api/v1/active/workers/{id}` updates record, invalidates cache, and emits trace span.
    4. `DELETE /api/v1/active/workers/{id}` deletes record and emits trace span.
    5. `GET /health` returns `200 OK` (`healthy`) bypassing concurrency limits.
  - **Negative (-v) Tests:**
    1. `POST /api/v1/active/workers` with `status: inactive` returns `400 Bad Request` and logs error trace_id.
    2. `GET /api/v1/active/workers/{invalid_uuid}` returns `404 Not Found` and logs error trace_id.
    3. Exceeding `MAX_CONCURRENT=10` requests returns `503 Service Unavailable` with error trace context.
  - **Verification Command:** `uv run pytest services/person-api/tests/ -v`

---

## Phase 2: AIOps Sentinel Core & Local Qwen Integration

- [x] **Task 2.1: Implement Local Qwen Async Client with Mock Fallback**
  - **Action:** In `services/aiops-sentinel/app/core/llm.py`, build `AsyncOpenAI` client targeting `http://localhost:8000/v1`. Add a fixture to mock LLM responses if local server is unreachable.
  - **Positive (+v) Test:** Client sends prompt to Qwen and parses generated text.
  - **Negative (-v) Test:** When LLM server is down/times out (500ms timeout simulated), client gracefully catches `ConnectError` and raises structured `LLMUnavailableException`.
  - **Verification Command:** `uv run pytest services/aiops-sentinel/tests/test_llm.py -v`

- [x] **Task 2.2: Define Strict Pydantic Grounded RCA Schemas**
  - **Action:** In `services/aiops-sentinel/app/schemas/rca.py`, build strict models:
    * `EvidenceCitation`: `trace_id` (str), `span_id` (str), `snippet` (str).
    * `RCAResponse`: `root_cause` (str), `confidence` (float 0.0-1.0), `citations` (list), `dry_run_payload` (dict).
  - **Positive (+v) Test:** Valid structured JSON deserializes into `RCAResponse`.
  - **Negative (-v) Test:** JSON missing `citations` or invalid `confidence` (> 1.0) fails validation with `ValidationError`.
  - **Verification Command:** `uv run pytest services/aiops-sentinel/tests/test_schemas.py -v`

- [x] **Task 2.3: In-Memory OTel Signal Correlator**
  - **Action:** In `services/aiops-sentinel/app/engine/correlator.py`, build rolling 10-second memory window to group error logs and traces by `trace_id`.
  - **Positive (+v) Test:** Multiple log events with same `trace_id` collapse into single `IncidentContext`.
  - **Negative (-v) Test:** Logs outside 10s window or without error status are ignored.
  - **Verification Command:** `uv run pytest services/aiops-sentinel/tests/test_correlator.py -v`

- [x] **Task 2.4: Local Markdown Runbook Retriever**
  - **Action:** In `services/aiops-sentinel/app/engine/retriever.py`, create file-system retriever reading `.md` runbooks from `services/aiops-sentinel/runbooks/`.
  - **Positive (+v) Test:** Querying "database timeout" retrieves `db_latency.md` context.
  - **Negative (-v) Test:** Querying unknown error returns `default_fallback.md` without throwing `FileNotFoundError`.
  - **Verification Command:** `uv run pytest services/aiops-sentinel/tests/test_retriever.py -v`

- [x] **Task 2.5: Grounded Prompt Orchestrator & Remediation Whitelist**
  - **Action:** Combine Correlator + Retriever + Qwen LLM call in `services/aiops-sentinel/app/engine/orchestrator.py`. Validate `dry_run_payload` against allowed whitelist (`flush_cache`, `reset_pool`).
  - **Positive (+v) Test:** Valid trace context returns grounded RCA citing exact trace IDs.
  - **Negative (-v) Test:** If Qwen attempts non-whitelisted action (e.g., `drop_table`), system catches it and replaces action with `UNAUTHORIZED_ACTION_BLOCKED`.
  - **Verification Command:** `uv run pytest services/aiops-sentinel/tests/test_orchestrator.py -v`

---

## Phase 3: OpenWindow Control Desk (Django Web App) & Idempotency Bridge

- [x] **Task 3.1: Django Project Setup & Incident Data Models**
  - **Action:** Initialize Django application in `apps/openwindow/`. Define `Incident` model in `incidents/models.py` with fields: `id`, `root_cause`, `confidence`, `citations` (JSONField), `dry_run_payload` (JSONField), `status` (`NEW`, `APPROVED`, `EXECUTED`, `DUPLICATE_IGNORED`), `target_resource`, and `created_at`.
  - **Positive (+v) Test:** Django model creation and database migrations execute cleanly on SQLite/PostgreSQL.
  - **Negative (-v) Test:** Creating an incident without required fields raises `ValidationError`.
  - **Verification Command:** `uv run pytest apps/openwindow/tests/test_models.py -v`

- [x] **Task 3.2: Build OpenWindow Ingestion Endpoint & 10-Min Idempotency Guard**
  - **Action:** Implement `POST /api/v1/incidents/` in Django using Ninja/DRF/standard JSON views. Include 10-minute lookup logic: if an incident matching `(root_cause, target_resource)` exists within last 10 minutes, set status to `DUPLICATE_IGNORED`.
  - **Positive (+v) Test:** Valid incoming RCA creates new incident (`status: NEW`).
  - **Negative (-v) Test:** Duplicate incoming RCA within 10-minute window is suppressed (`status: DUPLICATE_IGNORED`). Malformed RCA returns HTTP 400.
  - **Verification Command:** `uv run pytest apps/openwindow/tests/test_ingest.py -v`

- [x] **Task 3.3: Django Web Dashboard Template & Human Approval Callback**
  - **Action:** Create Django HTML template view at `/dashboard/` rendering active incidents, confidence badges, and cited OTel trace IDs. Add `POST /incidents/{id}/approve/` endpoint that updates status to `APPROVED` and sends callback to Sentinel execution handler via `httpx`.
  - **Positive (+v) Test:** Approving incident via POST triggers Sentinel execution handler and updates status to `EXECUTED`. Dashboard view renders with 200 OK.
  - **Negative (-v) Test:** Approving an invalid or already executed incident ID returns HTTP 400 / 404.
  - **Verification Command:** `uv run pytest apps/openwindow/tests/test_dashboard.py -v`

---

## Phase 4: Mocked Chaos Lab Scripts (Python-Native)

- [x] **Task 4.1: Build Python Chaos Harness for All 6 Failures**
  - **Action:** Create `chaos/harness.py` that invokes `httpx.AsyncClient` against `person-api` to simulate failure scenarios in Python:
    1. `DB Latency` (Inject mock delay).
    2. `Pool Exhaustion` (Simulate max connections).
    3. `Slow Unindexed Search` (Scan large dataset).
    4. `Cache Staleness` (Mutate DB directly bypassing API cache).
    5. `Retry Storm` (Fire 20 simultaneous async requests).
    6. `Dependency Timeout` (Simulate mock HTTP drop).
  - **Positive (+v) Test:** Chaos triggers correctly emit OTel trace errors and trigger Sentinel detection.
  - **Negative (-v) Test:** System recovers after dry-run remediation is approved in OpenWindow dashboard.
  - **Verification Command:** `uv run pytest chaos/test_chaos_harness.py -v`

---

## Phase 5: Master Integration Test Script

- [x] **Task 5.1: End-to-End Suite Verification Script**
  - **Action:** Create `scripts/run_all_tests.py` that executes pytest across all 3 modules and the chaos harness natively via `uv`.
  - **Verification Command:** `uv run python scripts/run_all_tests.py`
