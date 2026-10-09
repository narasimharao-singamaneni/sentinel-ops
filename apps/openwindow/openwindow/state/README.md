## Configuration & State

This component is responsible for persisting all durable system state
required to execute and recover OpenWindow workflows.

Responsibilities:
- Stores user‑defined alert configuration
- Persists last known availability snapshots
- Records alert intent and delivery history for idempotency

Non‑Responsibilities:
- Does not initiate execution
- Does not perform availability checks
- Does not deliver notifications