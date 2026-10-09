## Purpose

The live root-cause-analysis pipeline: when a production-style error occurs in person-api, it is automatically reported to Sentinel, analyzed with the local LLM grounded in runbook knowledge, and delivered as a structured, cited incident to the openwindow Control Desk — with a per-incident time-saved ROI that is computed from an exact, runbook-metadata-driven, auditable model.

## ADDED Requirements

### Requirement: Sentinel health endpoint
Sentinel SHALL expose `GET /health` returning HTTP 200 when the service is ready. This endpoint is the readiness signal used by the live test harness.

#### Scenario: Health reports readiness
- **WHEN** the Sentinel service is up and accepting requests
- **THEN** `GET /health` returns 200

### Requirement: Automatic error event reporting
person-api SHALL report a structured error event to the Sentinel analyze endpoint when a request completes with a 5xx status or an unhandled exception occurs. The reporter MUST NOT report 4xx responses. The original response MUST be sent before the reporter awaits the Sentinel response; reporting MUST run as a background task after response completion and MUST NOT block, delay, alter, or fail the original response. The background reporting task MUST use a 30-second timeout. Reporting failures, timeouts, and transport errors MUST be logged and swallowed. Reporting MUST NOT retry in v1. When the Sentinel URL is not configured, reporting MUST be a complete no-op with zero outbound connections, and the API MUST behave exactly as before.

#### Scenario: 5xx response is reported after the response is sent
- **WHEN** a request to person-api completes with a 503 status and a Sentinel endpoint is configured
- **THEN** the client receives the 503 response before the reporter awaits the Sentinel response
- **AND** the error event is delivered to the analyze endpoint by the background task afterwards

#### Scenario: 4xx responses are never reported
- **WHEN** a request fails with a 4xx status (e.g. 404 or 400)
- **THEN** no error event is sent to Sentinel

#### Scenario: Reporting timeout does not affect later requests
- **WHEN** the Sentinel endpoint stops responding and the 30-second reporting timeout expires
- **THEN** the timeout is logged and swallowed, no retry is attempted, and subsequent requests are unaffected

#### Scenario: Unconfigured reporting is a no-op
- **WHEN** the Sentinel URL environment variable is unset and a request fails with 5xx
- **THEN** the API returns the 5xx response normally and zero outbound connections are attempted

### Requirement: Error event structure
Every reported error event SHALL contain: `service`, `method`, `path`, `status_code`, `timestamp`, `request_id`, `trace_id`, `span_id`, `error_detail`. All timestamps MUST be ISO-8601. For structured person-api events, `target_resource` MUST be derived as `person-api:<path>`; for raw log input without a path, the value MUST be `person-api:unknown`.

#### Scenario: Failure event carries real trace identity
- **WHEN** a request fails with a 5xx status and is reported
- **THEN** the event's `trace_id` equals the OTel trace id of that failed request, `request_id` equals the request's `X-Request-ID` response header, `timestamp` is ISO-8601, and the derived `target_resource` is `person-api:<path>`

### Requirement: Sentinel analyze endpoint
Sentinel SHALL expose `POST /api/v1/analyze` that is synchronous and accepts either a structured JSON error event or a raw log string. Invalid JSON, an empty unparseable body, or otherwise invalid analyze input MUST return a 4xx status, never a 5xx. When a structured event is missing `trace_id`, analyze MUST still succeed using a synthetic trace id, and the synthetic origin MUST be recorded in the ROI breakdown. Analyze returns HTTP 200 with exactly this shape:

```json
{
  "incident_id": "string | null",
  "ingested": true,
  "duplicate": false,
  "rca": {
    "root_cause": "string",
    "confidence": 0.0,
    "citations": [],
    "dry_run_payload": {}
  },
  "roi": {}
}
```

On successful openwindow ingestion: `incident_id` is the openwindow incident id, `ingested` is true, `duplicate` is false for a newly created incident. On openwindow duplicate suppression: `incident_id` is the existing incident id when available, `ingested` is true, `duplicate` is true. On openwindow push failure: `incident_id` is null, `ingested` is false, `duplicate` is false, the RCA is still returned, and the ROI is still returned but `tool_time_minutes` and `time_saved_minutes` are null.

#### Scenario: Error event returns complete RCA
- **WHEN** a valid error event is POSTed to analyze with the LLM reachable
- **THEN** the response is 200 with the full shape: non-empty `rca.root_cause`, `confidence` in 0.0–1.0, at least one citation, a well-formed `dry_run_payload`, and `roi`

#### Scenario: Raw log string is accepted
- **WHEN** a plain-text log line is POSTed to analyze
- **THEN** it is treated as single-event input, processed into an RCA, and `target_resource` is `person-api:unknown`

#### Scenario: Invalid input returns 4xx not 5xx
- **WHEN** a request to analyze contains invalid JSON or an empty unparseable body
- **THEN** the response status is a 4xx and the service does not return 5xx

#### Scenario: Missing trace id uses a recorded synthetic id
- **WHEN** a structured error event without `trace_id` is POSTed to analyze
- **THEN** analyze succeeds (200) using a synthetic trace id and the ROI breakdown records the synthetic origin

#### Scenario: Duplicate suppression is reported
- **WHEN** an error event identical (within the dedupe window) to a previously analyzed event is POSTed again
- **THEN** the response is 200 with `ingested: true`, `duplicate: true`, and `incident_id` equal to the existing incident id

### Requirement: RCA response shape
The RCA object SHALL use the following shapes. Citations SHALL be objects of the form `{"trace_id": "string | null", "span_id": "string | null", "source": "string", "snippet": "string"}`, with at least one citation in all cases, including LLM fallback. In the LLM fallback case the citation SHALL be of the form `{"trace_id": "<original-or-synthetic-trace-id>", "span_id": null, "source": "llm_unavailable", "snippet": "LLM endpoint unavailable; manual review required"}`. The `dry_run_payload` SHALL use the shape `{"action": "string", "target_resource": "string", "params": {}, "fallback": false}`. If the LLM proposes a non-whitelisted action: the action MUST be replaced with `manual_review`, `dry_run_payload.fallback` MUST be set to true, and the unauthorized action MUST NOT be returned. If the LLM returns malformed JSON or schema-invalid RCA output: the system MUST treat this as LLM unavailable, return the fallback RCA, and return HTTP 200 (never 5xx).

#### Scenario: Fallback RCA keeps the full citation shape
- **WHEN** the LLM endpoint is unreachable at call time
- **THEN** the RCA has at least one citation with `source: "llm_unavailable"`, `span_id: null`, a non-null `trace_id` equal to the incident's trace id, and a snippet indicating manual review is required

#### Scenario: Non-whitelisted action is replaced
- **WHEN** the LLM output proposes an action outside the remediation whitelist
- **THEN** `dry_run_payload.action` is `manual_review`, `dry_run_payload.fallback` is true, and the unauthorized action value appears nowhere in the response

#### Scenario: Malformed LLM output degrades to fallback with 200
- **WHEN** the LLM returns text containing no parseable JSON, or JSON failing the RCA schema
- **THEN** analyze returns 200 with the fallback RCA and `dry_run_payload.fallback: true`

### Requirement: Grounded root cause analysis
RCA SHALL be generated by the LLM using runbook knowledge retrieved for the error class and SHALL cite the actual trace id of the incident (the original trace id, or the recorded synthetic id). Recommended actions MUST be validated against the remediation whitelist before return.

#### Scenario: Citation references the incident trace
- **WHEN** an RCA is generated for an error event with trace id T
- **THEN** at least one citation's `trace_id` equals T

### Requirement: RCA ingestion push
On RCA completion Sentinel SHALL push the RCA to the openwindow ingest endpoint, using the ingest response's `id` and `created_at`. If the push fails, analyze SHALL still return 200 with the RCA and `ingested: false`.

#### Scenario: Successful push propagates incident identity
- **WHEN** analyze completes an RCA and openwindow accepts the incident
- **THEN** the analyze response contains openwindow's incident id, `created_at`-derived ROI `tool_time_minutes`, and `ingested: true`

#### Scenario: Ingest failure degrades ROI but not the RCA
- **WHEN** openwindow is unreachable during the push
- **THEN** analyze returns 200 with the RCA, `ingested: false`, `incident_id: null`, and an ROI object whose `tool_time_minutes` and `time_saved_minutes` are null

### Requirement: Execute and executions endpoints
Sentinel SHALL expose `POST /api/v1/execute` accepting at minimum `incident_id` and `dry_run_payload`; it MUST record the execution in an append-only in-memory list, emit a structured execution log line, and return HTTP 200 `{"status": "executed", "incident_id": "..."}`. Sentinel SHALL expose `GET /api/v1/executions` returning:

```json
{
  "executions": [
    {"incident_id": "...", "status": "executed", "timestamp": "...", "dry_run": true}
  ]
}
```

#### Scenario: Approval callback is acknowledged and recorded
- **WHEN** openwindow calls execute with an incident id and its dry-run payload
- **THEN** the response is 200 `{"status": "executed", ...}` and the execution is recorded in the append-only list

#### Scenario: Executions listing is deterministic
- **WHEN** two executions have been recorded and `GET /api/v1/executions` is called
- **THEN** the response lists both records with incident id, status, timestamp, and `dry_run: true`

### Requirement: LLM configuration
The LLM base URL, model, API key, and timeout SHALL be configurable via the environment variables `QWEN_BASE_URL`, `QWEN_MODEL`, `QWEN_API_KEY`, `QWEN_TIMEOUT`. Defaults SHALL target the local OpenAI-compatible endpoint (`http://127.0.0.1:8000/v1`) with key `not-needed` and a 15-second timeout. All verification in this change uses the real LLM at that address only.

#### Scenario: Environment overrides defaults
- **WHEN** the service starts with `QWEN_BASE_URL` and `QWEN_API_KEY` set
- **THEN** LLM calls use those values (verified by a successful call against the configured endpoint)

### Requirement: Per-incident ROI time saved
On RCA completion the analyze response and the pushed incident SHALL include an `roi` object containing at least: `{"runbook_class": "string", "manual_estimate_minutes": 0.0, "tool_time_minutes": 0.0, "time_saved_minutes": 0.0, "breakdown": []}`.

Each runbook SHALL declare frontmatter `manual_triage_base_minutes: <integer>` (starters: `db_latency` 90, `pool_exhaustion` 45, `cache_staleness` 120, `default_fallback` 180). If the matched runbook lacks this field, the base SHALL fall back to 180, MUST NOT raise, and the fallback SHALL be recorded in the ROI breakdown. There MUST NOT be a single hardcoded universal manual baseline; bases come from runbook metadata.

`manual_estimate_minutes` SHALL be computed exactly as:

```
manual_estimate_minutes = clamp(
  runbook_base_minutes
  + 15 * max(0, distinct_error_signatures - 1)
  + 20 * max(0, distinct_services - 1)
  + 60 * (1 - confidence),
  5,
  480
)
```

`tool_time_minutes` SHALL be computed as `(openwindow incident created_at − error event timestamp) / 60` seconds, using the `created_at` value returned by the openwindow ingest response. If ingestion fails or no usable `created_at` is available, `tool_time_minutes` MUST be null and `time_saved_minutes` MUST be null. Otherwise: `time_saved_minutes = manual_estimate_minutes − tool_time_minutes`.

`breakdown` SHALL be an array of objects, each containing at least `{"item": "string", "label": "string", "minutes": 0.0}`, and count-based adjustments MUST additionally include the relevant `count` (e.g. `{"item": "extra_error_signatures", "label": "distinct error signatures above first", "count": 2, "minutes": 15}`). The breakdown MUST make it possible for the dashboard and certification report to display: matched runbook class, runbook base, each adjustment, measured tool time, and final time saved.

#### Scenario: Time saved is fully auditable
- **WHEN** an incident is generated with runbook class base R, 3 distinct error signatures, and confidence C
- **THEN** the roi breakdown lists R, the signature adjustment `{"item": "extra_error_signatures", "count": 3, "minutes": 30}` (15 × (3 − 1)), the confidence verification adjustment, the final `manual_estimate_minutes` from the exact formula, the measured `tool_time_minutes`, and `time_saved_minutes = manual_estimate_minutes − tool_time_minutes`

#### Scenario: Different error classes estimate differently
- **WHEN** two incidents with different matched runbooks (e.g. pool-exhaustion base 45 vs fallback base 180) are analyzed with identical signal counts and confidence
- **THEN** their `manual_estimate_minutes` differ according to their runbooks' declared bases

#### Scenario: Missing runbook metadata falls back safely
- **WHEN** the matched runbook has no `manual_triage_base_minutes` frontmatter
- **THEN** the estimate uses base 180, no error is raised, and the breakdown records the fallback

#### Scenario: Failed ingestion nulls measured fields
- **WHEN** the openwindow push fails
- **THEN** `roi.tool_time_minutes` and `roi.time_saved_minutes` are null while `manual_estimate_minutes` and the breakdown remain present
