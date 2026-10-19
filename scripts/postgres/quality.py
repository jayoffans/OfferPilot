"""Isolated PostgreSQL HTTP journey and synthetic query measurements.

Run using backend/.venv/Scripts/python.exe. Set the admin/app URLs documented by
tests/postgres/support.py. That helper creates and removes only fresh isolated
databases; existing databases are never migrated or cleared. No LLM is contacted.
Reports contain synthetic IDs and timings only, never database credentials.
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import hashlib
import io
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pymupdf
from sqlalchemy import event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PASSWORD = "Synthetic-S5-quality-password-2026"
sys.path.insert(0, str(BACKEND / "src"))


def require(condition, check):
    if not condition:
        raise RuntimeError(f"PostgreSQL quality check failed: {check}")


def checked_url(value):
    url = make_url(value)
    require(url.get_backend_name() == "postgresql", "real PostgreSQL required")
    require(url.host in {"127.0.0.1", "localhost", "::1"}, "loopback database required")
    require(bool(url.database) and url.database.startswith("offerpilot_s5_"), "isolated database name required")
    return url


def synthetic_pdf():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Synthetic S5 student. Python project evidence.")
        return pdf.tobytes()


def configured_environment(url, storage, port, metrics):
    environment = dict(os.environ)
    paths = [str(BACKEND / "src"), str(BACKEND / ".venv" / "Lib" / "site-packages")]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    environment.update({
        "PYTHONPATH": os.pathsep.join(paths),
        "OFFERPILOT_DATABASE_URL": url,
        "OFFERPILOT_UPLOAD_DIR": str(storage),
        "OFFERPILOT_DELETION_LEDGER_DIR": str(storage.parent / "deletion-ledger"),
        "OFFERPILOT_ENVIRONMENT": "development",
        "OFFERPILOT_SESSION_COOKIE_SECURE": "false",
        "OFFERPILOT_TRUSTED_ORIGINS": json.dumps([f"http://127.0.0.1:{port}"]),
        "OFFERPILOT_AUTH_IP_ATTEMPT_LIMIT": "1000",
        "OFFERPILOT_AUTH_REGISTRATION_ATTEMPT_LIMIT": "100",
        "OFFERPILOT_MOCK_EXTRACTION_ENABLED": "false",
        "OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED": "false",
        "OFFERPILOT_QUALITY_METRICS": str(metrics),
    })
    return environment


def serve(port):
    """Private child process: no fixture endpoints, no SQL parameters in metrics."""
    import uvicorn
    from offerpilot_api.db.session import engine
    from offerpilot_api.main import create_app

    current = contextvars.ContextVar("query_metrics", default=None)
    metrics_file = Path(os.environ["OFFERPILOT_QUALITY_METRICS"])
    output_lock = threading.Lock()

    @event.listens_for(engine, "before_cursor_execute")
    def before(connection, _cursor, _statement, _parameters, _context, _many):
        connection.info["quality_query_started"] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after(connection, _cursor, _statement, _parameters, _context, _many):
        measurement = current.get()
        if measurement is not None:
            measurement["queries"] += 1
            duration = (time.perf_counter() - connection.info.pop("quality_query_started")) * 1000
            measurement["sql_ms"] += duration
            measurement["max_sql_ms"] = max(measurement["max_sql_ms"], duration)

    app = create_app()

    @app.middleware("http")
    async def measure(request, call_next):
        record = {"queries": 0, "sql_ms": 0.0, "max_sql_ms": 0.0}
        token = current.set(record)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            record.update({"route": getattr(request.scope.get("route"), "path", "unknown"),
                "method": request.method, "status": response.status_code,
                "server_ms": (time.perf_counter() - start) * 1000})
            with output_lock, metrics_file.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record) + "\n")
            return response
        finally:
            current.reset(token)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        log_level="critical", access_log=False))

    def stop():
        sys.stdin.buffer.read()
        server.should_exit = True

    control = threading.Thread(target=stop, daemon=True)
    control.start()
    try:
        server.run()
    finally:
        engine.dispose()


@contextlib.contextmanager
def running_server(environment, port):
    child = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-u",
        str(Path(__file__).resolve()), "--serve", str(port)], cwd=ROOT, env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 30
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                require(child.poll() is None, "local HTTP process remains running")
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("PostgreSQL HTTP server readiness timeout")
        yield
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5)
            raise RuntimeError("PostgreSQL HTTP server graceful shutdown timeout") from None
        require(child.returncode == 0, "local HTTP server exits cleanly")


def response_json(client, method, path, status=200, **kwargs):
    response = client.request(method, path, **kwargs)
    require(response.status_code == status, f"{method} {path.split('?')[0]} returns {status}, got {response.status_code}")
    return response.json() if response.content else None


def login(client, email):
    result = response_json(client, "POST", "/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    client.headers["X-CSRF-Token"] = result["csrf_token"]
    return result


def journey(engine, options):
    from offerpilot_api.identity.services import create_invitation

    with Session(engine) as db:
        invitations = [create_invitation(db, email=f"s5-journey-{index}@example.com") for index in range(2)]
    steps = []
    with httpx.Client(**options) as owner, httpx.Client(**options) as other:
        for index, client in enumerate((owner, other)):
            email = f"s5-journey-{index}@example.com"
            response_json(client, "POST", "/api/v1/auth/register", status=201,
                json={"email": email, "password": PASSWORD, "invite_token": invitations[index]})
            login(client, email)
        steps.append("01 invited registration and independent login")
        content = synthetic_pdf()
        resume = response_json(owner, "POST", "/api/v1/resumes", status=201,
            files={"file": ("s5-synthetic.pdf", content, "application/pdf")})
        require(owner.get(f"/api/v1/resumes/{resume['id']}/download").content == content, "original PDF bytes")
        require(other.get(f"/api/v1/resumes/{resume['id']}").status_code == 404, "private resume isolation")
        steps.append("02 synthetic PDF upload and exact download")
        profile = response_json(owner, "GET", "/api/v1/profile")
        profile = response_json(owner, "PUT", "/api/v1/profile", json={"revision": profile["revision"],
            "education": [{"school": "合成测试大学", "degree": "本科在读", "major": "计算机科学",
                "end_date": "2027-06", "end_date_kind": "expected"}],
            "skills": [{"name": "Python", "level": "掌握"}], "projects": [], "experiences": []})
        require(profile["skills"][0]["source"]["kind"] == "manual", "explicit formal user facts")
        require(not profile["candidates"], "no unconfirmed AI facts")
        steps.append("03 explicitly saved formal education and skill profile")
        jd = "合成后端实习岗位\n必须掌握 Python\nJava 优先"
        job = response_json(owner, "POST", "/api/v1/jobs", status=201, json={"title": "合成后端实习生",
            "company": "合成测试公司", "location": "北京", "work_type": "internship", "jd_text": jd,
            "idempotency_key": uuid4().hex})
        steps.append("04 private synthetic JD import")
        preview = response_json(owner, "POST", "/api/v1/jobs/parse-preview", json={"jd_text": jd})
        require(bool(preview["requirements"]) and all(item["review_status"] == "pending" for item in preview["requirements"]),
            "rule extraction is initially pending")
        job = response_json(owner, "PUT", f"/api/v1/jobs/{job['id']}/requirements", json={"revision": job["revision"],
            "requirements": [{**item, "review_status": "confirmed"} for item in preview["requirements"]]})
        require(all(item["review_status"] == "confirmed" for item in job["requirements"]), "explicit JD review persisted")
        steps.append("05 deterministic JD extraction and explicit review")
        match = response_json(owner, "POST", f"/api/v1/jobs/{job['id']}/matches", json={"job_revision": job["revision"],
            "profile_revision": profile["revision"], "idempotency_key": uuid4().hex})
        require(not match["stale"] and bool(match["evaluation"]["items"]), "current deterministic match")
        steps.append("06 versioned deterministic matching")
        evidence = next(item for item in match["evaluation"]["items"] if "Python" in item["content"])
        require(evidence["status"] == "met" and bool(evidence["profile_evidence"]), "matching uses saved profile evidence")
        source = evidence["jd_evidence"]
        require(jd[source["source_start"]:source["source_end"]] == source["evidence_text"], "exact JD evidence offsets")
        steps.append("07 both profile and JD evidence verified")
        application = response_json(owner, "POST", "/api/v1/applications", status=201, json={"job_id": job["id"],
            "job_revision": job["revision"], "match_id": match["id"], "idempotency_key": uuid4().hex})
        require(application["match"]["id"] == match["id"], "explicit matching reference")
        steps.append("08 preparation application created")

        def update_application(**changes):
            nonlocal application
            payload = {key: application[key] for key in ("revision", "status", "submitted_at", "notes", "next_action")}
            application = response_json(owner, "PUT", f"/api/v1/applications/{application['id']}",
                json={**payload, "idempotency_key": uuid4().hex, **changes})

        update_application(status="submitted")
        require(application["submitted_at"] is None, "unknown submission instant remains unknown")
        steps.append("09 submitted status without invented timestamp")
        dashboard = response_json(owner, "GET", "/api/v1/dashboard")
        update_application(next_action={"title": "核对合成申请材料", "notes": "合成待办",
            "due_date": dashboard["today"], "due_time": None, "timezone": "Asia/Shanghai", "status": "pending"})
        require(application["next_action"]["due_time"] is None, "date-only action preserved")
        steps.append("10 date-only next action saved")
        dashboard = response_json(owner, "GET", "/api/v1/dashboard")
        require(dashboard["summary"]["total"] == dashboard["summary"]["submitted"] == 1, "real dashboard totals")
        require(dashboard["actions"]["today"]["total"] == 1, "today action projection")
        require(response_json(other, "GET", "/api/v1/dashboard")["summary"]["total"] == 0, "other user's empty dashboard")
        require(other.get(f"/api/v1/applications/{application['id']}").status_code == 404, "private application isolation")
        steps.append("11 bounded workbench and two-user isolation")
        update_application(next_action={**application["next_action"], "status": "completed"})
        dashboard = response_json(owner, "GET", "/api/v1/dashboard")
        require(dashboard["actions"]["today"]["total"] == 0 and dashboard["actions"]["completed"]["total"] == 1,
            "completed action removed from pending")
        steps.append("12 complete action and reconcile summary")
        response_json(owner, "POST", "/api/v1/auth/logout", status=204)
        require(owner.get("/api/v1/me").status_code == 401, "revoked session")
        login(owner, "s5-journey-0@example.com")
        require(response_json(owner, "GET", f"/api/v1/applications/{application['id']}")["next_action"]["status"] == "completed",
            "application restored after relogin")
        require(response_json(owner, "GET", "/api/v1/profile")["skills"][0]["name"] == "Python", "profile restored after relogin")
        require(response_json(owner, "GET", f"/api/v1/jobs/{job['id']}")["jd_text"] == jd, "JD restored after relogin")
        require(owner.get(f"/api/v1/resumes/{resume['id']}/download").content == content, "PDF restored after relogin")
        steps.append("13 logout, independent relogin and persisted state restoration")
    return {"passed": len(steps), "failed": 0, "skipped": 0, "real_http": True,
        "database": "PostgreSQL", "paid_provider_calls": 0, "steps": steps}


def seed_benchmark(engine, storage, users):
    """Fixtures use model tables; only the journey claims HTTP write coverage."""
    from offerpilot_api.db.models import (Application, ApplicationEvent, CareerProfile, CareerProfileEntry,
        JobJDSnapshot, JobMatch, JobRequirement, ResumeDocument, SavedJob, User)
    from offerpilot_api.job_intelligence.matching import RULE_VERSION
    from offerpilot_api.resume_ingestion.storage import LocalResumeStorage

    now = datetime.now(timezone.utc)
    today = now.astimezone(timezone(timedelta(hours=8))).date()
    pdf = synthetic_pdf()
    object_storage = LocalResumeStorage(storage)
    statuses = ["preparing", "submitted", "assessment", "interview", "offer", "rejected", "withdrawn", "closed"]
    account_ids, application_ids = [], []
    with Session(engine) as db:
        password_hash = db.scalar(select(User.password_hash).limit(1))
    started = time.perf_counter()
    for index in range(users):
        with Session(engine) as db:
            user = User(email_normalized=f"s5-benchmark-{index}@example.com", password_hash=password_hash)
            db.add(user)
            db.flush()
            account_ids.append(user.id)
            profile = CareerProfile(user_id=user.id, revision=1, preferences={})
            db.add(profile)
            db.flush()
            db.add(CareerProfileEntry(profile_id=profile.id, category="skill", value={"name": "Python"}, position=0,
                source_kind="manual", field_sources={"name": "manual"}, evidence=[]))
            for resume_index in range(3):
                resume_id, object_key = uuid4(), f"{uuid4().hex}.pdf"
                db.add(ResumeDocument(id=resume_id, user_id=user.id, file_name=f"synthetic-{resume_index}.pdf",
                    file_path="", file_type="application/pdf", object_key=object_key, byte_size=len(pdf),
                    sha256=hashlib.sha256(pdf).hexdigest(), raw_text="Synthetic S5 Python project.",
                    processing_status="parsed", lifecycle_status="active"))
                db.flush()
                object_storage.save(object_key, io.BytesIO(pdf), claim_token=resume_id.hex)
            for job_index in range(30):
                job = SavedJob(user_id=user.id, title=f"合成岗位 {index}-{job_index}", company="合成公司", location="北京",
                    work_type="internship", source_kind="pasted", content_digest=uuid4().hex, revision=1, jd_version=1,
                    is_favorite=job_index % 2 == 0, created_at=now, updated_at=now)
                db.add(job)
                db.flush()
                snapshot = JobJDSnapshot(job_id=job.id, version=1, jd_text="必须掌握 Python",
                    text_sha256=hashlib.sha256("必须掌握 Python".encode()).hexdigest())
                db.add(snapshot)
                db.flush()
                db.add(JobRequirement(job_id=job.id, snapshot_id=snapshot.id, type="technical_skill",
                    content="必须掌握 Python", priority="must", review_status="confirmed", evidence_text="必须掌握 Python",
                    source_start=0, source_end=11, source_kind="manual", position=0, revision=1))
                match_id = None
                if job_index < 10:
                    result = JobMatch(user_id=user.id, job_id=job.id, profile_revision=1, job_revision=1,
                        jd_version=1, rule_version=RULE_VERSION, schema_version="1.0",
                        evaluation={"schema_version": "1.0", "rule_version": RULE_VERSION, "conclusion": "verify_first",
                            "reasons": ["合成容量数据"], "items": [], "excluded_requirements": [], "next_steps": []})
                    db.add(result)
                    db.flush()
                    match_id = result.id
                if job_index < 20:
                    status = statuses[job_index % len(statuses)]
                    terminal = status in {"rejected", "withdrawn", "closed"}
                    application = Application(user_id=user.id, job_id=job.id, job_title=job.title,
                        company=job.company, location=job.location, match_id=match_id, attempt_number=1,
                        status=status, active_slot=None if terminal else 1, revision=10, notes="合成备注9",
                        action_title="合成下一步", action_notes="合成行动备注", action_due_date=today + timedelta(days=job_index % 10 - 2),
                        action_due_time=None, action_status="cancelled" if terminal else "pending",
                        created_at=now, updated_at=now)
                    db.add(application)
                    db.flush()
                    application_ids.append((user.id, application.id))
                    for revision in range(1, 11):
                        db.add(ApplicationEvent(user_id=user.id, application_id=application.id, revision=revision,
                            type="application_created" if revision == 1 else "notes_changed",
                            changes={"before": None, "after": {"status": status}} if revision == 1 else
                                {"before": f"合成备注{revision - 2}", "after": f"合成备注{revision - 1}"},
                            occurred_at=now + timedelta(seconds=revision), recorded_at=now + timedelta(seconds=revision)))
            db.commit()
    return {"users": users, "resumes": users * 3, "jobs": users * 30, "requirements": users * 30,
        "matches": users * 10, "applications": users * 20, "events": users * 200,
        "fixture_seconds": round(time.perf_counter() - started, 3)}, account_ids, application_ids


def summarize(values):
    ordered = sorted(values)
    return {"count": len(values), "min": round(ordered[0], 3), "median": round(statistics.median(ordered), 3),
        "p95_sample": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3), "max": round(ordered[-1], 3)}


def benchmark(engine, options, storage, metrics_path, users, migration_url):
    scale, account_ids, application_ids = seed_benchmark(engine, storage, users)
    from offerpilot_api.db.session import build_engine
    analyze_engine = build_engine(migration_url)
    try:
        with analyze_engine.begin() as connection:
            connection.execute(text("ANALYZE"))
    finally:
        analyze_engine.dispose()
    results = {}
    with httpx.Client(**options) as owner:
        login(owner, "s5-benchmark-0@example.com")
        cases = {
            "dashboard": ("/api/v1/dashboard?limit=5", "summary"),
            "applications_5": ("/api/v1/applications?page_size=5", "total"),
            "applications_20": ("/api/v1/applications?page_size=20", "total"),
            "jobs_5": ("/api/v1/jobs?page_size=5", "total"),
            "jobs_30": ("/api/v1/jobs?page_size=30", "total"),
            "filtered_actions": ("/api/v1/applications?next_action=pending&sort=next_action_asc", "total"),
            "timeline": (f"/api/v1/applications/{application_ids[0][1]}/events", "total"),
        }
        for name, (path, expected) in cases.items():
            elapsed = []
            begin = len(metrics_path.read_text(encoding="utf-8").splitlines())
            for _ in range(20):
                started = time.perf_counter()
                body = response_json(owner, "GET", path)
                elapsed.append((time.perf_counter() - started) * 1000)
                require(expected in body, "benchmark response shape")
                if name == "dashboard":
                    require(body["summary"]["total"] == 20, "dashboard user isolation at scale")
                    require(all(len(group["items"]) <= 5 for group in body["actions"].values()), "bounded dashboard at scale")
                elif name.startswith("jobs"):
                    require(body["total"] == 30, "job list user isolation at scale")
                elif name.startswith("applications"):
                    require(body["total"] == 20, "application list user isolation at scale")
            request_metrics = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()[begin:]]
            results[name] = {"http_ms": summarize(elapsed), "sql_queries": sorted({record["queries"] for record in request_metrics}),
                "server_ms": summarize([record["server_ms"] for record in request_metrics]),
                "max_statement_ms": round(max(record["max_sql_ms"] for record in request_metrics), 3)}
    # Independent clients/users exercise the actual connection pool concurrently.
    def concurrent_reader(index):
        with httpx.Client(**options) as client:
            login(client, f"s5-benchmark-{index}@example.com")
            samples = []
            for _ in range(10):
                started = time.perf_counter()
                body = response_json(client, "GET", "/api/v1/dashboard?limit=5")
                samples.append((time.perf_counter() - started) * 1000)
                require(body["summary"]["total"] == 20, "concurrent dashboard owner counts")
                owned = {str(item[1]) for item in application_ids if item[0] == account_ids[index]}
                require(all(item["application_id"] in owned for group in body["actions"].values() for item in group["items"]),
                    "concurrent dashboard contains no other user's actions")
            return samples
    concurrency = min(4, users)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        samples = list(executor.map(concurrent_reader, range(concurrency)))
    results["concurrent_dashboard"] = {"concurrency": concurrency, "http_ms": summarize([value for batch in samples for value in batch]),
        "cross_user_isolation": "passed"}
    with engine.connect() as connection:
        active = connection.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND xact_start IS NOT NULL AND pid <> pg_backend_pid() AND now() - xact_start > interval '5 seconds'" )).scalar_one()
        require(active == 0, "no leftover long transactions")
        database_bytes = connection.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
    return {"scale": scale, "queries": results, "database_bytes": database_bytes,
        "long_transactions_over_5_seconds_after_run": active,
        "limits": "Local development machine and small synthetic fixture; this is not production capacity or an SLA."}


def run_database(database, output, users, backup):
    url_value = database.database_url
    checked_url(url_value)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    # Private objects stay available beside the ignored report for the separate
    # backup/restore rehearsal; no existing path is recursively removed.
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    storage = output / "private-objects"
    require(not storage.exists(), "new synthetic storage directory required")
    metrics_path = output / "http-metrics.jsonl"
    require(not metrics_path.exists(), "new report directory required")
    environment = configured_environment(url_value, storage, port, metrics_path)
    os.environ.update(environment)
    from support import migrate
    from offerpilot_api.db.session import build_engine, engine as module_engine
    engine = build_engine(url_value)
    try:
        require(not inspect(engine).get_table_names(), "empty isolated quality database required")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            migrate(database.migration_url)
        from offerpilot_api.account_lifecycle.ledger import DeletionLedger
        DeletionLedger(Path(environment["OFFERPILOT_DELETION_LEDGER_DIR"])).initialize()
        from backup_restore import secure_runtime_role
        runtime_privileges = secure_runtime_role(database)
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version()" )).scalar_one()
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        options = {"base_url": f"http://127.0.0.1:{port}", "timeout": 20, "trust_env": False}
        report = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(), "postgresql": version, "revision": revision,
            "runtime_privileges_before_http": runtime_privileges,
            "machine": {"platform": platform.platform(), "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
                "python": platform.python_version()}, "pool": {"class": type(engine.pool).__name__, "configuration": engine.pool.status()}}
        with running_server(environment, port):
            report["journey"] = journey(engine, options)
            (output / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            report["benchmark"] = benchmark(engine, options, storage, metrics_path, users, database.migration_url)
        (output / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if backup:
            from backup_restore import rehearse
            report["backup_restore"] = rehearse(database.migration_url, storage, output / "backup")
        (output / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PostgreSQL real HTTP journey: 13/13 passed; synthetic query benchmark completed")
    finally:
        engine.dispose()
        module_engine.dispose()


def run(output, users, backup):
    # Imported lazily after CLI/environment validation. The fixture itself guards
    # both maintenance and runtime URLs and generates an unguessable fresh name.
    sys.path.insert(0, str(ROOT / "tests" / "postgres"))
    from support import isolated_database
    with isolated_database(name_prefix="quality", migrate_head=False) as database:
        run_database(database, output, users, backup)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "frontend" / "artifacts" / "s5-1" / "quality")
    parser.add_argument("--users", type=int, choices=range(4, 101), default=100)
    parser.add_argument("--backup", action="store_true", help="also rehearse isolated backup/restore using PostgreSQL tools")
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    try:
        serve(arguments.serve) if arguments.serve else run(arguments.output, arguments.users, arguments.backup)
    except Exception as error:
        # No traceback or exception repr: driver errors can embed connection info.
        print(str(error) if isinstance(error, RuntimeError) else f"PostgreSQL quality failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
