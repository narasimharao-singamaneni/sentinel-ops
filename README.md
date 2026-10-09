# Sentinel-Ops

End-to-end, no-mock AIOps loop: a real fault in a real service is reported up, root-caused by a real local LLM against a local runbook knowledge base, surfaced on a real dashboard with a measured ROI, and — once approved — executed back through the system. Everything runs on this machine: uv only, no Docker, no mocks in any verification code, and the real LLM at `127.0.0.1:8000/v1` is the only LLM used (no fallback/fake-LLM verification paths).

## System map

| Component | Port | Role |
|---|---|---|
| LLM (operator-run server) | `8000` | Qwen model at `http://127.0.0.1:8000/v1` (OpenAI-compatible); the only LLM in any verification path |
| PostgreSQL (Homebrew) | `5432` | `person_api` database with the `workers` table (200K rows after the loader); test suites get unique temporary `openwindow_test_<uuid>` databases, migrated before use and dropped on teardown |
| aiops-sentinel | `8001` | `GET /health`, `POST /api/v1/analyze`, `POST /api/v1/execute`, `GET /api/v1/executions` |
| person-api | `8002` | FastAPI worker service with X-Request-ID middleware, structured JSON logging (real OTel trace/span ids), pool + cache telemetry, DB query timeout guard, `/test/db-delay` chaos route, and the 5xx-only Sentinel error reporter |
| openwindow | `8003` | Django incident store + dashboard: `GET /health/`, `POST /api/v1/incidents`, `GET /dashboard/`, `GET /dashboard/data/`, incident approve |

Data flow: person-api emits one structured JSON log line per request (`request_id`/`trace_id` from the live OTel span). On **5xx only** (never 4xx), the error reporter sends the original response first, then a background task POSTs the error event to Sentinel `POST /api/v1/analyze` (30s timeout, failures logged and swallowed, no retries in v1; unset `SENTINEL_URL` = complete no-op). Sentinel correlates the event's trace against its runbook knowledge base, asks the local LLM for a strictly-validated RCA (root cause, confidence, evidence citations with trace ids, whitelisted dry-run action), computes the ROI (knowledge-base baseline manual estimate vs measured tool time), and ingests the incident into openwindow with duplicate suppression. The dashboard (`/dashboard/`, 10s polling of `/dashboard/data/`) shows each incident with its citations, dry-run payload, and a time-saved card (runbook class, base + adjustments, measured tool time, saved; null ROI renders as unavailable). Approving an incident calls Sentinel `POST /api/v1/execute`, which records the execution (visible in `GET /api/v1/executions`) and the incident becomes `EXECUTED`.

## Repository layout

```
services/person-api/       the instrumented worker service (FastAPI)
services/aiops-sentinel/   the AIOps engine + HTTP service (correlator, retriever, orchestrator, ROI)
apps/openwindow/           incident store + dashboard (Django)
chaos/                     chaos harness — real failure scenarios against the stack
integration/               no-mock live E2E suite (spawns real services, real Postgres, real LLM)
scripts/                   run_all_tests.py, launch_sentinel_ops.sh, kill_sentinel_ops.sh, load_workers.py, bench_latency.py, certify.py
reports/                   baseline, latency benchmark, and certification reports
openspec/                  change proposals/specs for this system
```

## Quickstart

One uv environment for the whole repo: a single `pyproject.toml`, a single `uv.lock`, and a single `.venv` at the repo root. All five suites (person-api, aiops-sentinel, openwindow, chaos, integration) share that env; each member directory keeps a `pytest.ini` with its own suite config, so `uv run pytest` works from the root or from inside a member directory alike.

Prerequisites (no installs are performed by this repo):

1. Local PostgreSQL on `localhost:5432` with a `person_api` database and `workers` table (Homebrew `postgresql@16` is the reference setup; see `reports/baseline.md`).
2. A real LLM server on `http://127.0.0.1:8000/v1` (OpenAI-compatible). The live-harness model pin in `integration/conftest.py` must be an id your server advertises (current pin: `Qwen3.8-27B-oQ4e-mtp`).

Then, from the repo root:

```sh
uv sync                                    # one env for all five suites
uv run python scripts/run_all_tests.py     # all five suites
uv run python scripts/certify.py           # end-gate certification (D14)
```

Individual suites: `uv run pytest tests/ -v` from inside each member directory. The integration suite needs the live LLM + Postgres (see below) and takes ~2 minutes.

## Running the whole stack

```sh
./scripts/launch_sentinel_ops.sh   # person-api :8002, sentinel :8001, openwindow :8003
./scripts/kill_sentinel_ops.sh     # stops all three; ports 8001–8003 freed
```

The launch script starts each service with the canonical env (Postgres URL, `SENTINEL_URL`, the LLM endpoint, ingest/callback URLs), waits for a 200 from each health endpoint before starting the next, and leaves logs + pids in `/tmp/sentinel-ops/`. Health probes: `http://127.0.0.1:8002/health`, `http://127.0.0.1:8001/health`, `http://127.0.0.1:8003/health/`.

## openwindow dev commands

The `openwindow` package is installed into the shared env by `uv sync`, from the repo root:

```sh
uv run openwindow          # the monitoring/execution loop (runner/loop_runner.py)
uv run openwindow-api      # the API runner (runner/api_runner.py; serves openwindow.api.app via uvicorn)
```

Django dashboard server:

```sh
cd apps/openwindow
DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost \
  uv run python manage.py runserver 8003 --noreload
```

## Certification and the SLO gate

`scripts/certify.py` runs, in order: **PREFLIGHT → TEST SUITES → DATASET LOAD (200K) → SLO → LIVE LOOP (its own D12 harness + one live DB-timeout injection) → EVIDENCE VS RCA → TIME SAVED → OVERALL VERDICT**, writes `reports/certification-<ts>.md`, and exits with the verdict. SLO thresholds default to lookup warm p99 < 25 ms and indexed-list warm p99 < 150 ms (overridable: `--lookup-p99-ms`, `--list-p99-ms`, env `SLO_LOOKUP_P99_MS` / `SLO_LIST_P99_MS`).

Dependency behavior — the two modes are different by design:

- **Integration suite** skips cleanly when a dependency is absent, naming the exact address in the skip reason (PostgreSQL at `localhost:5432`; LLM at `http://127.0.0.1:8000/v1`).
- **Certification never silently passes**: if PostgreSQL or the LLM is unreachable, PREFLIGHT is FAIL, the runner exits non-zero immediately, and the report marks every later section `NOT RUN` (failed preflight behavior is part of the documented contract, including with the LLM stopped).

## Reports layout

```
reports/baseline.md                 pre-implementation baseline (DB + suite counts)
reports/run{1,2,3}_master.log       baseline stability runs (3x full master suite)
reports/latency-<ts>.json|.md       bench_latency.py output: percentile tables, SLO checks, verdict
reports/certification-<ts>.md       certify.py output: all eight sections + overall verdict
```

## Notes

- The chaos suite's live `db-timeout` tests are precondition-gated on a live person-api at `http://127.0.0.1:8002` backed by PostgreSQL and skip otherwise (in-process chaos scenarios always run).
- The 200K loader (`scripts/load_workers.py`) wipes and reloads `workers`; it is idempotent and self-checks its counts.
- Each member directory has its own README with API/scheme detail; `openspec/changes/` holds the full design (D1–D14).

## License

MIT — file to be added.
