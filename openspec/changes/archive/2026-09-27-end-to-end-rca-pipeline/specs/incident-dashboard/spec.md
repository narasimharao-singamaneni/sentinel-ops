## Purpose

The user-facing Control Desk: a clean, fast, offline-friendly dashboard on openwindow where operators review AI-generated incidents — root cause, evidence citations, confidence, recommended dry-run action, and time saved — and approve execution with explicit confirmation, plus the health and data endpoints that machines rely on.

## ADDED Requirements

### Requirement: Service health endpoint
openwindow SHALL expose `GET /health/` returning HTTP 200 when the service is ready. This endpoint is the readiness signal used by the live test harness.

#### Scenario: Health reports readiness
- **WHEN** openwindow is running
- **THEN** `GET /health/` returns 200

### Requirement: Ingest response contract
The existing ingest endpoint SHALL remain contract-compatible, and its successful response MUST additively include: `id` (string), `created_at` (string, timestamp), and `duplicate` (boolean). For a newly created incident: `id` is the new incident id, `created_at` is the creation timestamp, `duplicate` is false. For a duplicate-suppressed incident: `id` is the existing incident id, `created_at` is the existing incident's creation timestamp when available, `duplicate` is true.

#### Scenario: New incident response
- **WHEN** a client ingests a novel incident
- **THEN** the 201 response includes the new incident's `id`, its `created_at`, and `duplicate: false`

#### Scenario: Duplicate-suppressed response
- **WHEN** a client ingests an incident matching one created within the dedupe window
- **THEN** the response includes the existing incident's `id`, its `created_at` when available, and `duplicate: true`

#### Scenario: Original required-field validation unchanged
- **WHEN** a client POSTs a payload with only the original required fields (root_cause, confidence, target_resource)
- **THEN** the incident is created exactly as before, with any new optional fields empty

### Requirement: Incident display
The dashboard SHALL display every incident with: status (as a color-coded badge), root cause text, confidence as a percentage, target resource, evidence citations (trace/span ids + snippets), recommended dry-run payload, and timestamps.

#### Scenario: Live incident renders fully
- **WHEN** an incident exists in the database
- **THEN** the dashboard page shows its status badge, root cause, confidence, target resource, citations, and dry-run payload

### Requirement: Summary counts
The dashboard header SHALL show aggregate counts: total, new, approved, executed, and duplicate-ignored incidents, plus a cumulative estimated time saved across all incidents.

#### Scenario: Counts reflect current state
- **WHEN** two incidents exist (one new, one executed)
- **THEN** the header shows total 2, new 1, executed 1, and the cumulative time saved equals the sum of the incidents' non-null roi values

### Requirement: Time-saved visualization
Each incident with a roi object SHALL display its estimated time saved with an auditable breakdown: matched runbook class, reference manual base, each applied adjustment, measured tool time, and resulting time saved. The display MUST be labeled "knowledge-base baseline vs measured tool time" and MUST NOT present the estimate as a universal hardcoded fact.

#### Scenario: Breakdown is visible per incident
- **WHEN** an incident with a roi object is displayed
- **THEN** the page shows the runbook class, base, each adjustment, measured tool time, and time saved, with the baseline explicitly attributed to the runbook knowledge base

#### Scenario: Null ROI rendered without error
- **WHEN** an incident has no roi object
- **THEN** the page renders the incident normally and marks time saved as unavailable (no error, no fake value)

### Requirement: Dashboard data endpoint
The endpoint `GET /dashboard/data/` SHALL return JSON with counts and incident rows, and MUST handle incidents with `roi = null` gracefully: missing ROI MUST contribute 0 to the cumulative time saved and MUST NOT cause a 500; the endpoint MUST still return counts and incident rows.

#### Scenario: Null ROI does not break the data endpoint
- **WHEN** the database contains incidents with `roi = null` and the endpoint is called
- **THEN** the response is 200 JSON with correct counts, incident rows present, and cumulative time saved counting only the non-null roi values

### Requirement: Approve with confirmation
The approve action on a NEW incident SHALL require an explicit user confirmation before submitting. On successful Sentinel execution the UI SHALL reflect the EXECUTED state after refresh.

#### Scenario: Confirmation then execution
- **WHEN** the operator clicks approve on a NEW incident and confirms
- **THEN** the approve request is sent, and after refresh the incident shows EXECUTED (when Sentinel acknowledged execution)

#### Scenario: Cancelled confirmation submits nothing
- **WHEN** the operator clicks approve but cancels the confirmation
- **THEN** no approve request is sent and the incident remains NEW

### Requirement: Auto-refresh
The dashboard SHALL refresh incident data without a full page reload by polling `GET /dashboard/data/` at approximately 10-second intervals.

#### Scenario: New incident appears without reload
- **WHEN** a new incident is ingested while the dashboard is open
- **THEN** it appears on the dashboard within one polling interval without the user reloading the page

### Requirement: Offline-friendly assets
The dashboard SHALL use no external assets (no CDN, no external fonts, no external scripts) — all CSS and JavaScript MUST be served locally.

#### Scenario: Renders without internet
- **WHEN** the dashboard is loaded in an offline browser environment
- **THEN** all styles and interactive behavior work normally
