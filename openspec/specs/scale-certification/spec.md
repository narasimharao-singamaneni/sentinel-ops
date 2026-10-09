# scale-certification Specification

## Purpose
Proves that person-api operates at its stated 200,000-worker scale on real PostgreSQL with measured latency SLOs, exposes its internals (request IDs, structured logs, connection pool, cache) for observability, admits a real database-timeout fault, and produces a single auditable PASS/FAIL certification report whose preflight fails — never silently passes — when required live dependencies are absent.
## Requirements
### Requirement: 200K-worker dataset
The loader SHALL target local PostgreSQL at `localhost:5432/person_api`, wipe/truncate the `workers` table before loading, insert in chunks of 5,000, and load exactly 50,000 workers with status `active` and 150,000 with status `inactive` (200,000 total). It MUST print counts and elapsed time, MUST exit non-zero if counts do not match, and MUST be idempotent when re-run.

#### Scenario: Load and verify counts
- **WHEN** the loader completes
- **THEN** it printed exactly 50,000 active / 150,000 inactive / 200,000 total with elapsed time, and a follow-up count query confirms the same numbers

#### Scenario: Re-load is idempotent
- **WHEN** the loader is run a second time
- **THEN** the counts are again exactly 50,000 active / 150,000 inactive with no duplicates accumulated

#### Scenario: Count mismatch fails the run
- **WHEN** the final database count differs from the expected 200,000
- **THEN** the loader exits with a non-zero status

### Requirement: Latency benchmark
The benchmark harness SHALL run against live person-api over real HTTP on :8002 using the 200K PostgreSQL dataset, and SHALL measure these query types: primary-key lookup of an active worker, primary-key lookup of an inactive worker, and indexed list query by status with limit=100. For each query type it SHALL execute 200 requests at concurrency 1 and at concurrency 10, perform 20 warmup requests per query type/concurrency level, and exclude warmup requests from the SLO gate. Cold/warmup results SHALL be reported separately as informational. Percentiles p50/p95/p99 SHALL be computed in-process without adding a new dependency. The harness SHALL write `reports/latency-<timestamp>.json` and `reports/latency-<timestamp>.md`.

The report MUST include enough diagnostics to explain a failure: timestamp, host/environment name if available, PostgreSQL version if available, dataset counts, warmup count, concurrency level, sample count, SLO thresholds used, and p50/p95/p99 for every query type/concurrency level.

#### Scenario: Complete latency report
- **WHEN** the benchmark completes
- **THEN** both report files contain the full diagnostics list and p50/p95/p99 for every query type at both concurrency levels, with warmup values separate from SLO-gated values

#### Scenario: Warmup is excluded from the gate
- **WHEN** a warmup request is slower than any measured request
- **THEN** the SLO gate still passes if all non-warmup percentiles are within thresholds

### Requirement: SLO gate
The SLO gate defaults SHALL be: primary-key lookup warm p99 < 25 ms and indexed list warm p99 < 150 ms. The gate MUST apply to the warm phase at both concurrency levels (1 and 10). If any required warm p99 violates its threshold, the SLO section MUST be FAIL, the overall certification verdict MUST be FAIL, and the runner MUST exit non-zero. Thresholds MUST be overridable for negative testing via CLI flags or environment variables (lookup p99 threshold, default 25 ms; indexed list p99 threshold, default 150 ms).

#### Scenario: SLO violation fails the gate
- **WHEN** a measured warm p99 exceeds its threshold
- **THEN** the SLO section is FAIL, the certification verdict is FAIL, and the runner exits non-zero

#### Scenario: Overridden thresholds prove the gate can fail
- **WHEN** the benchmark dry-run sets deliberately failing thresholds (below the observed values)
- **THEN** it reports SLO FAIL and exits non-zero, and a re-run with the default thresholds (25 ms / 150 ms) restores normal gating

### Requirement: Request ID and structured logging
Every person-api HTTP response SHALL carry an `X-Request-ID` header. If an inbound `X-Request-ID` is present it MUST be honored; otherwise a UUID4 MUST be generated. The response header MUST equal the log line's `request_id`. Every request SHALL produce exactly one structured JSON log line of the form: `{"timestamp": "string", "request_id": "string", "trace_id": "string | null", "span_id": "string | null", "method": "string", "path": "string", "status": 0, "duration_ms": 0.0}`. For request logs, `trace_id` MUST come from the active OTel span when available. Non-request startup logs MAY have `trace_id: null`, but MUST still be valid JSON when emitted by the structured logger.

#### Scenario: Header and log agree
- **WHEN** any request is made
- **THEN** the response `X-Request-ID` header equals the `request_id` of that request's JSON log line, the log line has all eight fields, and `trace_id` equals the active span's trace id

#### Scenario: Inbound request id is honored
- **WHEN** a request arrives with an inbound `X-Request-ID` header
- **THEN** the response and log line carry that same value instead of a new one

### Requirement: Connection-pool telemetry
person-api SHALL expose `GET /api/v1/telemetry/pool` returning live values for: pool size, checked-out connections, overflow connections, concurrency max, concurrency in-use, and configured wait timeout.

#### Scenario: No semaphore leak after a rejected storm
- **WHEN** a burst of requests exceeds the concurrency limit (some 503-rejected) and then drains
- **THEN** the pool telemetry reports concurrency in-use back to 0

#### Scenario: No semaphore leak after a DB-timeout 504
- **WHEN** a `/test/db-delay` request times out with 504 and completes, and the pool returns to idle
- **THEN** the pool telemetry reports concurrency in-use back to 0 and checked-out connections return to baseline

### Requirement: Cache telemetry
person-api SHALL expose `GET /api/v1/telemetry/cache` returning live values for: hits, misses, entries, and configured ttl.

#### Scenario: Hit/miss counters move with traffic
- **WHEN** a worker is created, read once (miss), and read again (hit)
- **THEN** the cache telemetry shows the hit and miss counters incremented accordingly

#### Scenario: 404 reads do not touch counters
- **WHEN** a read of a nonexistent worker returns 404
- **THEN** neither the hit counter nor the miss counter changes

### Requirement: Real database-timeout fault
person-api SHALL add a `DB_QUERY_TIMEOUT` setting (default 1.0 second) and a route `GET /test/db-delay?delay=N` that executes a real PostgreSQL `pg_sleep(N)` query through the normal database session. This route is PostgreSQL-only: under SQLite it MUST return 503 with an "engine unsupported" style message so existing unit suites stay deterministic. If `delay` is less than or equal to the DB timeout the response MUST be 200; if `delay` exceeds the DB timeout the response MUST be 504. The timeout guard MUST release the concurrency slot, the DB session/connection, and any related request state; after a 504 timeout, pool telemetry MUST return to baseline.

#### Scenario: Long query times out
- **WHEN** the route is called on a PostgreSQL setup with a sleep longer than `DB_QUERY_TIMEOUT`
- **THEN** the response is 504

#### Scenario: Short query succeeds
- **WHEN** the route is called with a sleep less than or equal to `DB_QUERY_TIMEOUT`
- **THEN** the response is 200

#### Scenario: SQLite stays deterministic
- **WHEN** the route is called on a SQLite-backed app
- **THEN** the response is 503 with an engine-unsupported message and no `pg_sleep` is executed

### Requirement: Certification runner
The certification runner SHALL: run all five suites through the master runner and record per-suite collected/passed counts; run the 200K loader; run the latency benchmark and SLO gate; run one live DB-timeout E2E injection against a **dedicated certification harness**; compare observed fault evidence with the generated RCA; produce a cumulative time-saved table from incident ROI objects; write `reports/certification-<timestamp>.md`; and exit with a code matching the verdict.

Lifecycle: the runner MUST NOT assume services remain running after the integration suite finishes. After the five suites complete and their harness tears down, the runner MUST start its own dedicated live harness for the certification live-loop section, and its teardown MUST stop all child processes.

Prerequisites: if PostgreSQL is unavailable, the PREFLIGHT section MUST FAIL and the runner MUST exit non-zero. If the LLM is unavailable at `http://127.0.0.1:8000/v1`, the PREFLIGHT section MUST FAIL and the runner MUST exit non-zero. Certification MUST NOT silently PASS when its required live dependencies are absent. (The ordinary integration suite may still skip cleanly when dependencies are absent; certification itself must fail.)

The report MUST contain the sections: PREFLIGHT, TEST SUITES, DATASET LOAD, SLO, LIVE LOOP, EVIDENCE VS RCA, TIME SAVED, OVERALL VERDICT.

The EVIDENCE VS RCA section MUST compare observed fault evidence (HTTP status, route/path, request id, trace id, `pg_sleep` delay duration, timeout detail when applicable) with generated RCA (root cause, confidence, citation trace-id match, recommended action, whitelist check, ROI summary).

The TIME SAVED section MUST include, per incident: incident id, runbook class, manual estimate, measured tool time, time saved, and the duplicate flag when available. When an incident's ROI is null it MUST be shown as unavailable and MUST NOT crash the report.

The verdict MUST be PASS only if every required gate section is green. If any section fails, the verdict MUST be FAIL, the report MUST identify the failing section(s), and the exit code MUST be non-zero.

#### Scenario: All-green run certifies
- **WHEN** PostgreSQL and the LLM are present and every gate section passes
- **THEN** the report contains all eight sections, the verdict is PASS, and the runner exits 0

#### Scenario: Missing PostgreSQL fails preflight
- **WHEN** nothing answers PostgreSQL at `localhost:5432` and the runner executes
- **THEN** the PREFLIGHT section is FAIL, the verdict is FAIL, and the runner exits non-zero

#### Scenario: Missing LLM fails preflight
- **WHEN** the LLM at `http://127.0.0.1:8000/v1` is unreachable and the runner executes
- **THEN** the PREFLIGHT section is FAIL, the verdict is FAIL, and the runner exits non-zero

#### Scenario: Any gate failure fails certification
- **WHEN** a gate section fails (e.g. one SLO breach or one missing live incident)
- **THEN** the verdict is FAIL, the report names the failing section(s), and the runner exits non-zero

#### Scenario: Null ROI in the time-saved table is safe
- **WHEN** a certification incident has a null ROI (e.g. its openwindow push failed)
- **THEN** the TIME SAVED table shows it as unavailable and the report is still written and the verdict computed

