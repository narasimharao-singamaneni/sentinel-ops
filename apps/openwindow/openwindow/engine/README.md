## Execution Engine

The Execution Engine is responsible for orchestrating the end‑to‑end execution flow
defined in the architecture.

Responsibilities:
- Coordinates execution of availability checks based on persisted configuration
- Invokes Data Providers to retrieve raw availability data
- Evaluates execution outcomes and controls progression through the workflow
- Initiates state updates and alert evaluation without owning persistent state

Non‑Responsibilities:
- Does not fetch external data directly
- Does not interpret or enrich raw availability data
- Does not generate explanations or deliver notifications
