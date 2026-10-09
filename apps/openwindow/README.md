# OpenWindow — AI-Powered Availability Intelligence System

OpenWindow is a stateful backend system that monitors availability changes and uses AI to generate structured reasoning about why the change matters and what action to take.

---

## 🚀 Problem

Users receive many alerts about availability (parks, bookings, etc.), but:
- they don’t know which alerts matter
- they don’t know how urgent it is
- they don’t know what action to take

---

## Solution

OpenWindow transforms raw availability signals into:

- ✅ meaningful explanations (why it matters)
- ✅ urgency classification
- ✅ actionable recommendations

---

## System Overview

- The system processes availability events through a structured decision pipeline:

---

Input → Compare State → Detect Change → Classify → AI Reasoning → Structured Output

---

## Features

- ✅ Stateful change detection (OPEN ↔ CLOSED)
- ✅ Priority classification (HIGH / LOW)
- ✅ AI-based explanation using LLM (Groq)
- ✅ Structured JSON output (reason, urgency, action)
- ✅ Idempotent execution (no duplicate alerts)
- ✅ Continuous monitoring capability

---

## Example Output
```json
{
  "change_detected": true,
  "change_type": "OPENED",
  "priority": "HIGH",
  "explanation": "Seasonal reopening with high demand",
  "action": "Reserve a spot immediately",
  "urgency": "HIGH",
  "message": "Change detected"
}
```
## Tech Stack
- Python
- FastAPI
- Groq (LLM API)
- JSON-based state persistence

## AI Integration
- LLM is used as a structured reasoning component, not just text generation.
# The system:
- enforces strict JSON output
- parses and validates responses
- maps AI output into system fields

## How It Works
- Fetch availability data (simulated dataset)
- Compare with previous state
- Detect change
- Assign priority
- Invoke LLM for reasoning
- Return structured response

## Running the Project

OpenWindow runs from the repo's single uv environment (one `pyproject.toml` / `uv.lock` / `.venv` at the repo root — there is no per-app install step):

```bash
cd <repo-folder>
uv sync                  # from the repo root
```

To run in loop:

```bash
uv run openwindow        # from the repo root — runner/loop_runner.py
```

To run API:

```bash
uv run openwindow-api    # from the repo root — runner/api_runner.py
```

Django dashboard server (standard stack, port 8003):

```bash
cd apps/openwindow
DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost \
  uv run python manage.py runserver 8003 --noreload
```

## Notes
- This project focuses on AI + system integration, not UI
- Data source is simulated for faster iteration
- Designed as a foundation for real-world AI-driven systems

## Next Steps (Future)
- Replace simulated data with real APIs
- Add UI/dashboard
- Extend into multi-agent workflows (LangGraph)

## Author
- Built as part of AI Engineer transition journey — focused on system thinking, reasoning, and real-world applicability.
