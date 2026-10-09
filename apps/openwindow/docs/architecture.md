# OpenWindow – Architecture

## System Components

- Configuration & State  
    - Owns durable storage of user intent, availability truth, and alert history, without initiating execution.
- Execution Engine  
    - Owns progression of the system through the defined execution flow by coordinating other components.
- Data Providers  
    - Own external availability data retrieval without interpretation or persistence.
- Context Builder  
    - Owns enrichment and aggregation of availability context without modifying system state or eligibility.
- Explanation Engine (AI)  
    - Owns transformation of established facts into human‑readable explanations only.
- Alert Delivery  
    - Owns notification transport to users without influencing correctness or execution state.

The following steps exchange structured data objects with clearly defined ownership.

---
## Execution Flow
## First Execution Flow: Availability → Alert

This document describes the exact sequence of events from availability detection to user notification.

1. The system periodically checks availability for a user‑defined campground and dates in State and National parks.  
   *(Execution Engine + Data Providers)*

2. The system compares the newly fetched availability with the last known state.  
   *(Execution Engine + Configuration & State)*

3. If nothing changed, the system records the check and exits.  
   *(Execution Engine)*

4. If a new opening is detected, the system classifies the availability transition as "OPENED".  
   *(Execution Engine)*

5. The system gathers relevant context about this opening (price, nearby availability, season).  
   *(Context Builder)*

6. The system generates a short explanation describing why this opening is notable.  
   *(Explanation Engine – AI)*

7. The system sends a single notification to the user with a booking link and a description of why this opening is notable.  
   *(Alert Delivery)*

8. The system records that this alert was sent to prevent duplicates.  
   *(Configuration & State)*

---
## Data Ownership
## Data Hand‑Offs Between Components

- Configuration & State → Execution Engine  
- Data Providers → Execution Engine  
- Execution Engine → Context Builder  
- Context Builder → Explanation Engine (AI)  
- Execution Engine → Alert Delivery  

---

## Invariants (One per Hand‑Off)

- Raw data is never interpreted or filtered by data providers.  
- Stored availability snapshots represent the last known external truth observed by the system.  
- Context data is additive and will not modify execution state.  
- Explanations never introduce new facts and only summarize or interpret previously established context.  
- Alert payloads are idempotent and uniquely identifiable across retries.  

---
## Failure Handling
## Failure Scenarios and Recovery Behavior (v1)

This section documents expected system behavior when external dependencies or internal execution steps fail.

### Failure Scenarios

- **External availability API is temporarily unavailable.**  
  In this case, the system will retry the check later without notifying the user and without modifying stored state.

- **Availability API returns incomplete or malformed data.**  
  In this case, the system will discard the response, preserve the last known valid availability state, and retry on the next scheduled execution.

- **Execution engine fails during processing after availability is detected.**  
  In this case, the system will retry execution using the preserved state and ensure no duplicate alerts are generated.

- **Explanation generation fails or times out.**  
  In this case, the system will proceed with alert delivery using a minimal factual description and skip explanatory enrichment.

- **Alert delivery fails after explanation is generated.**  
  In this case, the system will retry alert delivery according to policy without re‑evaluating availability or regenerating explanations.

---

## Alert Correctness and Idempotency Rules (v1)

This section defines the conditions under which notifications are considered correct and how duplicate alerts are prevented under retries and failures.

### When an Alert May Be Sent

- An alert may be sent only when a newly available site is detected that was previously unavailable for the same user.  
- An alert may be sent only after all required availability data has been validated as complete and trustworthy.  
- An alert may be sent only after the system records intent to notify in persistent state.  
- An alert must not be sent if a matching alert has already been recorded for the same user, site, and date window.  

---

### Idempotency Boundaries

- The combination of user identifier, site identifier, and date range defines alert uniqueness.  
- Explanation content does not affect alert uniqueness.  
- Retrying delivery must not re‑trigger evaluation of availability or alert eligibility.  

---

## Irreversible Commit Point

Once an alert is recorded as sent for a given user, site, and date window, the system must never re‑evaluate availability for that tuple as a trigger for additional notifications, regardless of delivery retries or downstream failures.

---
## Guarantees
## Recovery Guarantees

- A user will never receive more than one notification for the same availability window.  
- Partial failures will not corrupt availability state or alert history.  

---

## Explicitly Forbidden Data Coupling

- The Explanation Engine will not access external data sources directly.  
- Alert Delivery will not mutate execution or availability state.  

---

## Explicit Non‑Goals (v1)

- The system does not automatically book campsites or cabins.  
- The system does not recommend or rank parks across unrelated locations.  
- The system does not send repeated notifications for the same availability window.