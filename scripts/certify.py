"""End-gate certification runner (Task 9.1 / D14).

Runs, in order:
  1. PREFLIGHT      — PostgreSQL at localhost:5432 AND LLM at
                      http://127.0.0.1:8000/v1 reachable. Either missing ->
                      PREFLIGHT FAIL + non-zero exit. Certification never
                      silently passes (the ordinary integration suite skips;
                      certification does not).
  2. TEST SUITES    — all five suites via scripts/run_all_tests.py; per-suite
                      collected/passed counts recorded.
  3. DATASET LOAD   — scripts/load_workers.py (200K workers).
  4. SLO            — a live person-api on :8002 over the 200K dataset +
                      scripts/bench_latency.py with its SLO gate.
  5. LIVE LOOP      — the runner's own dedicated live harness (same shape as
                      D12: three real services + unique temp openwindow DB)
                      with one live DB-timeout E2E injection.
  6. EVIDENCE VS RCA — observed fault evidence side-by-side with the
                      generated RCA (root cause, confidence, citation
                      trace-id match, recommended action, whitelist check,
                      ROI summary).
  7. TIME SAVED     — per-incident table (id, runbook class, manual estimate,
                      measured tool time, time saved, duplicate flag); null
                      ROI shown as unavailable, no crash.
  8. OVERALL VERDICT — PASS only if every gate section is green.

Writes reports/certification-<ts>.md; exit code = verdict (0 PASS / 1 FAIL);
teardown stops all child processes.

SLO thresholds are overridable: --lookup-p99-ms / --list-p99-ms (defaults
25 / 150, env SLO_LOOKUP_P99_MS / SLO_LIST_P99_MS). Preflight targets are
overridable for failure-path proofs: CERTIFY_PG_ADDR / CERTIFY_LLM_URL
(defaults are the canonical addresses).
"""

import argparse
import asyncio
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
REPORTS_DIR = PROJECT_ROOT / "reports"
INTEGRATION_DIR = PROJECT_ROOT / "integration"

sys.path.insert(0, str(INTEGRATION_DIR))
sys.path.insert(0, str(INTEGRATION_DIR / "tests"))
import conftest as harness  # noqa: E402
import e2e_helpers  # noqa: E402

import httpx  # noqa: E402

POSTGRES_ADDR = os.getenv("CERTIFY_PG_ADDR", "localhost:5432")
LLM_URL = os.getenv("CERTIFY_LLM_URL", "http://127.0.0.1:8000/v1")

SLO_PORT = 8002
SLO_BASE_URL = f"http://127.0.0.1:{SLO_PORT}"
PERSON_API_DSN = "postgresql://localhost:5432/person_api"

POLL_BUDGET = 90.0
POLL_INTERVAL = 2.0
READY_TIMEOUT = 30.0

ALLOWED_ACTIONS = set(e2e_helpers.ALLOWED_ACTIONS)

_ACTIVE_CHILDREN: dict[str, subprocess.Popen] = {}
_ACTIVE_DB: str | None = None


# ---------------------------------------------------------------------------
# Runner plumbing
# ---------------------------------------------------------------------------

def _install_signal_handlers() -> None:
    """If the runner itself is killed mid-section, never orphan live services:
    stop every tracked child and best-effort drop the temp database."""

    def teardown(signum, _frame) -> None:
        print(f"\n[certify] received signal {signum} — tearing down tracked child processes", flush=True)
        try:
            harness.stop_children(dict(_ACTIVE_CHILDREN))
        except Exception as exc:  # noqa: BLE001
            print(f"[certify] child teardown error: {exc}", flush=True)
        db = _ACTIVE_DB
        if db:

            def _drop() -> None:
                try:
                    asyncio.run(harness.drop_temp_db(db))
                except Exception as exc:  # noqa: BLE001
                    print(f"[certify] could not drop temp db {db}: {exc}", flush=True)

            thread = threading.Thread(target=_drop, daemon=True)
            thread.start()
            thread.join(timeout=90)
            if thread.is_alive():
                print(f"[certify] temp db {db} may need manual drop", flush=True)
        os._exit(128 + signum)

    signal.signal(signal.SIGTERM, teardown)
    signal.signal(signal.SIGINT, teardown)


def python_cmd() -> list[str]:
    uv = shutil.which("uv")
    return ["uv", "run", "python"] if uv else [sys.executable]


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@dataclass
class Section:
    name: str
    status: str = "NOT RUN"
    lines: list[str] = field(default_factory=list)
    gates: list[tuple[str, bool]] = field(default_factory=list)

    def add(self, line: str = "") -> None:
        self.lines.append(line)

    def gate(self, label: str, ok: bool, detail: str = "") -> None:
        self.gates.append((label, ok))
        mark = "PASS" if ok else "FAIL"
        self.add(f"- {mark} {label}" + (f" — {detail}" if detail else ""))

    def pass_gates(self, detail: str = "") -> None:
        self.add("")
        ok = all(ok for _label, ok in self.gates) if self.gates else False
        self.status = "PASS" if ok else "FAIL"
        if detail:
            self.add(f"_Status: {self.status} — {detail}_")


# ---------------------------------------------------------------------------
# 1. PREFLIGHT
# ---------------------------------------------------------------------------

async def _preflight_inner(section: Section) -> None:
    from conftest import PreflightMissing

    pg_line = ""
    try:
        await harness.check_postgres(POSTGRES_ADDR)
        import asyncpg

        conn = await asyncio.wait_for(
            asyncpg.connect(host=POSTGRES_ADDR.partition(":")[0],
                            port=int(POSTGRES_ADDR.partition(":")[2] or 5432),
                            database="postgres", timeout=5.0),
            7.0,
        )
        try:
            pg_line = (await conn.fetchval("SELECT version()")).split(",")[0]
        finally:
            await conn.close()
        section.gate(f"PostgreSQL reachable at {POSTGRES_ADDR}", True, pg_line)
    except PreflightMissing as exc:
        section.gate(f"PostgreSQL reachable at {POSTGRES_ADDR}", False, str(exc))

    try:
        await harness.check_llm(LLM_URL)
        section.gate(f"LLM reachable at {LLM_URL}", True)
    except PreflightMissing as exc:
        section.gate(f"LLM reachable at {LLM_URL}", False, str(exc))


def run_preflight() -> Section:
    section = Section("PREFLIGHT")
    section.add(f"- PostgreSQL target: `{POSTGRES_ADDR}`")
    section.add(f"- LLM target: `{LLM_URL}`")
    section.add("")
    print(f"\n[certify] 1/8 PREFLIGHT — PostgreSQL {POSTGRES_ADDR}, LLM {LLM_URL}")
    try:
        asyncio.run(_preflight_inner(section))
    except Exception as exc:  # noqa: BLE001 — any other failure = FAIL, never a silent pass
        section.gate("preflight ran to completion", False, f"{type(exc).__name__}: {exc}")
    section.pass_gates()
    print(f"[certify] PREFLIGHT: {section.status}")
    return section


# ---------------------------------------------------------------------------
# 2. TEST SUITES
# ---------------------------------------------------------------------------

SUITE_NAMES = ["person-api", "aiops-sentinel", "openwindow", "chaos", "integration"]
HEADER_RE = re.compile(rf"^  ({'|'.join(SUITE_NAMES)}): ")
COLLECTED_RE = re.compile(r"collected (\d+) items")
SUMMARY_RE = re.compile(r"Duration: ([\d.]+)s \| Status: (\w+) \(passed (\d+), skipped (\d+)\)")


def run_test_suites() -> Section:
    section = Section("TEST SUITES")
    section.add("All five suites via `scripts/run_all_tests.py` (streamed).")
    section.add("")
    print("\n[certify] 2/8 TEST SUITES — running master runner")

    per_suite: dict[str, dict] = {name: {} for name in SUITE_NAMES}
    current: str | None = None
    timed_out = False
    returncode: int | None = None
    lines_out: list[str] = []

    cmd = python_cmd() + [str(SCRIPTS / "run_all_tests.py")]
    started = time.time()
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
        )
        assert proc.stdout is not None
        deadline = time.monotonic() + 3600.0
        for line in proc.stdout:
            if time.monotonic() > deadline:
                timed_out = True
                break
            text = line.rstrip("\n")
            print(text)
            lines_out.append(text)
            match = HEADER_RE.match(text)
            if match:
                current = match.group(1)
                continue
            if current is None:
                continue
            entry = per_suite[current]
            collected = COLLECTED_RE.search(text)
            if collected and "collected" not in entry:
                entry["collected"] = int(collected.group(1))
            summary = SUMMARY_RE.search(text)
            if summary:
                entry["duration"] = float(summary.group(1))
                entry["status"] = summary.group(2)
                entry["passed"] = int(summary.group(3))
                entry["skipped"] = int(summary.group(4))
        proc.wait(timeout=60)
        returncode = proc.returncode
    except Exception as exc:  # noqa: BLE001
        section.gate("master runner completed", False, f"{type(exc).__name__}: {exc}")
        section.pass_gates()
        return section
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), 15)
                proc.wait(timeout=15)
            except Exception:  # noqa: BLE001
                pass

    elapsed = time.time() - started
    if timed_out:
        section.gate("master runner completed", False, "runner exceeded 3600s budget")
        section.pass_gates()
        return section

    section.add("| Suite | Collected | Passed | Skipped | Status | Duration (s) |")
    section.add("|---|---|---|---|---|---|")
    for name in SUITE_NAMES:
        entry = per_suite.get(name) or {}
        status = entry.get("status", "NOT RUN")
        section.add(
            f"| {name} | {entry.get('collected', '?')} | {entry.get('passed', '?')} | "
            f"{entry.get('skipped', '?')} | {status} | {entry.get('duration', '?')} |"
        )
    section.add("")
    section.gate("master runner exit code 0", returncode == 0, f"exit code {returncode}")
    parsed_all = all(per_suite[name].get("status") for name in SUITE_NAMES)
    section.gate("all five suites reported", parsed_all)
    bad = [n for n in SUITE_NAMES if (per_suite[n].get("status") or "") not in ("PASS", "SKIP")]
    section.gate("no suite FAIL/TIMEOUT/ERROR", not bad, ", ".join(bad))
    integration_status = (per_suite.get("integration") or {}).get("status")
    # Preflight already proved LLM + PostgreSQL reachable: the live E2E suite
    # must have actually run, not skipped. Certification never silently passes.
    section.gate(
        "integration suite ran live (status PASS)",
        integration_status == "PASS",
        f"integration status: {integration_status}",
    )
    section.add("")
    section.add(f"Total master-runner duration: {elapsed:.0f}s")
    section.pass_gates()
    print(f"[certify] TEST SUITES: {section.status} ({elapsed:.0f}s)")
    return section


# ---------------------------------------------------------------------------
# 3. DATASET LOAD
# ---------------------------------------------------------------------------

def run_dataset_load() -> Section:
    section = Section("DATASET LOAD")
    section.add("`scripts/load_workers.py` — wipe + 50,000 active / 150,000 inactive / 200,000 total.")
    section.add("")
    print("\n[certify] 3/8 DATASET LOAD")

    cmd = python_cmd() + [str(SCRIPTS / "load_workers.py")]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=600)
    for line in (proc.stdout or "").splitlines():
        print(f"  {line}")
        section.add(f"`{line}`")
    if (proc.stderr or "").strip():
        section.add(f"stderr: `{(proc.stderr or '').strip()[:500]}`")
    section.add("")

    def grab(pattern: str, cast: type = int):
        match = re.search(pattern, proc.stdout or "")
        return cast(match.group(1)) if match else None

    active, inactive, total = (
        grab(r"active:\s+(\d+)"), grab(r"inactive:\s+(\d+)"), grab(r"total:\s+(\d+)"),
    )
    load_elapsed = grab(r"elapsed:\s+([\d.]+)s", cast=float)
    section.gate("loader exit code 0", proc.returncode == 0, f"exit code {proc.returncode}")
    section.gate(
        "counts 50,000 / 150,000 / 200,000",
        (active, inactive, total) == (50_000, 150_000, 200_000),
        f"active={active} inactive={inactive} total={total}",
    )
    section.add("")
    section.add(f"Load elapsed: {load_elapsed}s")
    section.pass_gates()
    print(f"[certify] DATASET LOAD: {section.status}")
    return section


# ---------------------------------------------------------------------------
# 4. SLO
# ---------------------------------------------------------------------------

def _start_person_api_for_slo() -> tuple[subprocess.Popen, Path]:
    log_path = INTEGRATION_DIR / "logs" / f"person-api-cert-slo-{int(time.time())}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql+asyncpg://localhost:5432/person_api"
    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(SLO_PORT)],
            cwd=str(PROJECT_ROOT / "services" / "person-api"),
            env=env, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
        )
    _ACTIVE_CHILDREN["slo-person-api"] = proc
    return proc, log_path


async def _stop_person_api_for_slo(proc: subprocess.Popen) -> None:
    _ACTIVE_CHILDREN.pop("slo-person-api", None)
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), 15)
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:  # noqa: BLE001
            proc.kill()
        proc.wait(timeout=10)
    print(f"[certify] stopped SLO person-api (pid {proc.pid})")


def run_slo(lookup_p99: float, list_p99: float) -> Section:
    section = Section("SLO")
    section.add(f"Thresholds: lookup warm p99 < {lookup_p99} ms, indexed list warm p99 < {list_p99} ms.")
    section.add("")
    print(f"\n[certify] 4/8 SLO — lookup p99 < {lookup_p99} ms, list p99 < {list_p99} ms")

    proc: subprocess.Popen | None = None
    try:
        if port_open(SLO_PORT):
            section.gate(f"port {SLO_PORT} free", False, "already in use — cannot start dedicated person-api")
            section.pass_gates()
            return section
        section.gate(f"port {SLO_PORT} free", True)
        proc, log_path = _start_person_api_for_slo()
        print(f"[certify] started person-api for SLO on :{SLO_PORT} (pid {proc.pid}, log {log_path.name})")

        ok = asyncio.run(harness.wait_ready("person-api", f"{SLO_BASE_URL}/health", timeout=READY_TIMEOUT))[0]
        if not ok:
            raise RuntimeError(f"person-api failed readiness on :{SLO_PORT} (log {log_path})")
        print(f"[certify] SLO person-api ready")

        cmd = python_cmd() + [
            str(SCRIPTS / "bench_latency.py"),
            "--base-url", SLO_BASE_URL,
            "--lookup-p99-ms", str(lookup_p99),
            "--list-p99-ms", str(list_p99),
        ]
        bench = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=1800)
        for line in (bench.stdout or "").splitlines():
            print(f"  {line}")
        for line in (bench.stdout or "").splitlines():
            check = re.search(r"^(PASS|FAIL) (\S+) c=(\d+): warm p99 ([\d.]+) ms vs threshold ([\d.]+) ms", line)
            if check:
                section.add(
                    f"- {check.group(1)} {check.group(2)} c={check.group(3)}: "
                    f"warm p99 {check.group(4)} ms vs {check.group(5)} ms"
                )
            elif "verdict:" in line:
                section.add(f"- benchmark {line.split('verdict:', 1)[1].strip()}")
        if (bench.stderr or "").strip():
            section.add(f"stderr: `{(bench.stderr or '').strip()[:500]}`")
        section.add("")
        section.gate("benchmark exit code 0 (SLO PASS)", bench.returncode == 0, f"exit code {bench.returncode}")
        section.pass_gates()
        print(f"[certify] SLO: {section.status}")
        return section
    except Exception as exc:  # noqa: BLE001
        section.gate("SLO section ran to completion", False, f"{type(exc).__name__}: {exc}")
        section.pass_gates()
        return section
    finally:
        if proc is not None:
            asyncio.run(_stop_person_api_for_slo(proc))


# ---------------------------------------------------------------------------
# 5-7. LIVE LOOP / EVIDENCE VS RCA / TIME SAVED
# ---------------------------------------------------------------------------

async def run_live_loop() -> tuple[Section, dict]:
    section = Section("LIVE LOOP")
    section.add("Dedicated D12-shape harness: three real services + unique temp openwindow DB, one live DB-timeout injection.")
    section.add("")
    print("\n[certify] 5/8 LIVE LOOP — starting dedicated harness")

    evidence: dict = {}
    try:
        await harness.preflight()
        section.gate("harness preflight (Postgres + LLM)", True)

        db_name = await harness.create_temp_db()
        _ACTIVE_DB = db_name
        db_dsn = harness.target_dsn(db_name)
        children: dict[str, subprocess.Popen] = {}
        try:
            harness.run_migrations(db_dsn)
            section.gate("temp database + migrations", True, db_name)
            for name, port, _url, cwd, args, extra_env in harness.SERVICE_SPECS:
                if harness.port_in_use(port):
                    raise RuntimeError(f"port {port} already in use — cannot start {name}")
                children[name] = harness.start_service(
                    name, port, cwd, args, extra_env,
                    db_dsn if name == "openwindow" else None,
                )
                _ACTIVE_CHILDREN[f"live-{name}"] = children[name]
            for name, port, url, *_ in harness.SERVICE_SPECS:
                ok, waited = await harness.wait_ready(name, url, timeout=READY_TIMEOUT)
                if not ok:
                    raise RuntimeError(harness.unhealthy_message(name, url, port, waited))
                print(f"[certify] {name} ready at {url} ({waited:.1f}s)")
            section.gate("all three services healthy", True)

            result = await inject_db_timeout(children)
            evidence.update(result)
        finally:
            harness.stop_children(children)
            _ACTIVE_CHILDREN.clear()
            _ACTIVE_DB = None
            await harness.drop_temp_db(db_name)

        if evidence.get("http_status") != 504:
            section.gate("observed real 504 from /test/db-delay?delay=5", False,
                         f"got HTTP {evidence.get('http_status')}")
        else:
            section.gate("observed real 504 from /test/db-delay?delay=5", True,
                         "body carries timeout + pg_sleep signature")
        section.gate("concurrency drained after 504 (in_use == 0)", evidence.get("pool_drained", False))
        incident = evidence.get("incident")
        if isinstance(incident, dict):
            section.gate("incident ingested with matching real trace id",
                         evidence.get("citation_trace_match", False))
            section.gate("root cause is non-fallback",
                         incident.get("root_cause") not in (None, "", e2e_helpers.FALLBACK_ROOT_CAUSE))
        else:
            section.gate("incident ingested with matching real trace id", False, "no incident found")
            section.gate("root cause is non-fallback", False, "no incident found")
        section.pass_gates()
        print(f"[certify] LIVE LOOP: {section.status}")
        return section, evidence
    except Exception as exc:  # noqa: BLE001
        section.gate("LIVE LOOP ran to completion", False, f"{type(exc).__name__}: {exc}")
        section.pass_gates()
        print(f"[certify] LIVE LOOP: {section.status} ({exc})")
        return section, evidence


async def inject_db_timeout(children: dict[str, subprocess.Popen]) -> dict:
    """One live DB-timeout injection against the dedicated harness; captures evidence."""
    person = f"http://127.0.0.1:{harness.PERSON_API_PORT}"
    ow = f"http://127.0.0.1:{harness.OPENWINDOW_PORT}"

    out: dict = {"delay_seconds": 5.0, "route": "/test/db-delay?delay=5"}
    async with httpx.AsyncClient(base_url=person, timeout=30.0) as api, httpx.AsyncClient(
        base_url=ow, timeout=10.0
    ) as client:
        pool = None
        for _ in range(30):
            pool = (await api.get("/api/v1/telemetry/pool")).json()
            if pool.get("concurrency_in_use") == 0:
                break
            await asyncio.sleep(0.5)
        if pool is None or pool.get("concurrency_in_use") != 0:
            raise RuntimeError(f"limiter not drained before injection: {pool}")

        before = (await client.get("/dashboard/data/")).json()
        before_ids = {str(i["id"]) for i in before.get("incidents", [])}

        resp = await api.get("/test/db-delay?delay=5", timeout=15.0)
        out["http_status"] = resp.status_code
        out["timeout_detail"] = resp.text[:400]
        out["request_id"] = resp.headers.get("x-request-id")

        for _ in range(20):
            pool = (await api.get("/api/v1/telemetry/pool")).json()
            if pool.get("concurrency_in_use") == 0:
                break
            await asyncio.sleep(0.5)
        out["pool_drained"] = (pool or {}).get("concurrency_in_use") == 0

        request_id = out.get("request_id")
        if not request_id:
            raise RuntimeError("504 response carried no X-Request-ID header")
        traces = e2e_helpers.traces_for_request_ids(harness.LOG_DIR, [request_id])
        trace = traces.get(request_id)
        if not trace:
            raise RuntimeError(f"no structured log line with a trace_id for request {request_id}")
        out["trace_id"] = trace

        deadline = time.monotonic() + POLL_BUDGET
        last_data: dict | None = None
        while time.monotonic() < deadline:
            data = (await client.get("/dashboard/data/")).json()
            last_data = data
            for inc in e2e_helpers.incidents_matching(data.get("incidents", []), "person-api:/test/db-delay", "status=504"):
                if str(inc["id"]) in before_ids:
                    continue
                raw_trace = e2e_helpers.raw_trace(inc.get("raw_log"))
                if raw_trace == trace and e2e_helpers.citation_trace_matches(inc, trace):
                    out["incident"] = inc
                    out["citation_trace_match"] = True
                    break
            if out.get("incident"):
                break
            await asyncio.sleep(POLL_INTERVAL)
        out["dashboard"] = last_data or {}
    return out


def run_evidence_vs_rca(evidence: dict) -> Section:
    section = Section("EVIDENCE VS RCA")
    section.add("Observed fault evidence vs generated RCA for the injected 504.")
    section.add("")
    incident = evidence.get("incident") or {}
    roi = incident.get("roi") if isinstance(incident, dict) else None
    dry = incident.get("dry_run_payload") or {}
    action = dry.get("action")

    section.add("| Field | Observed | Generated (RCA) |")
    section.add("|---|---|---|")
    section.add(f"| HTTP status | {evidence.get('http_status', '?')} | — |")
    section.add(f"| Route | `{evidence.get('route', '?')}` | — |")
    section.add(f"| pg_sleep delay | {evidence.get('delay_seconds')}s | — |")
    section.add(f"| Request id | `{evidence.get('request_id', '?')}` | — |")
    section.add(f"| Trace id | `{evidence.get('trace_id', '?')}` | citation trace id `{next((c.get('trace_id') for c in incident.get('citations', []) if c.get('trace_id')), '?')}` |")
    section.add(f"| Timeout detail | `{str(evidence.get('timeout_detail', ''))[:120]}` | — |")
    section.add(f"| Root cause | — | `{str(incident.get('root_cause', '<none>'))[:200]}` |")
    section.add(f"| Confidence | — | {incident.get('confidence', '?')} |")
    section.add(f"| Recommended action | — | `{action}` (target `{dry.get('target_resource', '?')}`, fallback={dry.get('fallback', False)}) |")
    if isinstance(roi, dict):
        section.add(
            f"| ROI summary | — | runbook `{roi.get('runbook_class', '?')}` — manual "
            f"{roi.get('manual_estimate_minutes', '?')} min vs measured tool time "
            f"{roi.get('tool_time_minutes', '?')} min → saved {roi.get('time_saved_minutes', '?')} min "
            f"({len(roi.get('breakdown', []) or [])} breakdown items) |"
        )
    else:
        section.add("| ROI summary | — | unavailable |")
    section.add("")

    if incident:
        section.gate("citation trace id matches the observed real trace", bool(evidence.get("citation_trace_match")))
        whitelisted = action in ALLOWED_ACTIONS and (action != "manual_review" or dry.get("fallback") is True)
        section.gate("recommended action is whitelisted", whitelisted, f"action={action!r}")
        conf = incident.get("confidence")
        section.gate("confidence in [0, 1]", isinstance(conf, (int, float)) and 0.0 <= float(conf) <= 1.0, f"{conf}")
        section.gate("root cause present and non-fallback",
                     bool(incident.get("root_cause")) and incident.get("root_cause") != e2e_helpers.FALLBACK_ROOT_CAUSE)
        breakdown = (roi or {}).get("breakdown") if isinstance(roi, dict) else None
        saved = (roi or {}).get("time_saved_minutes") if isinstance(roi, dict) else None
        section.gate("ROI object valid, breakdown non-empty, time saved > 0",
                     isinstance(roi, dict) and bool(breakdown)
                     and isinstance(saved, (int, float)) and float(saved) > 0)
    else:
        for label in ("citation trace id matches the observed real trace",
                      "recommended action is whitelisted",
                      "confidence in [0, 1]",
                      "root cause present and non-fallback",
                      "ROI object valid, breakdown non-empty, time saved > 0"):
            section.gate(label, False, "no incident generated")
    section.pass_gates()
    print(f"[certify] EVIDENCE VS RCA: {section.status}")
    return section


def run_time_saved(evidence: dict) -> Section:
    section = Section("TIME SAVED")
    section.add("Per-incident time saved on the live temp openwindow DB (knowledge-base baseline vs measured tool time).")
    section.add("")
    incidents = (evidence.get("dashboard") or {}).get("incidents", [])
    section.add("| Incident | Status | Runbook class | Manual estimate (min) | Measured tool time (min) | Time saved (min) | Duplicate |")
    section.add("|---|---|---|---|---|---|---|")
    cumulative = 0.0
    rendered = 0
    for inc in incidents:
        roi = inc.get("roi")
        if isinstance(roi, dict):
            runbook = str(roi.get("runbook_class", "unavailable"))
            manual = roi.get("manual_estimate_minutes")
            tool = roi.get("tool_time_minutes")
            saved = roi.get("time_saved_minutes")
            if isinstance(saved, (int, float)):
                cumulative += float(saved)
        else:
            runbook = manual = tool = saved = "unavailable"
        section.add(
            f"| {inc.get('id', '?')} | {inc.get('status', '?')} | {runbook} | "
            f"{manual if isinstance(manual, (int, float)) else 'unavailable'} | "
            f"{tool if isinstance(tool, (int, float)) else 'unavailable'} | "
            f"{saved if isinstance(saved, (int, float)) else 'unavailable'} | "
            f"{inc.get('duplicate', 'n/a')} |"
        )
        rendered += 1
    section.add("")
    section.add(f"Incidents listed: {rendered}")
    section.add(f"Cumulative time saved: {round(cumulative, 3)} min")
    section.add("")
    section.gate("table rendered without error (null ROI shown as unavailable)", True, f"{rendered} incident(s)")
    section.pass_gates()
    print(f"[certify] TIME SAVED: {section.status}")
    return section


# ---------------------------------------------------------------------------
# 8. Report + verdict
# ---------------------------------------------------------------------------

GATE_SECTIONS = ["PREFLIGHT", "TEST SUITES", "DATASET LOAD", "SLO", "LIVE LOOP", "EVIDENCE VS RCA", "TIME SAVED"]


def write_report(stamp: str, sections: list[Section], verdict: str, failing: list[str]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"certification-{stamp}.md"
    lines = [
        "# Certification Report",
        "",
        f"- Timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"- Host: {platform.node()}",
        f"- Environment: {platform.system()} {platform.release()} ({platform.machine()})",
        f"- Python: {sys.version.split()[0]}",
        f"- Preflight targets: PostgreSQL `{POSTGRES_ADDR}`, LLM `{LLM_URL}`",
        "",
    ]
    numbers = {
        "PREFLIGHT": 1, "TEST SUITES": 2, "DATASET LOAD": 3, "SLO": 4,
        "LIVE LOOP": 5, "EVIDENCE VS RCA": 6, "TIME SAVED": 7,
    }
    for section in sections:
        num = numbers.get(section.name, "?")
        lines.append(f"## {num}. {section.name} — {section.status}")
        lines.append("")
        lines.extend(section.lines)
        lines.append("")
    lines += [
        f"## 8. OVERALL VERDICT — {verdict}",
        "",
        f"**{verdict}**" + (f" — failing sections: {', '.join(failing)}" if failing else " — every gate section green"),
        "",
        "| Section | Status |",
        "|---|---|",
    ]
    for section in sections:
        lines.append(f"| {section.name} | {section.status} |")
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel-Ops end-gate certification (D14)")
    parser.add_argument("--lookup-p99-ms", type=float, default=float(os.getenv("SLO_LOOKUP_P99_MS", "25")),
                        help="SLO threshold (ms) for PK lookup warm p99 (default 25)")
    parser.add_argument("--list-p99-ms", type=float, default=float(os.getenv("SLO_LIST_P99_MS", "150")),
                        help="SLO threshold (ms) for indexed list warm p99 (default 150)")
    args = parser.parse_args()

    _install_signal_handlers()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started = time.time()
    sections: list[Section] = []
    evidence: dict = {}

    print("Sentinel-Ops Certification Runner (D14)")
    print(f"Project root: {PROJECT_ROOT}")

    # 1. PREFLIGHT — hard stop on failure; certification never silently passes.
    preflight = run_preflight()
    sections.append(preflight)
    if preflight.status != "PASS":
        verdict_sections = sections
        write_and_exit(stamp, verdict_sections, started)

    # 2. TEST SUITES
    suites = run_test_suites()
    sections.append(suites)
    if suites.status != "PASS":
        write_and_exit(stamp, sections, started)

    # 3. DATASET LOAD
    load = run_dataset_load()
    sections.append(load)
    if load.status != "PASS":
        write_and_exit(stamp, sections, started)

    # 4. SLO
    slo = run_slo(args.lookup_p99_ms, args.list_p99_ms)
    sections.append(slo)
    if slo.status != "PASS":
        write_and_exit(stamp, sections, started)

    # 5. LIVE LOOP
    live, evidence = asyncio.run(run_live_loop())
    sections.append(live)
    if live.status != "PASS":
        write_and_exit(stamp, sections, started)

    # 6. EVIDENCE VS RCA
    sections.append(run_evidence_vs_rca(evidence))

    # 7. TIME SAVED
    sections.append(run_time_saved(evidence))

    # 8. OVERALL VERDICT
    write_and_exit(stamp, sections, started)


def write_and_exit(stamp: str, sections: list[Section], started: float) -> None:
    by_name = {s.name: s for s in sections}
    aborted_at = sections[-1].name if sections else "?"
    padded: list[Section] = []
    for name in GATE_SECTIONS:
        if name in by_name:
            padded.append(by_name[name])
        else:
            skip = Section(name)
            skip.status = "NOT RUN"
            skip.add(f"NOT RUN — aborted at {aborted_at}.")
            padded.append(skip)

    status_by_name = {s.name: s.status for s in padded}
    failing = [name for name in GATE_SECTIONS if status_by_name.get(name) != "PASS"]
    verdict = "PASS" if not failing else "FAIL"
    path = write_report(stamp, padded, verdict, failing)
    elapsed = time.time() - started
    print(f"\n{'=' * 70}")
    print(f"  OVERALL VERDICT: {verdict}" + (f" — failing sections: {', '.join(failing)}" if failing else ""))
    print(f"  sections: " + ", ".join(f"{s.name}={s.status}" for s in padded))
    print(f"  report: {path}")
    print(f"  duration: {elapsed:.0f}s")
    print(f"{'=' * 70}")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
