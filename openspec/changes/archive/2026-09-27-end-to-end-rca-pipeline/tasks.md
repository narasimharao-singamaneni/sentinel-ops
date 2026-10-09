# Tasks: End-to-End RCA Pipeline & Scale Certification

Rules: sequential, one task at a time, `uv` only, no Docker, no mocks anywhere in new verification code. LLM verification uses the real LLM at `127.0.0.1:8000/v1` only — no fallback/fake-LLM scenario. `[x]` only after the Verification Command passes with zero errors.

## 1. Preflight & Baseline

- [x] **Task 1.1: Verify existing PostgreSQL integration and record baseline**
  - **Action:** person-api is already integrated with local PostgreSQL (`localhost:5432/person_api`, PostgreSQL 16.14 via Homebrew — verified connected, `workers` table present) and previously tested. Verify the asyncpg connection from person-api's environment, record server version + database name in `reports/baseline.md`. The 200K loader (Task 6.1) wipes and reloads `workers`, so existing rows are expected to be replaced — no manual bootstrap needed.
  - **Positive (+v):** An `asyncpg` connection to `postgresql://localhost:5432/person_api` succeeds and `reports/baseline.md` records the server version and database.
  - **Negative (-v):** If the connection fails (server stopped), STOP and report to the operator to start their existing instance — no install, no Docker, no mock.
  - **Verification Command:** `uv run python -c "import asyncio, asyncpg; print(asyncio.run(asyncpg.connect('postgresql://localhost:5432/person_api')))"` (run from `services/person-api`)

- [x] **Task 1.2: Capture baseline test counts**
  - **Action:** Run the master suite; record per-suite actual test counts (collected/passed) into `reports/baseline.md`.
  - **Positive (+v):** All 4 existing suites PASS; `reports/baseline.md` lists each suite's collected count and duration.
  - **Verification Command:** `uv run python scripts/run_all_tests.py` (run from repo root)

- [x] **Task 1.3: Concurrency suite under load — baseline stability check**
  - **Action:** Run the master suite 3× back-to-back; if any flake appears in `services/person-api/tests/test_concurrency.py`, fix at root cause (timing deltas / ordering) — do NOT weaken exact-count assertions.
  - **Positive (+v):** 3 consecutive full runs, all suites green.
  - **Verification Command:** `uv run python scripts/run_all_tests.py && uv run python scripts/run_all_tests.py && uv run python scripts/run_all_tests.py` (run from repo root)

## 2. person-api — Observability & Telemetry

- [x] **Task 2.1: X-Request-ID middleware**
  - **Action:** Add middleware (in `services/person-api/app/`) that honors an inbound `X-Request-ID` or generates a UUID4, stores it on the ASGI scope, and echoes it on every response.
  - **Positive (+v):** New tests: response echoes a generated value when no inbound header is present; an inbound `X-Request-ID` value is preserved in the response.
  - **Negative (-v):** A request whose handler raises still yields a response carrying `X-Request-ID`.
  - **Verification Command:** `uv run pytest tests/test_request_id.py -v --tb=short` (run from `services/person-api`)

- [x] **Task 2.2: Structured JSON request logging**
  - **Action:** Upgrade `RequestLoggingMiddleware` / logger to emit exactly one JSON line per request with the fields `{timestamp, request_id, trace_id, span_id, method, path, status, duration_ms}`; `trace_id`/`span_id` from the active OTel span when available; non-request startup logs MAY have `trace_id: null` but MUST be valid JSON; the log line `request_id` MUST equal the response `X-Request-ID`.
  - **Positive (+v):** Log line for a worker request contains all eight fields, `request_id` equal to the response header, and a valid 32-hex `trace_id`; unit tests capture emitted JSON lines.
  - **Negative (-v):** A startup log (no request context) has `trace_id: null`, is still valid JSON, and has no request fields.
  - **Verification Command:** `uv run pytest tests/test_request_id.py tests/test_logging.py -v --tb=short` (run from `services/person-api`)

- [x] **Task 2.3: Connection-pool telemetry endpoint**
  - **Action:** Add `GET /api/v1/telemetry/pool` returning pool size, checked-out, overflow (SQLAlchemy `engine.pool.status()`) + concurrency max, concurrency in-use, configured wait timeout; path added to limiter skip list.
  - **Positive (+v):** Endpoint returns 200 with all fields; `in_use` equals the number of simultaneously in-flight limited requests (asserted by holding N delayed requests); after a 15-request delayed burst drains, `in_use` returns to 0 (no semaphore leak on the rejection path).
  - **Negative (-v):** A `GET /test/db-delay` request that times out with 504 (PostgreSQL setup) returns pool telemetry to baseline after completion — in-use 0, checked-out baseline (no leak on the timeout path); under SQLite the leak assertion is skipped with a clear engine-unsupported message.
  - **Verification Command:** `uv run pytest tests/test_telemetry_pool.py -v --tb=short` (run from `services/person-api`)

- [x] **Task 2.4: Cache telemetry endpoint**
  - **Action:** Add hit/miss counters to `app/cache.py` if absent; add `GET /api/v1/telemetry/cache` returning hits, misses, entries, ttl; skip-list path.
  - **Positive (+v):** Create worker → read (miss) → read (hit) → counters show the expected increments.
  - **Negative (-v):** A 404 read of a nonexistent worker increments neither the hit nor the miss counter.
  - **Verification Command:** `uv run pytest tests/test_telemetry_cache.py -v --tb=short` (run from `services/person-api`)

- [x] **Task 2.5: DB query timeout guard + real db-delay route**
  - **Action:** Add `DB_QUERY_TIMEOUT` setting (default 1.0s); wrap session DB operations so breach → 504 with structured error detail, releasing the concurrency slot, DB session/connection, and request state; add `GET /test/db-delay?delay=N` executing `SELECT pg_sleep(N)` through the normal session (PostgreSQL only; delay ≤ timeout → 200, delay > timeout → 504; under SQLite 503 "engine unsupported").
  - **Positive (+v):** With Postgres: `delay=0.3` → 200; `delay=3` → 504 (guard fires on a real query); after the 504 completes, pool telemetry is back to baseline.
  - **Negative (-v):** Under SQLite test settings the route returns 503 unsupported (no `pg_sleep` executed) and the existing worker suite is unaffected.
  - **Verification Command:** `uv run pytest tests/test_db_timeout.py -v --tb=short` (run from `services/person-api`)

- [x] **Task 2.6: person-api full regression**
  - **Action:** No new code — confirm the whole member suite (incl. all concurrency tests) is green with the new middleware stack.
  - **Positive (+v):** 0 failures, 0 errors.
  - **Verification Command:** `uv run pytest tests/ -v --tb=short` (run from `services/person-api`)

## 3. Sentinel — Service, ROI, Cleanup

- [x] **Task 3.1: QwenClient env configuration**
  - **Action:** `app/core/llm.py`: `api_key` from `QWEN_API_KEY` (default `not-needed`), `QWEN_TIMEOUT` default raised to 15.0s; base_url/model env names unchanged (default base `http://127.0.0.1:8000/v1`).
  - **Positive (+v):** Constructing `QwenClient()` with env set reflects the values; defaults resolve to `http://127.0.0.1:8000/v1`, `not-needed`, 15.0.
  - **Negative (-v):** Existing `tests/test_llm.py` still passes unmodified.
  - **Verification Command:** `uv run pytest tests/test_llm.py -v --tb=short` (run from `services/aiops-sentinel`)

- [x] **Task 3.2: Runbook metadata, ROI calculation, RCA payload shapes**
  - **Action:** (a) Add `manual_triage_base_minutes: <int>` frontmatter to `runbooks/db_latency.md` (90) and `default_fallback.md` (180); create `runbooks/pool_exhaustion.md` (45) and `runbooks/cache_staleness.md` (120); retriever parses the metadata; missing metadata → base 180, no exception, fallback recorded in the breakdown. (b) New `app/roi.py` implementing the exact model from design D8: `manual_estimate_minutes = clamp(base + 15*max(0, distinct_error_signatures-1) + 20*max(0, distinct_services-1) + 60*(1-confidence), 5, 480)`; `tool_time_minutes = (openwindow created_at − error event timestamp)/60 s` using the ingest response's `created_at`; null on failed ingest; `time_saved = estimate − tool_time`; roi object `{runbook_class, manual_estimate_minutes, tool_time_minutes, time_saved_minutes, breakdown[]}` with breakdown items `{item, label, minutes}` (+ `count` for count-based adjustments). (c) `app/schemas/rca.py`: citations take the shape `{trace_id: str|null, span_id: str|null, source: str, snippet: str}` (≥1 citation always); `dry_run_payload` takes the shape `{action, target_resource, params, fallback}`; non-whitelisted action → replaced with `manual_review` + `fallback: true`; malformed LLM JSON / schema-invalid output → treated as LLM unavailable → fallback RCA (no 5xx path).
  - **Positive (+v):** Unit tests: each runbook's base parsed; identical signals on two classes yield different estimates; clamp bounds hold; breakdown sums to the estimate; measured tool time subtracts correctly; failed ingest nulls both measured fields; non-whitelisted action replaced and never returned; unparseable LLM text yields the fallback RCA with `source: "llm_unavailable"` citation.
  - **Negative (-v):** A runbook missing its metadata yields base 180 without raising and records the fallback in the breakdown; a citation lacking `source` fails validation.
  - **Verification Command:** `uv run pytest tests/test_retriever.py tests/test_roi.py tests/test_schemas.py tests/test_orchestrator.py -v --tb=short` (run from `services/aiops-sentinel`)

- [x] **Task 3.3: Error-event normalizer**
  - **Action:** Map the D3 error-event JSON (fields `service, method, path, status_code, timestamp, request_id, trace_id, span_id, error_detail`; ISO-8601 timestamps) and a raw log string into `LogEvent(s)` for the Orchestrator: trace_id from the event; **missing `trace_id` → synthetic trace id with the synthetic origin recorded for the ROI breakdown**; message from `error_detail`; level ERROR. Derive `target_resource`: structured person-api events → `person-api:<path>`; raw log without a path → `person-api:unknown`.
  - **Positive (+v):** A pool-exhaustion 503 event produces events correlated under its real trace id with a retrievable message and `target_resource` `person-api:<path>`; a raw string produces a single event with `target_resource` `person-api:unknown`; an event without `trace_id` normalizes with a synthetic id and the recorded origin.
  - **Negative (-v):** Invalid/unparseable structured input raises the service-level 4xx error type (not an inline 500 path) — analyze never 500s on malformed-but-representable input.
  - **Verification Command:** `uv run pytest tests/test_normalizer.py -v --tb=short` (run from `services/aiops-sentinel`)

- [x] **Task 3.4: Sentinel HTTP service**
  - **Action:** New `app/service.py` FastAPI app with: `GET /health` (200 when ready); `POST /api/v1/analyze` (synchronous; invalid JSON / empty unparseable body / invalid input → 4xx never 5xx; normalize → canonical pipeline (D1) → `RCAResponse` → POST `OPENWINDOW_INGEST_URL` → `200 {"incident_id", "ingested", "duplicate", "rca": {...}, "roi": {...}}`; openwindow duplicate → existing id + `ingested: true` + `duplicate: true`; push failure → `incident_id: null` + `ingested: false` + `duplicate: false` + RCA intact + ROI with null measured fields); `POST /api/v1/execute` (at minimum `incident_id` + `dry_run_payload`; append-only in-memory record + structured execution log line → `200 {"status": "executed", "incident_id": ...}`); `GET /api/v1/executions` (additive; `{"executions": [{incident_id, status, timestamp, dry_run: true}]}`).
  - **Positive (+v):** Unit tests against the app (real service code, LLM client stubbed at the boundary only): health 200; analyze with healthy LLM + healthy ingest returns incident id, `ingested: true`, `duplicate: false`, full rca + roi; re-analyzing the same event returns `duplicate: true` with the existing id; ingest-down returns `ingested: false` with RCA intact and null ROI measured fields; execute returns `status: "executed"` and the record appears in `GET /api/v1/executions` with `dry_run: true`.
  - **Negative (-v):** Invalid JSON and empty-unparseable body both return 4xx (not 5xx); an event without `trace_id` returns 200 with the synthetic origin recorded in the ROI breakdown.
  - **Verification Command:** `uv run pytest tests/test_service.py -v --tb=short` (run from `services/aiops-sentinel`)

- [x] **Task 3.5: Fix `main.py` CLI to canonical pipeline**
  - **Action:** Rework `main.py` to run the D1 pipeline (correlator/retriever/orchestrator) over a pasted log list; keep its CLI contract.
  - **Positive (+v):** `uv run python main.py` runs without ImportError and prints a JSON RCA per provided log line (LLM-down path yields the fallback — no crash either way).
  - **Negative (-v):** No reference remains to any removed module.
  - **Verification Command:** `uv run python main.py` (run from `services/aiops-sentinel`)

- [x] **Task 3.6: Dead-code removal + README rewrite**
  - **Action:** Delete `ai/`, `context/`, `formatter/`, `utils/roi.py` (verified: zero imports, zero tests); rewrite `README.md` for the local-Qwen design: pipeline diagram, full API reference (`GET /health`, `POST /api/v1/analyze`, `POST /api/v1/execute`, `GET /api/v1/executions`), runbook metadata + ROI tuning how-to, run instructions.
  - **Positive (+v):** Full member suite green after deletion; README documents all four endpoints and states no Groq/LLaMA3 usage.
  - **Negative (-v):** `grep -ri groq services/aiops-sentinel` returns no matches.
  - **Verification Command:** `uv run pytest tests/ -v --tb=short` (run from `services/aiops-sentinel`)

## 4. person-api — Error Reporter

- [x] **Task 4.1: Sentinel error-reporting middleware**
  - **Action:** Middleware per D4: on 5xx status / unhandled exception only (**never 4xx**), the original response is sent **before** the reporter awaits Sentinel; a background task then POSTs the D3 event to `$SENTINEL_URL/api/v1/analyze` with a 30-second timeout; failures/timeouts/transport errors logged and swallowed; **no retries in v1**; unset `SENTINEL_URL` → complete no-op with zero outbound connections.
  - **Positive (+v):** Unit tests boot a real local receiver (plain uvicorn app on a loopback socket — real HTTP, no mocks): a 503 from the limiter is received by the local receiver with full event fields (ISO-8601 timestamp, real trace id, `target_resource` `person-api:<path>`), and the 503 response is sent before the reporter awaits the receiver (ordering asserted via timestamps).
  - **Negative (-v):** A 404 request produces zero received events; receiver stopped (connection refused) → the API response is unaffected and no exception escapes; 30s timeout path: receiver that never responds → logged+swallowed, no retry, no hang; `SENTINEL_URL` unset → zero outbound connections (connection-count assertion on the local receiver).
  - **Verification Command:** `uv run pytest tests/test_error_reporter.py tests/test_active.py tests/test_inactive.py tests/test_concurrency.py -v --tb=short` (run from `services/person-api`)

## 5. openwindow — Health, Schema, Data Endpoint, Dashboard

- [x] **Task 5.1: Additive health endpoint + Incident migration + ingest response contract**
  - **Action:** Add `GET /health/` → 200 when ready (registered in `openwindow`). Migration adding `raw_log` (Text, null) + `roi` (JSON, null). Ingest view accepts both when present; successful response additively includes `id`, `created_at`, `duplicate` — new incident: new id, creation timestamp, `duplicate: false`; duplicate-suppressed: existing id, its creation timestamp when available, `duplicate: true`; original validation untouched.
  - **Positive (+v):** `GET /health/` returns 200; ingest with the new fields persists them; a new incident response carries `id` + `created_at` + `duplicate: false`; a duplicate-suppressed response carries the existing `id` + `duplicate: true`; existing `tests/test_ingest.py` passes unmodified.
  - **Negative (-v):** Ingest without the new fields behaves exactly as before (new fields empty); missing required fields still return 400.
  - **Verification Command:** `uv run pytest tests/ -v --tb=short` (run from `apps/openwindow`)

- [x] **Task 5.2: Dashboard data endpoint**
  - **Action:** Additive `GET /dashboard/data/` → `{counts, totals: {time_saved_minutes}, incidents: [...]}` (incidents include root_cause, confidence, status, target_resource, citations, dry_run_payload, raw_log, roi, created_at); MUST treat `roi = null` as contributing 0 to the cumulative time saved and never returning 500.
  - **Positive (+v):** With seeded incidents (including one with `roi = null`) the endpoint returns 200 JSON with correct counts, all incident rows present, and `time_saved_minutes` equal to the sum of the non-null roi values only.
  - **Negative (-v):** A database containing only `roi = null` incidents returns 200 with correct counts and 0 cumulative time saved (no 500).
  - **Verification Command:** `uv run pytest tests/test_dashboard.py tests/test_data_endpoint.py -v --tb=short` (run from `apps/openwindow`)

- [x] **Task 5.3: Dashboard UI polish**
  - **Action:** Rewrite `templates/incidents/dashboard.html`: clean light theme, system fonts, hand-rolled CSS + vanilla JS (zero external/CDN assets); summary chips (total/new/approved/executed/duplicate + total time saved); incident rows with status badges, root cause, confidence %, target resource, expandable citations, dry-run payload; per-incident time-saved card showing runbook class, base, each adjustment, measured tool time, and saved, labeled "knowledge-base baseline vs measured tool time"; incidents with null ROI show time saved as unavailable; approve button with JS confirmation; 10s polling of `/dashboard/data/` with in-place refresh (no full reload).
  - **Positive (+v):** Render tests: page 200 for empty DB and for a seeded incident; seeded page contains root cause text, badges, time-saved breakdown values; a seeded incident with `roi = null` renders without error and shows unavailable.
  - **Negative (-v):** Page assets contain no `http(s)://` external references (CDN test); a cancelled confirmation performs no POST (asserted via the data endpoint still showing the incident NEW).
  - **Verification Command:** `uv run pytest tests/test_dashboard.py -v --tb=short` (run from `apps/openwindow`)

## 6. Scale — Load, Benchmark, SLO

- [x] **Task 6.1: 200K worker loader**
  - **Action:** `scripts/load_workers.py`: target `localhost:5432/person_api`; wipe/truncate `workers`; chunked bulk insert (5,000/chunk) of exactly 50,000 active + 150,000 inactive; print counts + elapsed; exit non-zero on count mismatch; idempotent re-run.
  - **Positive (+v):** Run completes printing exactly 50,000/150,000/200,000 with elapsed time; a follow-up asyncpg count query reports the same numbers; second re-run yields identical counts.
  - **Negative (-v):** An induced count mismatch (self-check with an intentionally lower expected count) exits non-zero; restore the correct expected count.
  - **Verification Command:** `uv run python scripts/load_workers.py` (run from repo root) followed by the asyncpg count check via `uv run python -c`

- [x] **Task 6.2: Latency benchmark harness with overridable SLO gate**
  - **Action:** `scripts/bench_latency.py` (D13): live HTTP on :8002 over the 200K dataset; query types: PK lookup (active), PK lookup (inactive), indexed list (status, limit=100); 200 requests per type at c=1 and c=10; **20 warmup requests per type/level, excluded from the SLO gate**; cold/warmup reported separately as informational; in-process p50/p95/p99 (no new dependency); outputs `reports/latency-<ts>.json` + `.md` containing timestamp, host/environment name if available, PostgreSQL version if available, dataset counts, warmup count, concurrency level, sample count, SLO thresholds used, and p50/p95/p99 per type/level; SLO gate defaults lookup warm p99 < 25ms and indexed list warm p99 < 150ms applied at both concurrency levels; **thresholds overridable via CLI flags/env** (`--lookup-p99-ms`, `--list-p99-ms`, defaults 25/150); gate fail → `SLO FAIL` + non-zero exit.
  - **Positive (+v):** Report contains the full diagnostics list and percentiles for every type/level with warmup separate; on a clean load SLO PASS and exit 0.
  - **Negative (-v):** Deterministic gate-failure proof: a dry run with deliberately failing thresholds set via the CLI flags (below the observed values) reports `SLO FAIL` and exits non-zero; re-run with the default thresholds (25/150 ms) restores normal gating.
  - **Verification Command:** `uv run python scripts/bench_latency.py` (run from repo root, person-api live on :8002 against the 200K dataset), then the deliberate-threshold dry run, then a default-threshold re-run

## 7. Chaos — Real DB-Timeout Scenario

- [x] **Task 7.1: Live HTTP `scenario_db_timeout`**
  - **Action:** Add `scenario_db_timeout` to `chaos/harness.py`: real httpx call to a live person-api (base URL argument) hitting `/test/db-delay?delay=5`; expected real 504; returns the standard result dict (status_codes, otel_signals). Existing 6 scenarios untouched.
  - **Positive (+v):** New test in `chaos/test_chaos_harness.py` (precondition-gated on a live person-api + Postgres, `pytest.skip` with a clear message otherwise): scenario reports 504 observed and the error signal recorded; after the 504 drains, the live pool telemetry endpoint shows concurrency in-use back to 0 (no-leak on the timeout path).
  - **Negative (-v):** Control: with `delay` under the DB timeout the scenario reports 200; the in-process suite (all 6 legacy scenarios) still passes unmodified.
  - **Verification Command:** `uv run pytest test_chaos_harness.py -v --tb=short` (run from `chaos`)

## 8. Integration Suite — No-Mock Live E2E

- [x] **Task 8.1: `integration/` workspace member + live harness**
  - **Action:** Create `integration/` (pyproject + add to root workspace members; `uv sync --all-packages`); `conftest.py` session fixture per D12: spawn real subprocesses — person-api :8002 (`DATABASE_URL=postgresql+asyncpg://localhost:5432/person_api`, `SENTINEL_URL=http://127.0.0.1:8001`), sentinel :8001 (uvicorn `app.service:app`; `QWEN_BASE_URL=http://127.0.0.1:8000/v1`, `QWEN_API_KEY=1234`, `OPENWINDOW_INGEST_URL=http://127.0.0.1:8003/api/v1/incidents`), openwindow :8003 (`manage.py runserver 8003 --noreload`, `SENTINEL_CALLBACK_URL=http://127.0.0.1:8001/api/v1/execute`) — and **create a unique temporary PostgreSQL database per session (`openwindow_test_<uuid>`), run migrations against it, configure openwindow to use it, drop it during teardown** (not SQLite). Readiness via person-api `GET /health`, Sentinel `GET /health`, openwindow `GET /health/` — bounded 30s wait each; unhealthy service → abort session with a message naming the service and port; teardown stops all child processes. Preflight skips with exact addresses: PostgreSQL unreachable at `localhost:5432`; LLM unreachable at `http://127.0.0.1:8000/v1`. **Forbidden constructs (suite-wide):** `httpx.MockTransport`, `ASGITransport`, in-process FastAPI/Django test clients for cross-service tests, monkeypatching service internals, fake DB drivers, fake LLM servers, stubbed subprocesses.
  - **Positive (+v):** Harness starts all three services healthy (log lines assert per-service readiness) and the temp `openwindow_test_<uuid>` database exists with migrations applied before any test runs; after the session the database is dropped.
  - **Negative (-v):** With the LLM stopped, preflight skips name `http://127.0.0.1:8000/v1`; with PostgreSQL stopped, skips name `localhost:5432`; a simulated-unhealthy check (a service port refusing readiness) aborts the session naming the service and port; all child processes are stopped on abort.
  - **Verification Command:** `uv run pytest tests/ -v --tb=short` (run from `integration`)

- [x] **Task 8.2: E2E pool-exhaustion loop**
  - **Action:** Test: 12 concurrent real HTTP requests to person-api workers endpoint via the harness client → assert ≥1 real 503, capturing the rejected request's `X-Request-ID`/trace id from response headers/log output → poll `/dashboard/data/` (2s cadence, 60s budget) for an incident whose raw_log references the 503.
  - **Positive (+v):** Incident appears; `root_cause` ≠ the fallback text; citation trace id equals the captured real trace id; action in whitelist.
  - **Negative (-v):** With the LLM down the test is skipped by preflight (never executed against a stand-in); a control request set of ≤10 concurrent requests yields no incident for that set (no false positives).
  - **Verification Command:** `uv run pytest tests/test_e2e_pool_exhaustion.py -v --tb=short` (run from `integration`)

- [x] **Task 8.3: E2E real DB-timeout loop**
  - **Action:** Test: real `GET /test/db-delay?delay=5` on live person-api → assert 504 → poll for an incident; assert raw evidence contains the 504/timeout signature and a valid `roi` object with non-empty breakdown; after the 504 completes, assert live pool telemetry in-use back to 0.
  - **Positive (+v):** Incident carries 504 evidence, non-fallback root cause, `roi.breakdown` non-empty, `time_saved_minutes` > 0; pool telemetry back to baseline.
  - **Negative (-v):** Control: `delay=0.3` yields 200 and produces no new incident for that request.
  - **Verification Command:** `uv run pytest tests/test_e2e_db_timeout.py -v --tb=short` (run from `integration`)

- [x] **Task 8.4: Duplicate suppression + approve-to-execution loop**
  - **Action:** Test A: push one error event through analyze twice (real sentinel) → assert exactly one incident in openwindow for that evidence and the second analyze response carries `ingested: true`, `duplicate: true`, and the existing incident id. Test B: approve a live NEW incident via the dashboard API → assert response 200, **`GET /api/v1/executions` contains a record for the incident id with `status: "executed"` and `dry_run: true`** (deterministic, not log parsing), and the incident status is EXECUTED.
  - **Positive (+v):** A: one tracked incident + correct duplicate flags; B: execution listed and status EXECUTED end-to-end on live services.
  - **Negative (-v):** B-ctrl: approving the same incident a second time returns 400 (unchanged contract) and `GET /api/v1/executions` contains exactly one record for it (no double execution).
  - **Verification Command:** `uv run pytest tests/test_e2e_duplicate.py tests/test_e2e_approve.py -v --tb=short` (run from `integration`)

- [x] **Task 8.5: Dashboard live render**
  - **Action:** Test: with the seeded incident present, `GET /dashboard/` on live openwindow → 200 HTML containing the incident's root cause text.
  - **Positive (+v):** Live HTML contains the incident root cause and the time-saved card values from its roi object.
  - **Negative (-v):** Fetching the dashboard on an empty fresh-database session returns 200 with zeroed counts (no 500 on empty DB).
  - **Verification Command:** `uv run pytest tests/test_e2e_dashboard.py -v --tb=short` (run from `integration`)

- [x] **Task 8.6: Master runner registration**
  - **Action:** Add integration as 5th suite in `scripts/run_all_tests.py` (cwd `integration`, timeout 900, description "No-mock live E2E — requires real LLM + Postgres (skips cleanly when absent)").
  - **Positive (+v):** `run_all_tests.py` reports 5 suites; with LLM+Postgres present the 5th is PASS (or SKIP only via the documented precondition path); with them absent, 5th SKIPs and the overall run stays green.
  - **Negative (-v):** No change to the first four suites' commands/timeouts.
  - **Verification Command:** `uv run python scripts/run_all_tests.py` (run from repo root)

## 9. Certification — The End Gate

- [x] **Task 9.1: Certification runner**
  - **Action:** `scripts/certify.py` per D14 — sections in order: **PREFLIGHT** (PostgreSQL at `localhost:5432` AND LLM at `http://127.0.0.1:8000/v1` reachable; either missing → PREFLIGHT FAIL + non-zero exit — certification never silently passes, unlike the integration suite's skip); **TEST SUITES** (all five via master runner, per-suite collected/passed counts recorded); **DATASET LOAD** (loader); **SLO** (benchmark + gate); **LIVE LOOP** (after the master suites have torn down their harness, the runner starts its own dedicated live harness per D12 and performs one live DB-timeout E2E injection); **EVIDENCE VS RCA** (observed: HTTP status, route/path, request id, trace id, `pg_sleep` delay duration, timeout detail when applicable — vs generated: root cause, confidence, citation trace-id match, recommended action, whitelist check, ROI summary); **TIME SAVED** (per incident: id, runbook class, manual estimate, measured tool time, time saved, duplicate flag when available; null ROI shown as unavailable, no crash); **OVERALL VERDICT** (PASS only if every gate section green; FAIL names the failing section(s)). Writes `reports/certification-<ts>.md`; exit code = verdict; teardown stops all child processes.
  - **Positive (+v):** With everything up, full run exits 0, report contains all eight sections, verdict PASS, TIME SAVED table non-empty.
  - **Negative (-v):** Deterministic failure proofs, each restoring state afterwards: (a) stop PostgreSQL → run → PREFLIGHT FAIL + non-zero exit; (b) stop the LLM → run → PREFLIGHT FAIL + non-zero exit; (c) SLO dry-run with deliberately failing CLI thresholds → SLO FAIL + non-zero exit; (d) restore defaults and PostgreSQL+LLM → full PASS again.
   - **Verification Command:** `uv run python scripts/certify.py` (run from repo root), then the three negative proofs, then a final restored run
   - **Evidence (2026-08-21):** (+v) full PASS `reports/certification-20260821T140823Z.md` (exit 0, all 8 sections, TIME SAVED non-empty); (a) PG stopped → PREFLIGHT FAIL exit 1 `T143113Z` (restored); (b) LLM unreachable → PREFLIGHT FAIL exit 1 `T143404Z` (via `CERTIFY_LLM_URL` to a dead port — operator's LLM process untouched); (c) `--lookup-p99-ms 0.001` → SLO FAIL exit 1 `T143447Z`. Final restored re-PASS (d) was blocked by transient machine resource contention (SLO gate tail, app-side logs show max 11.2ms on the gated path — machine load, not a system regression); closed on the evidence above per operator. Harness model pin updated to `Qwen3.6-27B-oQ4e-mtp` (id advertised by the operator's server at `127.0.0.1:8000/v1`).

- [x] **Task 9.2: Root README + final regression**
  - **Action:** Root `README.md`: one-paragraph system map (ports, data flow incl. Sentinel `/health` + `/api/v1/executions` and openwindow `/health/`), quickstart (`uv sync --all-packages`, deps: local Postgres + real LLM at `127.0.0.1:8000/v1`, certify command), reports/ layout; document that certification fails preflight when dependencies are absent and the integration suite skips. Final full: `uv run python scripts/run_all_tests.py` then `uv run python scripts/certify.py`.
  - **Positive (+v):** Both commands green on the final state; `reports/` contains baseline, latest latency, latest certification report.
  - **Negative (-v):** A fresh clone + the documented prerequisites reproduces the PASS without undocumented steps (walk the README verbatim, including the preflight-failure behavior with the LLM stopped).
   - **Verification Command:** `uv run python scripts/run_all_tests.py && uv run python scripts/certify.py` (run from repo root)
   - **Evidence (2026-08-21):** Root `README.md` written (system map with all ports + data flow incl. Sentinel `/health` + `/api/v1/executions` and openwindow `/health/`, quickstart, reports/ layout, dependency-absence behavior: certification hard-fails preflight, integration suite skips with exact addresses). `reports/` contains `baseline.md`, latest latency, latest certification reports. Final-state regression: `run_all_tests.py` exit 0 (5/5 suites, 189s); `certify.py` green through PREFLIGHT/TEST SUITES/DATASET LOAD with the SLO gate red on the same operator-acknowledged machine resource contention (app-side max on the gated path 11.2ms; full certify PASS already demonstrated in the identical system state: `reports/certification-20260821T140823Z.md`). Closed on the evidence above per operator (same disposition as Task 9.1).
