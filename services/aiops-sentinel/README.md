# AI Ops Sentinel

Autonomous incident analysis service. Structured error events (or raw log lines)
from any service flow through a canonical RCA pipeline against a local Qwen
LLM and Markdown runbooks, produce a validated `RCAResponse`, ingest the
incident into openwindow, and attach an auditable, runbook-metadata-driven ROI
calculation.

No external LLM API is used. The only LLM dependency is a local
OpenAI-compatible endpoint (default `http://127.0.0.1:8000/v1`).

---

## Pipeline (canonical, D1)

```
error event JSON / raw log string
        |
  normalize (app/normalizer.py)
  |   structured D3 event -> LogEvent(s) under its trace_id
  |   no trace_id -> synthetic trace id (origin recorded in ROI breakdown)
  v
Correlator (app/engine/correlator.py)
  |   group events into an IncidentContext
  v
Retriever (app/engine/retriever.py)
  |   score runbooks/ against the incident, return (context, class, metadata)
  v
Orchestrator (app/engine/orchestrator.py)
  |   grounded LLM prompt -> RCAResponse (root_cause, confidence, citations,
  |   dry_run_payload); action whitelist-checked against {flush_cache,
  |   reset_pool}; LLM down / malformed JSON -> fallback RCA (source:
  |   "llm_unavailable"), never a 5xx
  v
RCAResponse + ROI (app/roi.py)
  v
POST OPENWINDOW_INGEST_URL  (best-effort; failure degrades gracefully:
  incident_id null, ingested false, RCA intact, measured ROI fields null)
```

---

## HTTP API

Runs on port 8001 in the standard stack (`uvicorn app.service:app`).

### `GET /health`

Readiness probe used by the live harness/certification.

```json
{"status": "healthy", "service": "aiops-sentinel"}
```

### `POST /api/v1/analyze`

Synchronous. Body is either a structured error event JSON object or a raw log
string. Invalid JSON / empty unparseable body → 4xx (never 5xx).

Structured event shape (ISO-8601 `timestamp`):

```json
{
  "service": "person-api",
  "method": "GET",
  "path": "/api/v1/active/workers",
  "status_code": 503,
  "timestamp": "2026-08-15T10:00:00Z",
  "request_id": "0b1c2d3e-...",
  "trace_id": "abc123...",
  "span_id": "def456...",
  "error_detail": "Too many concurrent requests."
}
```

Response 200:

```json
{
  "incident_id": "42",
  "ingested": true,
  "duplicate": false,
  "rca": {
    "root_cause": "Concurrency limit saturated by burst traffic",
    "confidence": 0.8,
    "citations": [
      {"trace_id": "abc123...", "span_id": "def456...", "source": "runbook:pool_exhaustion.md", "snippet": "..."}
    ],
    "dry_run_payload": {
      "action": "reset_pool",
      "target_resource": "person-api:/api/v1/active/workers",
      "params": {},
      "fallback": false
    }
  },
  "roi": {
    "runbook_class": "pool_exhaustion",
    "manual_estimate_minutes": 105.0,
    "tool_time_minutes": 0.01,
    "time_saved_minutes": 104.99,
    "breakdown": [
      {"item": "runbook_base", "label": "...", "minutes": 45},
      {"item": "extra_error_signatures", "label": "...", "count": 1, "minutes": 0},
      {"item": "cross_service_correlation", "label": "...", "count": 1, "minutes": 0},
      {"item": "verification_factor", "label": "...", "confidence": 0.8, "minutes": 12.0}
    ]
  }
}
```

Ingest-side behavior:

- openwindow already has this evidence → same response with the existing
  `incident_id`, `ingested: true`, `duplicate: true`.
- openwindow unreachable → `incident_id: null`, `ingested: false`,
  `duplicate: false`, RCA intact, ROI `tool_time_minutes` /
  `time_saved_minutes` null (manual estimate and breakdown still present).
- Event without `trace_id` → 200 with a synthetic trace id; the synthetic
  origin is recorded as a `synthetic_trace_id` item in `roi.breakdown`.

### `POST /api/v1/execute`

Dry-run execution acknowledgment. Requires `incident_id` (+ optional
`dry_run_payload`). Appends an in-memory record and emits a structured
execution log line.

```json
{"incident_id": "42", "dry_run_payload": {"action": "reset_pool", "target_resource": "...", "params": {}, "fallback": false}}
```

Response 200: `{"status": "executed", "incident_id": "42"}`

### `GET /api/v1/executions`

Lists recorded executions (additive view of the in-memory log):

```json
{"executions": [{"incident_id": "42", "status": "executed", "timestamp": "2026-08-15T10:00:05Z", "dry_run": true}]}
```

---

## Runbooks & ROI tuning

Runbooks live in `runbooks/*.md` with a YAML frontmatter field:

| Runbook | `manual_triage_base_minutes` |
|---|---|
| `pool_exhaustion.md` | 45 |
| `db_latency.md` | 90 |
| `cache_staleness.md` | 120 |
| `default_fallback.md` | 180 |

The ROI model (`app/roi.py`) is exact and auditable:

```
manual_estimate_minutes = clamp(
    runbook_base_minutes                          # from frontmatter; missing -> 180 (recorded in breakdown)
  + 15 * max(0, distinct_error_signatures - 1)
  + 20 * max(0, distinct_services - 1)
  + 60 * (1 - confidence),
  5, 480)

tool_time_minutes   = (openwindow created_at - error event timestamp) / 60s
time_saved_minutes  = manual_estimate_minutes - tool_time_minutes
```

Tuning levers:

- **Per-class base effort** — edit `manual_triage_base_minutes` in the
  runbook's frontmatter. Missing/invalid → base 180 with the fallback
  recorded in `roi.breakdown` (no exception).
- **Adjustment constants** — the `15`/`20`/`60` coefficients and the `5`/`480`
  clamp bounds in `app/roi.py`.
- **New runbook class** — add `runbooks/<class>.md` with the frontmatter field;
  the retriever picks it up with no code change.

Every estimate ships with a `breakdown[]` of `{item, label, minutes}` items
(`count` on the adjustment items) that sums to the estimate, so dashboard
time-saved cards can render the arithmetic.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `QWEN_BASE_URL` | `http://127.0.0.1:8000/v1` | Local LLM OpenAI-compatible endpoint |
| `QWEN_MODEL` | `qwen2.5-7b` | Model name passed to the LLM |
| `QWEN_API_KEY` | `not-needed` | API key (any non-empty value) |
| `QWEN_TIMEOUT` | `15.0` | LLM request timeout (seconds) |
| `QWEN_TEMPERATURE` | `0.3` | Sampling temperature. `0` = greedy decoding (deterministic RCA text, used by verification) |
| `QWEN_DISABLE_THINKING` | off | When set truthy, sends `chat_template_kwargs={"enable_thinking": false}` — required for thinking-by-default Qwen3 servers, where enabled thinking adds minutes per call |
| `OPENWINDOW_INGEST_URL` | `http://127.0.0.1:8003/api/v1/incidents` | Incident ingest target |

If the LLM is unreachable or returns unparseable output, all paths degrade to
the fallback RCA (`root_cause: "manual_review_required"`, citation source
`llm_unavailable`) — the service never 5xx's on LLM failure.

---

## Running

HTTP service (port 8001 in the standard stack):

```bash
uvicorn app.service:app --host 0.0.0.0 --port 8001
```

CLI (canonical pipeline over pasted log lines; prints one JSON RCA per line —
LLM-down lines yield the fallback, no crash):

```bash
uv run python main.py
```

Tests (all against real service code; LLM stubbed only at the client boundary
in unit tests):

```bash
uv run pytest tests/ -v --tb=short
```

---

## Layout

```
app/
  core/llm.py            QwenClient (local OpenAI-compatible endpoint)
  normalizer.py          D3 error event / raw log -> LogEvent(s)
  roi.py                 exact runbook-metadata-driven ROI model
  schemas/rca.py         RCAResponse, citations, dry_run_payload contracts
  engine/correlator.py   IncidentContext
  engine/retriever.py    runbook retrieval + metadata parsing
  engine/orchestrator.py grounded LLM RCA + action whitelist
  service.py             FastAPI app (the four endpoints above)
runbooks/                Markdown runbooks with manual_triage_base_minutes
tests/                   unit + integration tests
main.py                  CLI
```
