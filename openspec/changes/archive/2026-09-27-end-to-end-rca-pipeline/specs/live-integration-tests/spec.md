## Purpose

The no-mock verification harness: a live end-to-end test suite that proves the full error → RCA → incident → execution loop on real local services — real processes, real localhost HTTP, real PostgreSQL, and the real local LLM at `127.0.0.1:8000/v1` — so certification is based on observed system behavior rather than simulated components.

## ADDED Requirements

### Requirement: Zero mocks
The integration suite SHALL contain no mocks, stubs, fakes, or monkeypatched service behavior. The following are explicitly forbidden inside this suite: `httpx.MockTransport`, `ASGITransport`, in-process FastAPI/Django test clients for cross-service tests, monkeypatching service internals, fake DB drivers, fake LLM servers, and stubbed subprocesses. All services under test SHALL run as real processes communicating over real localhost HTTP; the database SHALL be real PostgreSQL; the LLM SHALL be the real local endpoint at port 8000. Skipping a missing external dependency is an allowed precondition; fabricating or simulating a dependency is not.

#### Scenario: No mock constructs in the suite
- **WHEN** the integration suite source is inspected
- **THEN** it contains none of the forbidden constructs, and no test patches service internals or network boundaries

### Requirement: Live harness
The suite SHALL start, as a session-scoped fixture, real subprocesses with the full wired environment configuration:
- person-api on :8002 with PostgreSQL DSN `postgresql+asyncpg://localhost:5432/person_api` and `SENTINEL_URL=http://127.0.0.1:8001`
- Sentinel on :8001 with `QWEN_BASE_URL=http://127.0.0.1:8000/v1`, `QWEN_API_KEY=1234`, `OPENWINDOW_INGEST_URL=http://127.0.0.1:8003/api/v1/incidents`
- openwindow on :8003 via `manage.py runserver 8003 --noreload` with `SENTINEL_CALLBACK_URL=http://127.0.0.1:8001/api/v1/execute`, running against a **fresh temporary PostgreSQL database per session** (not SQLite)

The temporary openwindow database SHALL be a unique local PostgreSQL database per session (e.g. `openwindow_test_<uuid>`), migrations SHALL be run against it before use, and it SHALL be dropped during teardown. The harness MUST wait for readiness using: person-api `GET /health`, Sentinel `GET /health`, openwindow `GET /health/` — a bounded 30-second wait per service. If a service fails to become healthy, the session MUST be aborted with a clear message naming the service and port. Teardown MUST stop all child processes.

#### Scenario: Services reach healthy state
- **WHEN** the harness session starts with all dependencies present
- **THEN** all three services report healthy via their respective health endpoints and migrations have run on a freshly created `openwindow_test_<uuid>` PostgreSQL database before any test executes

#### Scenario: Unhealthy service aborts with identification
- **WHEN** one service fails to become healthy within its 30-second wait
- **THEN** the session aborts with a message naming that service and its port, and all started child processes are stopped

#### Scenario: Temporary database lifecycle
- **WHEN** a harness session completes (pass or fail)
- **THEN** its `openwindow_test_<uuid>` database has been dropped from PostgreSQL

### Requirement: Dependency preconditions
An unreachable PostgreSQL at `localhost:5432` SHALL cause live tests to be skipped with a message naming PostgreSQL and `localhost:5432`. An unreachable LLM at `http://127.0.0.1:8000/v1` SHALL cause live LLM tests to be skipped with a message naming the expected LLM address. These skips are preconditions, not mocks. The LLM is exercised against port 8000 only; no fallback or fake-LLM scenario is built.

#### Scenario: PostgreSQL down skips with clear reason
- **WHEN** nothing answers PostgreSQL at `localhost:5432`
- **THEN** live tests are skipped with a message naming PostgreSQL and `localhost:5432`

#### Scenario: LLM down skips with clear reason
- **WHEN** no endpoint answers at `http://127.0.0.1:8000/v1`
- **THEN** live LLM tests are skipped with a message naming `http://127.0.0.1:8000/v1`; no test fabricates a stand-in LLM

### Requirement: End-to-end pool exhaustion loop
The suite SHALL inject a real fault by sending 11+ concurrent real HTTP requests to live person-api (exceeding its concurrency limit), MUST observe 503 rejections (capturing the request ids/trace ids of rejected requests), and SHALL then verify — purely from system output, without manual bridging — that an incident appears on openwindow within 60 seconds whose root cause is LLM-generated (not the fallback text), whose citation trace id matches the captured real trace id, and whose action is in the remediation whitelist.

#### Scenario: Real flood produces traced incident
- **WHEN** 11+ concurrent real requests are fired at live person-api producing at least one 503 response
- **THEN** polling openwindow reaches an incident within 60 seconds with non-fallback root cause, a citation trace id equal to the captured real trace id, and a whitelisted action

### Requirement: End-to-end real DB-timeout loop
The suite SHALL trigger a genuine database timeout via the real `pg_sleep` test route on live person-api (504), and SHALL verify the resulting incident exists within the polling window and that its recorded evidence contains the timeout signature (status 504 / query-timeout detail) and carries a valid roi object. After the 504 drains, person-api pool telemetry MUST show concurrency in-use back to 0 (no slot leak on the timeout path).

#### Scenario: Real 504 produces incident with evidence and no leak
- **WHEN** the db-delay route is called with a duration exceeding the DB timeout
- **THEN** person-api returns 504, an incident appears on openwindow within the polling window whose raw evidence references the 504/timeout with a valid roi object, and pool telemetry reports in-use concurrency 0 after the request completes

### Requirement: Duplicate suppression
The suite SHALL verify that two identical error events reported within the dedupe window result in exactly one tracked incident, and that the second analyze response carries the duplicate flags (`ingested: true`, `duplicate: true`, existing incident id).

#### Scenario: Same fault twice yields one incident
- **WHEN** the same error event is pushed through analyze twice within the dedupe window
- **THEN** openwindow contains one incident for that evidence and the second analyze response has `duplicate: true` with the existing incident id

### Requirement: Approve-to-execution loop
The suite SHALL approve a live NEW incident through the dashboard API and SHALL assert Sentinel execution deterministically through `GET /api/v1/executions` (an execution record for the incident id is present) — not by log parsing only — and SHALL verify the incident transitions to EXECUTED.

#### Scenario: Approval executes end-to-end and is listed
- **WHEN** the suite POSTs an approve for a live NEW incident
- **THEN** the approve response is 200, `GET /api/v1/executions` contains a record for that incident id with `status: "executed"`, and the incident status is EXECUTED

#### Scenario: Re-approval does not re-execute
- **WHEN** the suite approves the same incident twice
- **THEN** the second approve returns 400 (unchanged contract) and `GET /api/v1/executions` contains exactly one record for it

### Requirement: Dashboard live render
The suite SHALL fetch `GET /dashboard/` from live openwindow containing the seeded incident and verify the rendered page returns 200 and contains the incident's root cause text.

#### Scenario: Live HTML contains the incident
- **WHEN** an incident exists in the live openwindow database and the dashboard is fetched
- **THEN** the page is 200 HTML containing that incident's root cause text

### Requirement: Master runner registration
The integration suite SHALL be registered as a fifth suite in the master test runner with a long timeout (900 s), SHALL skip cleanly when its external dependencies are absent (keeping CI green), and the four existing unit suites SHALL remain unchanged and green.

#### Scenario: Runner includes the live suite
- **WHEN** the master runner is executed
- **THEN** it runs all five suites and reports the integration suite as pass-or-skip (never failing from configuration), with the existing four suites passing
