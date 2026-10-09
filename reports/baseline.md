# Baseline — end-to-end-rca-pipeline

Recorded before any implementation (Phase 1).

## PostgreSQL (existing, verified connected)

- Server: PostgreSQL 16.14 (Homebrew) on aarch64-apple-darwin
- DSN: `postgresql+asyncpg://localhost:5432/person_api`
- Database: `person_api`
- `workers` table: present (1 row at baseline time)
- Verified from `services/person-api` environment via asyncpg (Task 1.1)

## Test suites (master run, 2026-08-14)

| Suite | Collected | Result | Duration |
|---|---|---|---|
| person-api | 62 | 62 passed | 1.5s |
| aiops-sentinel | 35 | 35 passed | 0.8s |
| openwindow | 35 | 35 passed | 0.5s |
| chaos | 13 | 13 passed | 36.8s |
| **Total** | **145** | **4/4 PASS** | 39.6s |

Full log: `reports/run1_master.log`
