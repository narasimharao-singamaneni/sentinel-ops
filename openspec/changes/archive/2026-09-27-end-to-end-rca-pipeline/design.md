# Design: End-to-End RCA Pipeline & Scale Certification

## Context

Current state (audited):

- **person-api** (FastAPI, :8002): concurrency-limited worker API with OTel instrumentation, in-memory semaphore limiter (max 10, 3s wait → 503), TTL cache on active-worker reads. Tests run in-process on SQLite. No error reporting, no request IDs, no pool/cache exposure, no DB-timeout guard.
- **AIOps Sentinel**: two code generations coexist. Gen 2 (canonical, per original design intent): `app/core/llm.py` (QwenClient → local OpenAI-compatible endpoint), `app/engine/` (Correlator → RunbookRetriever → Orchestrator with remediation whitelist → strict `RCAResponse` validation). Gen 1 (dead, zero imports, zero tests): `ai/llm_client.py` (Groq URL), `context/` (JSON knowledge base), `formatter/`, `utils/roi.py`. `main.py` CLI is broken (imports nonexistent `run_rca_pipeline`); `engine/rca_engine.py` is a tested single-event helper kept for compatibility.
- **openwindow** (Django, WSGI via runserver, :8003): incidents API (ingest with 10-minute dedupe on `(root_cause, target_resource)`, approve with `SENTINEL_CALLBACK_URL` default `http://localhost:8001/api/v1/execute`), basic dashboard template. `Incident` has no evidence-text or ROI fields. No health endpoint.
- **chaos**: 6 scenarios, all in-process (ASGITransport) — pure load-based (pool exhaustion, retry storm) or simulated delays. No real HTTP, no real Postgres.
- **Tooling**: `scripts/run_all_tests.py` runs 4 suites (person-api, sentinel, openwindow, chaos) via `uv run` from each member dir. `openspec/specs/` empty → all capabilities here are new.

Hard constraints from the operator:

1. **No Docker.** PostgreSQL is the existing local instance person-api is already integrated with and tested against — `localhost:5432`, server `person_api` (PostgreSQL 16.14 via Homebrew, verified connected).
2. **No mocks** in any new verification code — real processes, real DB, real LLM at `127.0.0.1:8000/v1` (key `1234`, user-run). Existing unit suites keep their boundary-level stubs untouched. **The LLM is tested against port 8000 only — no fallback/fake-LLM scenario is built** (the in-code LLM-unavailable fallback stays as an existing runtime safeguard; it is not a tested scenario).
3. ROI bases live in the **runbook knowledge base** (editable markdown frontmatter), never a hardcoded universal constant.

## Goals / Non-Goals

**Goals:**
- Live loop: real person-api error → Sentinel analyze (real LLM, runbook-grounded) → openwindow incident → approve → execution recorded, observed end-to-end
- Deterministic machine-readable contracts: Sentinel `/health`, `/api/v1/analyze` (with duplicate/failure states), `/api/v1/execute`, `/api/v1/executions`; openwindow `/health/` and ingest `id`/`created_at`/`duplicate`
- person-api proven at 200K scale on real Postgres with warm/cold p50/p95/p99 and an overridable SLO gate
- Auditable per-incident time-saved ROI (measured tool time vs exact knowledge-base manual estimate) shown on dashboard + certification report
- One `uv run` certification command → PASS/FAIL report that **fails** (preflight) when live dependencies are absent
- Sentinel codebase single-generation: canonical path, broken CLI fixed, dead code removed, README truthful

**Non-Goals:**
- Real remediation execution (v1 = dry-run acknowledgment + recorded execution only; no orchestrator actions touch infra)
- Async job store / job id on analyze (v1 analyze is synchronous; fire-and-forget is on the person-api side)
- Any fallback-LLM or fake-LLM test scenario (LLM = port 8000 only)
- Distributed/multi-host deployment, message queues, streaming logs
- Auto-execution without human approval
- Rewriting existing unit suites (scope of "no mocks" = new integration/certification code)

## Decisions

### D1 — Canonical RCA path = `app/engine` Orchestrator pipeline
`analyze` normalizes the incoming error event into `LogEvent(s)`, feeds the Correlator, retrieves the matching runbook, and asks the Orchestrator (LLM + whitelist + validation). `engine/rca_engine.py::analyze_event` stays as the tested single-event fallback utility (used when the event yields no usable trace context); its tests remain green.
*Alternatives:* keep the simple direct-LLM path (rejected — drops runbook grounding + whitelist, the original design's point); delete `engine/rca_engine.py` (rejected — regresses an existing green suite).

### D2 — Sentinel HTTP service contract
New FastAPI app `services/aiops-sentinel/app/service.py` on port 8001 (uvicorn):
- **`GET /health`** → 200 when ready (live-harness readiness signal).
- **`POST /api/v1/analyze` (synchronous):** accepts a structured JSON error event or a raw log string. Invalid JSON / empty unparseable body / invalid input → **4xx, never 5xx**. Missing `trace_id` → success via a **synthetic trace id**, with the synthetic origin recorded in the ROI breakdown. Normalizes (D3) → D1 pipeline → `RCAResponse` → push to openwindow `OPENWINDOW_INGEST_URL` → returns `200` with exactly:
  ```json
  { "incident_id": "string | null", "ingested": true, "duplicate": false,
    "rca": { "root_cause": "...", "confidence": 0.0, "citations": [], "dry_run_payload": {} },
    "roi": {} }
  ```
  States: successful ingest → openwindow id + `ingested: true` + `duplicate: false`; openwindow duplicate suppression → existing id (when available) + `ingested: true` + `duplicate: true`; push failure → `incident_id: null` + `ingested: false` + `duplicate: false`, RCA still returned, ROI still returned with `tool_time_minutes` and `time_saved_minutes` null.
- **`POST /api/v1/execute`:** accepts at minimum `incident_id` + `dry_run_payload`; records in an **append-only in-memory list**, emits a structured execution log line, returns `200 {"status": "executed", "incident_id": "..."}`.
- **`GET /api/v1/executions`** (additive): `{"executions": [{"incident_id", "status", "timestamp", "dry_run": true}]}` — exists so the live approve-to-execution test asserts execution deterministically instead of parsing logs.
*Alternatives:* async 202 + job store (rejected — adds state, doubles the polling surface; synchronous keeps v1 small and the 2–20s LLM latency is absorbed by the fire-and-forget caller); log-only execution record (rejected — log parsing is nondeterministic for tests).

### D3 — Error event schema (person-api → Sentinel)
`{service, method, path, status_code, timestamp, request_id, trace_id, span_id, error_detail}` — all from the live request context (OTel trace ids from the active span, `X-Request-ID` from D6). **Timestamps are ISO-8601.** `target_resource` derivation (done by Sentinel): structured person-api events → `person-api:<path>`; raw log input without a path → `person-api:unknown`. Reporter policy (D4): 5xx and unhandled exceptions only — **4xx are never reported**.

### D4 — Fire-and-forget reporter, env-gated, response-first
person-api middleware: the original response is **sent before the reporter awaits Sentinel**; reporting runs as a background task after response completion; 30-second timeout on the reporting call; failures/timeouts/transport errors are logged and swallowed; **no retries in v1**; unset `SENTINEL_URL` → complete no-op with **zero outbound connections** (keeps all existing/chaos unit behavior untouched).
*Alternatives:* synchronous await (rejected — couples API latency to LLM latency); at-least-once retry queue (deferred to v2).

### D5 — Real DB-timeout mechanism, no-leak guaranteed
New setting `DB_QUERY_TIMEOUT` (default 1.0s): DB session operations are wrapped in `asyncio.wait_for`; breach → 504 with structured error detail. New route `GET /test/db-delay?delay=N` executes `SELECT pg_sleep(N)` through the normal session (PostgreSQL-only; 503 "engine unsupported" under SQLite so unit suites stay deterministic). On 504 the guard MUST release the concurrency slot, the DB session/connection, and any related request state; after drain, pool telemetry (D7) returns to baseline — asserted by tests on both the rejection and timeout paths. This is a *real* timeout of a *real* query — no in-process fabrication.
*Alternatives:* Postgres `statement_timeout` server setting (rejected — less portable, per-test variation needs session management); simulate delay in-process (rejected — violates the "real, no mocks" constraint).

### D6 — Request IDs + structured logs
Middleware sets `X-Request-ID` (honors inbound header, else UUID4), stored on the ASGI scope, echoed on every response; the header MUST equal the log line's `request_id`. Logger upgrades to exactly one JSON line per request: `{timestamp, request_id, trace_id, span_id, method, path, status, duration_ms}`. `trace_id`/`span_id` come from the active OTel span when available; non-request startup logs MAY have `trace_id: null` but MUST remain valid JSON.

### D7 — Pool + cache exposure (with no-leak and no-fake-counter semantics)
`GET /api/v1/telemetry/pool`: pool size, checked-out, overflow (SQLAlchemy `engine.pool.status()`) + concurrency max, **concurrency in-use**, configured wait timeout (in-use derived from semaphore value). `GET /api/v1/telemetry/cache`: hits, misses, entries, ttl (add counters to `app/cache.py` where absent). Both skip the limiter (like `/health`). Two guaranteed behaviors, both tested: (a) after a 503-rejected storm drains, concurrency in-use returns to 0; (b) after a DB-timeout 504 drains, in-use returns to 0 and checked-out returns to baseline. Cache counters move only on cached-path outcomes — a 404 read increments neither hit nor miss.

### D8 — ROI model (exact, runbook-driven, auditable)
Each runbook declares frontmatter `manual_triage_base_minutes: <integer>` — starters: `db_latency` 90, `pool_exhaustion` 45, `cache_staleness` 120, `default_fallback` 180. Missing metadata → base falls back to **180**, no exception raised, fallback recorded in the breakdown. **No single hardcoded universal baseline**; bases are knowledge-base data, operator-editable at any time (takes effect on the next incident, no code change).

Exact formulas:
```
manual_estimate_minutes = clamp(
  runbook_base_minutes
  + 15 * max(0, distinct_error_signatures - 1)
  + 20 * max(0, distinct_services - 1)
  + 60 * (1 - confidence),
  5, 480
)

tool_time_minutes = (openwindow incident created_at - error event timestamp) / 60 s
                    # created_at taken from the openwindow ingest response

time_saved_minutes = manual_estimate_minutes - tool_time_minutes
```
If ingestion fails or no usable `created_at` is available: `tool_time_minutes = null` and `time_saved_minutes = null`.

`roi` object (min shape): `{"runbook_class", "manual_estimate_minutes", "tool_time_minutes", "time_saved_minutes", "breakdown"}`. `breakdown` is an array of `{item, label, minutes}` objects; count-based adjustments also carry the relevant `count` (e.g. `{"item": "extra_error_signatures", "label": "distinct error signatures above first", "count": 2, "minutes": 15}`). The breakdown MUST be sufficient for dashboard + certification report to show: matched runbook class, runbook base, each adjustment, measured tool time, final time saved.
*Alternatives:* uniform 2h baseline (rejected by operator); LLM-estimated manual time (rejected — LLM-vs-LLM comparison is not auditable).

### D9 — RCA payload shapes (strict)
Citations: `{"trace_id": "string | null", "span_id": "string | null", "source": "string", "snippet": "string"}` — requires a `source` field and nullable ids (schema update to `app/schemas/rca.py`; existing green tests adjusted where the shape narrows, kept green). At least one citation in all cases; LLM fallback citation: `{"trace_id": "<original-or-synthetic>", "span_id": null, "source": "llm_unavailable", "snippet": "LLM endpoint unavailable; manual review required"}`. `dry_run_payload`: `{"action", "target_resource", "params", "fallback"}`. Non-whitelisted LLM action → action replaced with `manual_review`, `fallback: true`, unauthorized value never returned. Malformed LLM JSON / schema-invalid output → treated as LLM unavailable → fallback RCA, HTTP 200.

### D10 — openwindow additions
Single additive migration: `Incident.raw_log` (Text, null) + `Incident.roi` (JSON, null). Additive `GET /health/` → 200 when ready (harness readiness). Ingest view remains contract-compatible; its successful response MUST additively include `id`, `created_at`, `duplicate` (false for new; true for duplicate-suppressed with the existing id and its creation timestamp when available). New additive `GET /dashboard/data/` → `{counts, totals: {time_saved_minutes}, incidents: [...]}`; MUST treat `roi = null` as contributing 0 to the cumulative total and MUST NOT 500. Existing ingest/approve request contracts unchanged.

### D11 — Concurrency-test correctness
The 7-test concurrency suite passes standalone (verified 2026-08-14) but is timing-sensitive. Correctness work: (a) run the full master suite 3× back-to-back and fix any flake at root cause; (b) new pool-telemetry assertions: rejected 503 storm drains to in-use 0 (D7), and the DB-timeout 504 path drains to baseline (D5). Definition of "correct": deterministic green under load, not just standalone.
*Alternative:* loosen exact-count asserts to ranges (rejected — masks real regressions; stabilize delay/timeout deltas instead).

### D12 — No-mock live harness
New root workspace member `integration/`:
- Session fixture spawns real subprocesses with explicit env + waits on **health endpoints (30s each)**: person-api :8002 `GET /health` (`DATABASE_URL=postgresql+asyncpg://localhost:5432/person_api`, `SENTINEL_URL=http://127.0.0.1:8001`); sentinel :8001 `GET /health` (`QWEN_BASE_URL=http://127.0.0.1:8000/v1`, `QWEN_API_KEY=1234`, `OPENWINDOW_INGEST_URL=http://127.0.0.1:8003/api/v1/incidents`); openwindow :8003 `GET /health/` (`manage.py runserver 8003 --noreload`, `SENTINEL_CALLBACK_URL=http://127.0.0.1:8001/api/v1/execute`).
- **openwindow DB = fresh temporary PostgreSQL per session**: create unique `openwindow_test_<uuid>` in local PostgreSQL, run migrations against it, use for the harness, **drop during teardown**. Not SQLite.
- **Forbidden constructs** (suite-wide): `httpx.MockTransport`, `ASGITransport`, in-process FastAPI/Django test clients for cross-service tests, monkeypatching service internals, fake DB drivers, fake LLM servers, stubbed subprocesses.
- Readiness failure → abort session with a message naming the service and port; teardown stops all child processes.
- Preflight skips (preconditions, not mocks): PostgreSQL unreachable at `localhost:5432` → skip with message naming PostgreSQL + `localhost:5432`; LLM unreachable at `http://127.0.0.1:8000/v1` → skip live LLM tests with the expected address in the message.
- Incident polling: `GET /dashboard/data/` every 2s, 60s budget (absorbs real LLM latency).
- **No LLM fallback test**: the suite exercises the real LLM at :8000 only.
*Alternatives:* run everything in-process (rejected — defeats the point); containerize (rejected — no Docker); fake-LLM loopback server (rejected by operator — tests use port 8000 only).

### D13 — Scale + SLO
`scripts/load_workers.py`: target `localhost:5432/person_api`; wipe/truncate `workers`; chunked bulk insert (5,000/chunk); exactly 50,000 active + 150,000 inactive; print counts + elapsed; exit non-zero on mismatch; idempotent re-run.
`scripts/bench_latency.py`: live HTTP on :8002 over the 200K dataset; query types: PK lookup (active), PK lookup (inactive), indexed list (`status`, limit=100); **200 requests per type at c=1 and c=10**; **20 warmup requests per type/level, excluded from the SLO gate**; cold/warmup results reported separately as informational; in-process percentile math (no new dependency); outputs `reports/latency-<ts>.json` + `.md` containing: timestamp, host/environment name if available, PostgreSQL version if available, dataset counts, warmup count, concurrency level, sample count, SLO thresholds used, p50/p95/p99 per type/level.
SLO gate: lookup warm p99 < 25ms and indexed list warm p99 < 150ms, **applied at both c=1 and c=10**; thresholds **overridable via CLI flags or env** (defaults 25/150) so the negative test proves the gate can fail (set deliberately failing thresholds → assert SLO FAIL + non-zero exit → restore defaults). Gate fail → SLO FAIL → certification FAIL + non-zero exit.

### D14 — Certification runner
`scripts/certify.py` orchestrates, in order: (1) **PREFLIGHT** — PostgreSQL at `localhost:5432` and LLM at `http://127.0.0.1:8000/v1` reachable; if either is missing, PREFLIGHT is FAIL and the runner exits non-zero — **certification never silently passes** (the ordinary integration suite still skips cleanly; certification does not); (2) **TEST SUITES** — all five suites via the master runner, recording per-suite collected/passed counts; (3) **DATASET LOAD** — loader; (4) **SLO** — benchmark + gate; (5) **LIVE LOOP** — after the master suites have torn down their harness, the runner starts its **own dedicated live harness** (same shape as D12) and performs one live DB-timeout E2E injection; (6) **EVIDENCE VS RCA** — observed fault evidence (HTTP status, route/path, request id, trace id, `pg_sleep` delay duration, timeout detail when applicable) side-by-side with generated RCA (root cause, confidence, citation trace-id match, recommended action, whitelist check, ROI summary); (7) **TIME SAVED** — per-incident table (incident id, runbook class, manual estimate, measured tool time, time saved, duplicate flag when available) with cumulative total; null ROI shown as unavailable, no crash; (8) **OVERALL VERDICT** — PASS only if every gate section is green; otherwise FAIL naming the failing section(s). Writes `reports/certification-<ts>.md`; exit code = verdict; teardown stops all child processes. Report is the presentation artifact.

### D15 — Cleanup & documentation
Delete Gen-1 dead code (`ai/`, `context/`, `formatter/`, `utils/roi.py` — verified zero imports, zero tests). Fix `main.py` to drive the canonical D1 pipeline (CLI keeps working for manual RCA of a pasted log). Rewrite `services/aiops-sentinel/README.md`: local-Qwen design, pipeline diagram, runbook metadata format + how to tune ROI bases, **full API reference (`GET /health`, `POST /api/v1/analyze`, `POST /api/v1/execute`, `GET /api/v1/executions`)**. Root `README.md` gains a one-paragraph system map + the certify command.

## Risks / Trade-offs

- **LLM latency makes E2E timing fuzzy** → 60s incident-poll budget, 2s cadence; SLO measured separately from RCA latency; certification reports actual tool times (transparency, not hiding).
- **Fragility when running on a loaded laptop** (bench SLO contention; flaky concurrency asserts) → SLO gates on warm-phase percentiles over 200 samples (outlier-robust); 20 warmup requests excluded from the gate; concurrency suite asserted 3× green; benchmark reports every percentile + diagnostics so failures are explainable.
- **10-minute dedupe guard can swallow repeat E2E evidence** → fresh temporary PostgreSQL DB per harness session; distinct fault classes (503 flood vs 504 timeout) produce distinct root causes by construction; the duplicate test deliberately reuses the identical event and asserts on the analyze response's `duplicate` flag.
- **Real-Postgres dependency for previously-green suites** → unit suites stay on their existing SQLite setups (unchanged); only the new chaos live scenario, the integration suite, and certification require Postgres — the first two skip-with-message when absent, certification fails preflight by design.
- **runserver on :8003 is dev-grade** → acceptable for a localhost verification harness (documented; production serving out of scope); `--noreload` avoids double-process surprises. Temp-DB lifecycle (create/migrate/drop) adds setup time to every session — accepted for isolation.
- **Fire-and-forget means one report can be dropped** (process exit, network blip) → accepted for v1; the live suite asserts the report *did* arrive for the injected fault, so silent drops are still caught in certification.
- **Append-only in-memory execution list is lost on restart** → acceptable for v1 dry-run acknowledgment (documented); the `/api/v1/executions` contract remains stable if persistence is added later.

## Migration Plan

1. Apply tasks in order; each phase ends with its verification command green — any phase can stop without breaking earlier ones (all API changes additive; reporter env-gated off by default).
2. Rollback = revert the change's commits; DB state is disposable (drop `person_api` schema + any leftover `openwindow_test_*` databases + temp openwindow DBs). No production data involved (localhost verification environment).
3. Post-merge, `openspec archive` folds the four delta specs into `openspec/specs/` as the system's baseline behavior contracts.

## Open Questions

None. Local PostgreSQL is confirmed live (PostgreSQL 16.14 via Homebrew, connected to `localhost:5432/person_api`, `workers` table present) — person-api is already integrated with and tested against it, so this is not an environment unknown. Task 1.1 is a connectivity confirmation only; the 200K loader (Task 6.1) is expected to wipe and replace the current `workers` contents.
