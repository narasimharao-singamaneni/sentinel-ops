# Certification Report

- Timestamp: 2026-08-21T14:34:04.317261+00:00
- Host: wifi-automations-MacBook-Pro.local
- Environment: Darwin 25.4.0 (arm64)
- Python: 3.12.13
- Preflight targets: PostgreSQL `localhost:5432`, LLM `http://127.0.0.1:9/v1`

## 1. PREFLIGHT — FAIL

- PostgreSQL target: `localhost:5432`
- LLM target: `http://127.0.0.1:9/v1`

- PASS PostgreSQL reachable at localhost:5432 — PostgreSQL 16.14 (Homebrew) on aarch64-apple-darwin25.6.0
- FAIL LLM reachable at http://127.0.0.1:9/v1 — LLM unreachable at http://127.0.0.1:9/v1 (ConnectError); live LLM tests require it


## 2. TEST SUITES — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 3. DATASET LOAD — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 4. SLO — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 5. LIVE LOOP — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 6. EVIDENCE VS RCA — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 7. TIME SAVED — NOT RUN

NOT RUN — aborted at PREFLIGHT.

## 8. OVERALL VERDICT — FAIL

**FAIL** — failing sections: PREFLIGHT, TEST SUITES, DATASET LOAD, SLO, LIVE LOOP, EVIDENCE VS RCA, TIME SAVED

| Section | Status |
|---|---|
| PREFLIGHT | FAIL |
| TEST SUITES | NOT RUN |
| DATASET LOAD | NOT RUN |
| SLO | NOT RUN |
| LIVE LOOP | NOT RUN |
| EVIDENCE VS RCA | NOT RUN |
| TIME SAVED | NOT RUN |
