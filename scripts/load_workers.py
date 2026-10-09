"""Load the 200K worker dataset into the existing local PostgreSQL person_api DB.

Behavior (Task 6.1 / D13):
- Target: `localhost:5432/person_api` (the instance person-api is already integrated with)
- Wipes/truncates `workers`, then chunked bulk insert (5,000/chunk) of exactly
  50,000 active + 150,000 inactive workers
- Prints per-status counts + total + elapsed time
- Exits non-zero if the post-load count self-check does not match the expected counts
- Idempotent: a re-run yields identical counts
"""

import asyncio
import sys
import time
import uuid

import asyncpg

DSN = "postgresql://localhost:5432/person_api"
CHUNK_SIZE = 5_000
# Exact dataset to load
LOAD_ACTIVE = 50_000
LOAD_INACTIVE = 150_000
LOAD_TOTAL = LOAD_ACTIVE + LOAD_INACTIVE
# Post-load self-check targets (deliberately lowering one of these proves
# the non-zero exit path — restore after the negative test)
CHECK_ACTIVE = 50_000
CHECK_INACTIVE = 150_000
CHECK_TOTAL = CHECK_ACTIVE + CHECK_INACTIVE

INSERT_SQL = "INSERT INTO workers (id, name, status) VALUES ($1, $2, $3)"


def _chunked(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


async def _load() -> int:
    started = time.perf_counter()
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("TRUNCATE workers")

        total_rows = LOAD_TOTAL
        active_until = LOAD_ACTIVE
        rows = [
            (uuid.uuid4().hex, f"worker-{i}", "active" if i < active_until else "inactive")
            for i in range(total_rows)
        ]

        inserted = 0
        for chunk in _chunked(rows, CHUNK_SIZE):
            await conn.executemany(INSERT_SQL, chunk)
            inserted += len(chunk)

        counts = dict(
            (r["status"], r["count"])
            for r in await conn.fetch("SELECT status, COUNT(*) AS count FROM workers GROUP BY status")
        )
        active = counts.get("active", 0)
        inactive = counts.get("inactive", 0)
        total = active + inactive
        elapsed = time.perf_counter() - started

        print(f"active:   {active}")
        print(f"inactive: {inactive}")
        print(f"total:    {total}")
        print(f"elapsed:  {elapsed:.2f}s")

        if (active, inactive, total) != (CHECK_ACTIVE, CHECK_INACTIVE, CHECK_TOTAL):
            print(
                "COUNT MISMATCH: loaded "
                f"{active} active / {inactive} inactive / {total} total; "
                f"check expects {CHECK_ACTIVE} / {CHECK_INACTIVE} / {CHECK_TOTAL}",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        await conn.close()


def main() -> None:
    try:
        sys.exit(asyncio.run(_load()))
    except OSError as exc:
        print(f"FAILED to reach PostgreSQL at {DSN}: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
