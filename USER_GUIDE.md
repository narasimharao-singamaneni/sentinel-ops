# Sentinel-Ops — User Guide

How to run the system yourself and drive every loop in it: real fault → report → LLM root-cause → dashboard with ROI → approval → execution record. No mocks anywhere in this guide; every command below hits real HTTP, real PostgreSQL, and the real LLM at `127.0.0.1:8000`.

Reference for the architecture: `README.md`. This guide is operator-oriented: what to run, what to expect, and what to check afterwards.

## 0. What you're going to see

1. person-api (:8002) fails a request (504 or 503) against real PostgreSQL.
2. Its error reporter (5xx only) POSTs the event — with the real OTel trace id — to Sentinel `POST /api/v1/analyze` on :8001.
3. Sentinel matches a runbook, asks the local LLM, and gets back a structured RCA: root cause, confidence, evidence citations (trace ids), a whitelisted dry-run action — plus a measured ROI (manual-estimate baseline vs tool time actually spent).
4. Sentinel ingests it into openwindow (:8003); the dashboard `http://127.0.0.1:8003/dashboard/` shows it within ~10 s (it polls every 10 s).
5. You approve the incident; openwindow calls Sentinel `POST /api/v1/execute`, the incident flips to EXECUTED, and the execution is listed in `http://127.0.0.1:8001/api/v1/executions`.

## 1. Prerequisites

- `uv` installed; repo dependencies installed: `uv sync` (repo root — one environment for all five suites).
- PostgreSQL on `localhost:5432` (reference: Homebrew `postgresql@16`), with database `person_api` and a `workers` table. If you don't have the data yet:
  ```sh
  uv run python scripts/load_workers.py   # from repo root — wipes + loads 50,000 active / 150,000 inactive
  ```
  (Loader is idempotent; it only touches the `workers` table.)
- A real OpenAI-compatible LLM serving on `http://127.0.0.1:8000/v1` (reference key `1234`). Know the exact model id it advertises: `curl -s http://127.0.0.1:8000/v1/models -H "Authorization: Bearer 1234"`. You'll pin that id below; unknown ids get a 404 from the server.

## 2. Fastest way to verify the whole loop (no manual setup)

```sh
cd integration && uv run pytest tests/ -v --tb=short
```

This spawns all three services itself, makes a **unique temporary openwindow database** in PostgreSQL (migrated, then dropped), runs the real fault→RCA→dashboard→approval loops, and tears everything down. Takes ~2 minutes. If deps are missing it skips cleanly and names the exact address (`localhost:5432`, `http://127.0.0.1:8000/v1`).

Everything below is the same loop, but with your hands on the wheel.

## 3. Starting the stack manually

Do these in separate terminals (or background them with logs — the log location matters for step 3 of the scenarios):

**a) person-api (:8002)**
```sh
cd services/person-api
DATABASE_URL="postgresql+asyncpg://localhost:5432/person_api" \
SENTINEL_URL="http://127.0.0.1:8001" \
uv run python -m uvicorn app.main:app --port 8002 > /tmp/person-api.log 2>&1 &
```
`SENTINEL_URL` is the switch that arms the error reporter; unset = the reporter is a complete no-op (zero outbound connections).

**b) aiops-sentinel (:8001)**
```sh
cd services/aiops-sentinel
QWEN_BASE_URL="http://127.0.0.1:8000/v1" \
QWEN_API_KEY="1234" \
QWEN_MODEL="<model id your server advertises>" \
QWEN_TIMEOUT="90" \
QWEN_TEMPERATURE="0" \
QWEN_DISABLE_THINKING="1" \
uv run python -m uvicorn app.service:app --port 8001 > /tmp/sentinel.log 2>&1 &
```
`QWEN_TIMEOUT=90` gives a big local model headroom (default is 15 s); `QWEN_TEMPERATURE=0` = greedy decoding (identical events → identical RCA text, which is what makes openwindow's duplicate suppression deterministic); `QWEN_DISABLE_THINKING=1` keeps single-call latency in the seconds, not minutes. The default ingest target is already `http://127.0.0.1:8003/api/v1/incidents` — set `OPENWINDOW_INGEST_URL` only to point elsewhere.

**c) openwindow (:8003)**
```sh
cd apps/openwindow
uv run python manage.py migrate
DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost uv run python manage.py runserver 8003 --noreload > /tmp/openwindow.log 2>&1 &
```
By default it persists incidents in local SQLite (`apps/openwindow/openwindow/db.sqlite3`); set `DJANGO_DB_URL="postgresql://<user>@localhost:5432/<db>"` to use Postgres. The approve callback default (`http://localhost:8001/api/v1/execute`) already matches Sentinel; override with `SENTINEL_CALLBACK_URL` if you moved ports.

**Health checks** (each is a readiness probe):
```sh
curl -s http://127.0.0.1:8002/health     # {"status":"healthy",...}
curl -s http://127.0.0.1:8001/health
curl -s http://127.0.0.1:8003/health/
```

Dashboard: open **http://127.0.0.1:8003/dashboard/**.

## 4. Scenario A — real DB timeout (the showcase)

person-api has a DB query timeout guard (`DB_QUERY_TIMEOUT`, default **1.0 s**). Hitting the delay route with a longer sleep makes a real query breach it:

```sh
curl -s -i "http://127.0.0.1:8002/test/db-delay?delay=5"
```

**Expect:** `504` whose body carries the timeout signature (e.g. `database query timeout after 1.0s (pg_sleep 5.0s exceeded DB_QUERY_TIMEOUT)`), and an `X-Request-ID` response header — write that id down.

Then watch the dashboard (or poll `curl -s http://127.0.0.1:8003/dashboard/data/ | python3 -m json.tool`): a NEW incident for target resource `person-api:/test/db-delay` appears within seconds. Check the JSON row:

- `root_cause` — the LLM's explanation (anything other than the literal text `manual_review_required` means the real LLM ran; that literal string means the LLM was unreachable and the fallback RCA was used — see troubleshooting).
- `confidence` — 0..1.
- `citations[].trace_id` — the **real trace id** of the 504. Verify the match against person-api's log: `grep <your X-Request-ID> /tmp/person-api.log` — the JSON line's `trace_id` (32 hex chars) equals the citation trace id.
- `dry_run_payload.action` — one of `flush_cache`, `reset_pool`, `manual_review` (whitelist enforced by Sentinel; non-whitelisted actions are replaced with `manual_review` + `fallback: true`).
- `roi` — runbook class, manual estimate breakdown, **measured** tool time, and time saved.

**Control (no false positives):** `curl -s -i "http://127.0.0.1:8002/test/db-delay?delay=0.3"` → 200 → **no incident** is created for it.

**Pool sanity after the 504:** `curl -s http://127.0.0.1:8002/api/v1/telemetry/pool` → `concurrency_in_use` back to 0 (no reservation leak on the timeout path).

## 5. Scenario B — pool exhaustion (503 storm)

person-api admits at most `MAX_CONCURRENT` (10) requests concurrently; requests beyond that get a real `503` — which the reporter ships. The tested E2E does this deterministically in three moves; do the same:

```sh
# 1. Create the worker the flood will hit
curl -s -X POST http://127.0.0.1:8002/api/v1/active/workers \
  -H 'content-type: application/json' -d '{"name":"manual-pool-test","status":"active"}'
# → 201, note the "id" value = <worker-id>

# 2. Occupy all 10 concurrency slots with slow (but healthy, error-free) requests
for i in $(seq 10); do
  curl -s "http://127.0.0.1:8002/test/delay?delay=8" > /dev/null &
done

# 3. ~2 s later, fire 12 real lookups concurrently — the limit is fully saturated
sleep 2
for i in $(seq 12); do
  curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8002/api/v1/active/workers/<worker-id>" &
done | sort | uniq -c
sleep 8   # let the slot-holders drain
```

**Expect a wall of `503`s** (with the slots saturated, nearly every flood lookup is rejected; the tested E2E asserts at least one). Each 503 carries its own `X-Request-ID` and reaches the reporter. Then the dashboard shows a NEW incident for target resource `person-api:/api/v1/active/workers/<worker-id>`: pool-exhaustion runbook class, LLM-named root cause, and — with the slots saturated — the dry-run action typically `reset_pool`. If several 503s land before the first RCA completes, they may coalesce via dedupe (identical evidence under greedy decoding) — expect one incident for the storm, not one per 503.

Pool sanity afterwards: `curl -s http://127.0.0.1:8002/api/v1/telemetry/pool` → `concurrency_in_use` back to 0.

**Control:** 10-or-fewer concurrent requests produce **no** incidents. 4xx responses (e.g. `curl /api/v1/active/workers/nonexistent` → 404) are **never** reported — that's by design.

## 6. Scenario C — approve an incident to execution

1. Get the incident id: `curl -s http://127.0.0.1:8003/dashboard/data/ | python3 -m json.tool` → `incidents[].id` (status `NEW`).
2. Approve (or click the Approve button on the dashboard page — it asks for confirmation):
   ```sh
   curl -s -X POST "http://127.0.0.1:8003/incidents/<id>/approve/"
   ```
3. **Expect:** 200; the incident is now `EXECUTED` on the dashboard and in `/dashboard/data/`.
4. Proof of execution (deterministic, not log scraping):
   ```sh
   curl -s http://127.0.0.1:8001/api/v1/executions | python3 -m json.tool
   ```
   → a record for that incident id with `status: "executed"` and `dry_run: true`.
5. Idempotency guard: approving the same incident again returns **400** and no second execution record appears.

## 7. Scenario D — talk to Sentinel directly (analyze + duplicates)

You can feed an error event straight into Sentinel — the same shape the reporter sends:

```sh
curl -s -X POST http://127.0.0.1:8001/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"service":"person-api","method":"GET","path":"/api/v1/active/workers?limit=100","status_code":503,"timestamp":"2026-08-21T10:00:00Z","request_id":"manual-test-1","trace_id":"0000000000000000000000000000dead","span_id":null,"error_detail":"Too Many Requests: concurrency limit exceeded"}' \
  | python3 -m json.tool
```

**Expect:** `200` with `incident_id`, `ingested: true`, `duplicate: false`, a full `rca` (root cause / confidence / citations / dry_run_payload) and an `roi` object. A raw log string in place of the JSON object is also accepted.

Send the **exact same body again** → `ingested: true`, `duplicate: true`, and the **same** `incident_id` (openwindow dedupes identical evidence). Malformed/empty JSON bodies get a 4xx — analyze never returns 5xx for bad input.

## 8. Telemetry endpoints (person-api)

```sh
curl -s http://127.0.0.1:8002/api/v1/telemetry/pool    # pool size/checked-out/overflow, concurrency max/in-use, wait timeout
curl -s http://127.0.0.1:8002/api/v1/telemetry/cache   # hits, misses, entries, ttl
```

Pool: drive the fault scenarios above and confirm `concurrency_in_use` returns to 0 after everything drains (that's the "no leak" property the tests assert). Cache: create a worker, read it twice — second read is a hit; 404 reads move neither counter.

## 9. Verification battery (what CI-style runs do)

From the repo root:

```sh
uv run python scripts/run_all_tests.py    # 5 suites: person-api, aiops-sentinel, openwindow, chaos, integration (live)
uv run python scripts/certify.py          # end-gate: PREFLIGHT → 5 SUITES → 200K LOAD → SLO → LIVE LOOP → EVIDENCE VS RCA → TIME SAVED → VERDICT
```

- `run_all_tests.py` — integration suite skips cleanly (with the address named) when the LLM/Postgres are absent; other precondition-gated chaos tests skip without a live person-api on :8002.
- `certify.py` — writes `reports/certification-<ts>.md` and **exits with the verdict**. It never silently passes: if PostgreSQL or the LLM is unreachable, `PREFLIGHT` is FAIL and it exits non-zero immediately (later sections are marked NOT RUN — that failure behavior is part of the contract; try it with the LLM stopped).
- SLO gate defaults: lookup warm p99 < 25 ms, indexed list warm p99 < 150 ms, overridable via `--lookup-p99-ms` / `--list-p99-ms` (or `SLO_LOOKUP_P99_MS` / `SLO_LIST_P99_MS`). Note: the gate measures client-observed HTTP latency; on a heavily loaded machine, 1–2 requests per concurrency-10 phase can be inflated by OS scheduling. If a lookup cell barely misses, check person-api's own log lines for that window (`duration_ms` in the JSON log gives the app-side truth) before concluding the service is slow.

## 10. Resetting and stopping

```sh
# stop what you started (tune the pkill patterns to however you launched each service)
pkill -f "uvicorn app.main:app"     # person-api
pkill -f "uvicorn app.service:app"  # sentinel
pkill -f "manage.py runserver"      # openwindow

# fresh openwindow state (SQLite mode): delete + re-migrate
rm apps/openwindow/openwindow/db.sqlite3
(cd apps/openwindow && uv run python manage.py migrate)

# person_api data: re-load the dataset any time
uv run python scripts/load_workers.py
```

The automated harnesses (integration tests, certify) always use their own temporary `openwindow_test_<uuid>` databases — dropped on teardown; if one ever survives a crash, `DROP DATABASE openwindow_test_<uuid>;`.

## 11. Troubleshooting

| Symptom | Likely cause / check |
|---|---|
| No incident appears after a 504/503 | Is `SENTINEL_URL` set on person-api? (unset = no reporting, by design). Is Sentinel up (`curl :8001/health`)? The reporter has a 30 s budget and swallows failures — check `/tmp/person-api.log` for the report attempt. |
| Incident's `root_cause` is literally `manual_review_required` | Real LLM path failed (unreachable/timeout/bad model id). Check `QWEN_MODEL` against `curl :8000/v1/models`; check `QWEN_TIMEOUT` is big enough for your model; the citation will say `source: "llm_unavailable"`. |
| LLM calls 401/404 | Key: `QWEN_API_KEY` (reference server: `1234`). 404 = model id not advertised by the server. |
| Approve returns 400 | Only NEW incidents approve; already-processed incidents are rejected (expected). |
| Person-api 503 on a normal request | 10 in-flight already — normal limiter behavior; check `/api/v1/telemetry/pool`. |
| `certify.py` PREFLIGHT FAIL | One of the canonical dependencies is down — `localhost:5432` or `http://127.0.0.1:8000/v1`. That exit is correct behavior, not a bug. |
| `certify.py` SLO FAIL, app looks fast | The gate measures client-observed HTTP latency; on a busy machine, 1–2 outliers per concurrency-10 cell can trip it. Check app-side `duration_ms` in `/tmp/person-api.log` for that window — small app times = machine contention, retry when quiet. |
| Port already in use | `lsof -nP -iTCP:8001 -iTCP:8002 -iTCP:8003 -sTCP:LISTEN`; stale harness child from a killed run — kill it and drop any surviving `openwindow_test_%` database. |

## 12. Port & env quick reference

| Port | Service | Anything you must set |
|---|---|---|
| 8000 | LLM (yours) | your server's model id + key |
| 5432 | PostgreSQL | `person_api` DB + `workers` table |
| 8001 | aiops-sentinel | `QWEN_MODEL` (required in practice — default model id won't exist on your server); `OPENWINDOW_INGEST_URL` default is already right |
| 8002 | person-api | `SENTINEL_URL` to arm the error reporter; `DATABASE_URL` default is already right |
| 8003 | openwindow | `DJANGO_DB_URL` only if you want Postgres instead of local SQLite; callback default is already right |
