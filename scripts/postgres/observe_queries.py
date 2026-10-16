"""Inspect actual domain query plans on a fresh restore of a synthetic dump.

No SQL text, bindings, connection strings or row values are written to reports.
Only the dump produced by quality.py --backup should be supplied to this tool.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import event, inspect, select, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "tests" / "postgres"))


def hardware():
    result = {}
    if os.name == "nt":
        for label, command in {
            "windows_memory": "Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory | ConvertTo-Json -Compress",
            "windows_cpu": "Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress",
        }.items():
            completed = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode == 0:
                result[label] = json.loads(completed.stdout)
    docker = subprocess.run(["docker", "info", "--format", "{{json .NCPU}} {{json .MemTotal}}"],
        capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if docker.returncode == 0:
        cpus, memory = docker.stdout.strip().split()
        result["docker_linux"] = {"cpus": int(cpus), "memory_bytes": int(memory)}
    return result


def plan_summary(plan):
    keep = {"Node Type", "Relation Name", "Index Name", "Join Type", "Plan Rows", "Actual Rows",
        "Actual Loops", "Actual Total Time", "Shared Hit Blocks", "Shared Read Blocks", "Sort Method", "Sort Space Used"}
    result = {key: value for key, value in plan.items() if key in keep}
    if plan.get("Plans"):
        result["Plans"] = [plan_summary(child) for child in plan["Plans"]]
    return result


def observe(engine):
    from sqlalchemy.orm import Session
    from offerpilot_api.application_tracker.dashboard import get_dashboard
    from offerpilot_api.application_tracker.services import list_applications, list_events
    from offerpilot_api.db.models import Application, User
    from offerpilot_api.job_catalog.services import list_jobs

    with Session(engine) as db:
        owner = db.scalar(select(User.id).where(User.email_normalized == "s5-benchmark-0@example.com"))
        application = db.scalar(select(Application.id).where(Application.user_id == owner).limit(1))
    if not isinstance(owner, UUID) or application is None:
        raise RuntimeError("Expected isolated synthetic benchmark data")
    cases = {
        "dashboard": lambda db: get_dashboard(db, user_id=owner, limit=5),
        "applications_20": lambda db: list_applications(db, user_id=owner, page_size=20),
        "jobs_30": lambda db: list_jobs(db, user_id=owner, page_size=30),
        "timeline": lambda db: list_events(db, user_id=owner, application_id=application),
    }
    output = {}
    for label, operation in cases.items():
        captured = []
        def capture(_connection, _cursor, statement, parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                captured.append((statement, parameters))
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with Session(engine) as db:
                operation(db)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        plans = []
        with engine.connect() as connection:
            for statement, parameters in captured:
                result = connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters).scalar_one()[0]
                plans.append({"planning_ms": result["Planning Time"], "execution_ms": result["Execution Time"],
                    "plan": plan_summary(result["Plan"])})
        output[label] = {"domain_query_count": len(captured), "plans": plans}
    inspector = inspect(engine)
    indexes = {name: [{"name": index["name"], "columns": index["column_names"], "unique": index["unique"]}
        for index in inspector.get_indexes(name)] for name in inspector.get_table_names()}
    with engine.connect() as connection:
        settings = {name: connection.exec_driver_sql("SHOW " + name).scalar_one() for name in
            ("server_version", "timezone", "statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout", "max_connections")}
        activity = connection.execute(text("SELECT count(*) AS own_connections, count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction, count(*) FILTER (WHERE xact_start IS NOT NULL AND now() - xact_start > interval '5 seconds') AS transactions_over_5s FROM pg_stat_activity WHERE datname = current_database() AND usename = current_user AND pid <> pg_backend_pid()" )).mappings().one()
    return {"hardware": hardware(), "postgresql_settings": settings, "pool": engine.pool.status(),
        "actual_domain_plans": output, "indexes": indexes, "remaining_app_activity": dict(activity),
        "limits": "One fresh restore after ANALYZE; plans show small synthetic data on a local development machine, not production capacity."}


def run(dump, output, metrics):
    from backup_restore import pg_command, secure_runtime_role
    from offerpilot_api.db.session import build_engine
    from support import isolated_database
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        with isolated_database(name_prefix="query_plans", migrate_head=False) as database:
            pg_command("pg_restore", database.migration_url, dump_path=dump, restore=True)
            privileges = secure_runtime_role(database)
            migration_engine = build_engine(database.migration_url)
            try:
                with migration_engine.begin() as connection:
                    connection.execute(text("ANALYZE"))
            finally:
                migration_engine.dispose()
            report = observe(database.engine)
            report["runtime_privilege_recheck_after_final_benchmark"] = privileges
    records = [json.loads(line) for line in metrics.read_text(encoding="utf-8").splitlines()]
    report["http_statement_observations"] = {"requests": len(records),
        "max_single_statement_ms": max(record["max_sql_ms"] for record in records),
        "requests_with_statement_over_100ms": sum(record["max_sql_ms"] > 100 for record in records),
        "threshold_ms": 100,
        "limitations": "Request-scoped SQLAlchemy timings include driver round trips; PostgreSQL server-wide slow statement logging was not enabled because parameters must not be logged."}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PostgreSQL actual query EXPLAIN/index/transaction observations completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.dump.resolve(), args.output.resolve(), args.metrics.resolve())
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else f"Query observation failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
