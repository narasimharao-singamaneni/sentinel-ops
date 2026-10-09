# Proposal: End-to-End RCA Pipeline & Scale Certification

## Agent Execution Rules
1. Process tasks sequentially from top to bottom. Do NOT skip tasks.
2. Execute **only ONE task at a time**.
3. Use **strictly `uv`** commands for package management, script execution, and test execution (NEVER use `pip` or Docker).
4. After writing or modifying code for a task, run the specified **Verification Command**.
5. Both Positive (+v) and Negative (-v) assertions inside test files MUST pass.
6. Do NOT mark a task as complete `[x]` until its verification command succeeds with zero errors.
7. **No mocks, no stubs, no fakes in any new integration/certification code.** All end-to-end verification runs against real processes, real PostgreSQL, and the real local LLM at `127.0.0.1:8000/v1`. Existing unit suites (which use boundary-level stubs) remain untouched. The LLM is tested against port 8000 only — no fallback/fake-LLM scenario is built.

## Why

The three services exist but are not wired: person-api errors go nowhere, AIOps Sentinel has no service layer (its CLI entry point is broken and never reachable), openwindow only receives incidents by hand. There is no live proof that a real production-style error becomes a real LLM-generated root cause in the dashboard, and person-api has never been proven at its stated scale (200K workers) with measured latency SLOs. The end goal is a **certified** system: real errors → real RCA → real incident → approved execution, plus an auditable time-saved ROI for presentation.

## What Changes

**person-api (`services/person-api/`)**
- Error reporting middleware: 5xx responses and unhandled exceptions (never 4xx) are automatically reported to Sentinel's analyze endpoint as a background task after response completion — the original response is sent before the reporter awaits Sentinel; 30s reporting timeout, failures logged and swallowed, no retries in v1; complete no-op with zero outbound connections when `SENTINEL_URL` is unset
- Request IDs: `X-Request-ID` on every response (honored when inbound, else new UUID4); one structured JSON log line per request containing `timestamp`, `request_id`, `trace_id`, `span_id`, `method`, `path`, `status`, `duration_ms`
- Telemetry endpoints: `GET /api/v1/telemetry/pool` (pool size, checked-out, overflow, concurrency max/in-use, configured wait timeout) and `GET /api/v1/telemetry/cache` (hits, misses, entries, ttl); 404 reads do not increment cache counters; both rejection and DB-timeout paths must release the concurrency slot (no-leak, asserted via pool telemetry)
- Real DB-timeout guard: `DB_QUERY_TIMEOUT` setting (default 1.0s); `GET /test/db-delay?delay=N` executes a real `pg_sleep(N)` through the normal session (PostgreSQL only; 503 "engine unsupported" under SQLite); breach → 504 with the concurrency slot, DB session/connection, and request state fully released and pool telemetry back to baseline

**AIOps Sentinel (`services/aiops-sentinel/`)**
- New HTTP service (uvicorn, port 8001): `GET /health`, `POST /api/v1/analyze`, `POST /api/v1/execute`, and additive `GET /api/v1/executions`
- Analyze: synchronous; accepts a structured JSON error event or a raw log string; invalid/unparseable input returns 4xx (never 5xx); missing `trace_id` succeeds via a synthetic trace id recorded in the ROI breakdown; returns `200 {incident_id, ingested, duplicate, rca, roi}` — including the openwindow duplicate-suppressed state (`duplicate: true`) and push-failure state (`ingested: false`, ROI `tool_time_minutes`/`time_saved_minutes` null)
- RCA payload shapes fixed: citations `{trace_id|null, span_id|null, source, snippet}` (≥1 in all cases), `dry_run_payload {action, target_resource, params, fallback}`; non-whitelisted actions replaced with `manual_review` + `fallback: true`; malformed LLM JSON treated as LLM unavailable → fallback RCA, HTTP 200
- Canonical RCA path = existing `app/engine` pipeline (Correlator → RunbookRetriever → Orchestrator → QwenClient); broken `main.py` fixed to use it
- Per-incident **ROI**: exact, runbook-metadata-driven model — each runbook declares `manual_triage_base_minutes` (starters 90/45/120/180; missing metadata falls back to 180 and records it); `manual_estimate_minutes` from the exact adjustment formula (clamped 5–480); `tool_time_minutes` measured from error-event timestamp to openwindow `created_at`; full auditable `breakdown` array; no hardcoded universal baseline
- LLM config from env: `QWEN_BASE_URL`, `QWEN_MODEL`, `QWEN_API_KEY` (default `not-needed`), `QWEN_TIMEOUT` (default 15s); only the real LLM at `127.0.0.1:8000/v1` is used in all verification
- Dead Gen-1 code removed: `ai/` (Groq client), `context/`, `formatter/`, `utils/roi.py`; README rewritten (local-Qwen design, full API reference incl. `/health` and `/api/v1/executions`)

**openwindow (`apps/openwindow/`)**
- Additive: `GET /health/` endpoint (used by the live harness)
- Ingest response additively includes `id`, `created_at`, and `duplicate` (false for new incidents; for duplicate-suppressed incidents `duplicate: true` with the existing incident id and its creation timestamp)
- Additive `Incident` fields: `raw_log` (text) + `roi` (JSON)
- New additive `GET /dashboard/data/` JSON endpoint — handles `roi = null` gracefully (contributes 0 to cumulative time saved, never 500)
- Dashboard UI polish: clean light theme, no external assets (no CDN), summary chips, status badges, per-incident time-saved with breakdown labeled "knowledge-base baseline vs measured tool time", approve-with-confirmation, 10s auto-refresh

**New: `integration/` workspace member**
- No-mock live E2E suite: real subprocesses — person-api :8002 (real Postgres DSN `postgresql+asyncpg://localhost:5432/person_api`, `SENTINEL_URL=http://127.0.0.1:8001`), sentinel :8001 (`QWEN_BASE_URL=http://127.0.0.1:8000/v1`, `QWEN_API_KEY=1234`, `OPENWINDOW_INGEST_URL=http://127.0.0.1:8003/api/v1/incidents`), openwindow :8003 (`manage.py runserver 8003 --noreload`, `SENTINEL_CALLBACK_URL=http://127.0.0.1:8001/api/v1/execute`) on a **fresh temporary PostgreSQL database per session** (migrated, dropped on teardown) — not SQLite
- Readiness via `GET /health` (person-api), `GET /health` (Sentinel), `GET /health/` (openwindow) with 30s bounded waits; abort with a clear message naming the service and port if unhealthy
- Missing PostgreSQL/LLM ⇒ precondition skip with the expected address named (never simulated); explicitly forbidden in the suite: `MockTransport`, `ASGITransport`, in-process FastAPI/Django test clients for cross-service tests, monkeypatching, fake DB drivers, fake LLM servers, stubbed subprocesses
- Scenarios: pool-exhaustion 503 → traced incident, real DB-timeout 504 → incident, duplicate suppression (via analyze response flags), approve → execution asserted through `GET /api/v1/executions` → EXECUTED, dashboard render with live data

**New: `scripts/` + scale certification**
- `load_workers.py` — idempotent wipe + chunked (5,000) load into `localhost:5432/person_api`: exactly 50,000 active + 150,000 inactive; prints counts + elapsed; exits non-zero on mismatch
- `bench_latency.py` — live-HTTP benchmark on the 200K dataset: 200 requests each of PK lookup (active/inactive) and indexed list (limit=100) at concurrency 1 and 10, 20 warmup requests per type/level excluded from the SLO gate, cold/warmup reported separately as informational, in-process p50/p95/p99 (no new dependency), JSON + Markdown report with full failure diagnostics; SLO gate (lookup warm p99 < 25ms, indexed list warm p99 < 150ms, both concurrency levels) with **overridable thresholds for negative testing**
- `run_all_tests.py` gains the integration suite as 5th suite (900s timeout, clean precondition skip)
- `certify.py` — single certification run: all five suites, dataset load, SLO, one live DB-timeout E2E against its **own dedicated harness** (started after the master suites and their teardown), evidence-vs-RCA comparison, cumulative time-saved table → `reports/certification-<ts>.md` with sections PREFLIGHT / TEST SUITES / DATASET LOAD / SLO / LIVE LOOP / EVIDENCE VS RCA / TIME SAVED / OVERALL VERDICT; **PREFLIGHT FAILS (non-zero exit) when PostgreSQL or the LLM is missing — certification never silently passes**

No **BREAKING** changes externally: all API changes are additive; existing endpoints and unit suites keep their contracts.

## Capabilities

### New Capabilities
- `rca-pipeline`: live error → RCA → incident flow — person-api error reporting, Sentinel health/analyze/execute/executions HTTP API, RCA push to openwindow, exact RCA payload shapes, LLM configuration, per-incident ROI time-saved
- `scale-certification`: 200K-worker PostgreSQL dataset, warm/cold latency benchmark with overridable SLO gate, request IDs + structured logs, connection-pool and cache telemetry with no-leak guarantees, real DB-timeout scenario, and the PASS/FAIL certification runner
- `incident-dashboard`: user-facing openwindow incident dashboard — health endpoint, ingest response contract, display, counts, time-saved visualization, approve-with-confirmation, auto-refresh, offline-friendly
- `live-integration-tests`: no-mock end-to-end test harness — real subprocesses, temporary PostgreSQL for openwindow, readiness gates, live-loop tests, dependency preconditions, master-runner registration

### Modified Capabilities
(None — `openspec/specs/` is empty; all capabilities are new)

## Impact

- **Code:** `services/person-api/` (middleware, telemetry, timeout guard, tests), `services/aiops-sentinel/` (new service app, ROI, runbooks, tests, dead-code removal), `apps/openwindow/` (migration, health endpoint, views, template, tests), new `integration/` package + root pyproject workspace member, `chaos/` (one live HTTP scenario), `scripts/` (4 new/updated)
- **Data:** Existing local PostgreSQL on `localhost:5432` (already integrated with person-api and tested — no Docker, no install): `person_api` gets the 200K-row dataset (loader wipes existing `workers` rows), plus per-session temporary `openwindow_test_<uuid>` databases; new openwindow migration (additive columns)
- **Runtime:** LLM server at `127.0.0.1:8000/v1` (user-run) is required for live tests (skip) and for certification (preflight failure); no other external dependencies
- **Existing behavior:** invariant in isolation — with `SENTINEL_URL` unset, person-api behaves exactly as before
