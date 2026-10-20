"""Synthetic demo records through real APIs; Mock candidates always stay pending."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pymupdf

from offerpilot_api.db.session import SessionLocal, engine, verify_runtime_database_role
from offerpilot_api.identity.services import create_invitation
from offerpilot_api.main import create_app
from offerpilot_api.resume_ingestion.synthetic_fixtures import SYNTHETIC_RESUME_TEXT

EMAIL = "portfolio-demo@example.com"


def request(client, method, path, expected=200, **kwargs):
    result = client.request(method, "/api/v1" + path, **kwargs)
    if result.status_code != expected:
        # Never include response bodies, login inputs or DB URLs in output.
        raise ValueError(f"Synthetic demo API returned {result.status_code} for {method} {path}")
    return result.json() if result.content else None


def login(client, password):
    result = request(client, "POST", "/auth/login", json={"email": EMAIL, "password": password})
    client.headers["X-CSRF-Token"] = result["csrf_token"]


def seed(directory: Path):
    from manage import write_private
    marker = directory / "seed.json"
    credentials_path = directory / "demo-credentials.json"
    if marker.exists():
        print("Synthetic records already seeded; no records changed.")
        verify(directory)
        return
    if credentials_path.exists():
        raise ValueError("A previous seed was interrupted; preserve it and use a new --profile for a fresh demo")
    password = "Demo-" + secrets.token_urlsafe(24)
    write_private(credentials_path, {"email": EMAIL, "password": password,
                                     "notice": "Local synthetic demo only. Do not publish this file."})
    with SessionLocal() as db:
        invitation = create_invitation(db, email=EMAIL)
    with TestClient(create_app()) as client:
        request(client, "POST", "/auth/register", expected=201,
                json={"email": EMAIL, "password": password, "invite_token": invitation})
        login(client, password)
        profile = request(client, "PUT", "/profile", json={
            "revision": 0,
            "education": [{"school": "合成大学", "degree": "本科", "major": "计算机科学与技术",
                           "start_date": "2023-09", "end_date": "2027-06", "end_date_kind": "expected"}],
            "skills": [{"name": "Python", "level": "熟悉"}, {"name": "SQL", "level": "熟悉"}],
            "projects": [{"name": "合成课程选课系统", "role": "后端开发", "description": "合成演示：编写课程查询接口及测试。",
                          "start_date": "2025-03", "end_date": "2025-06"}],
            "experiences": [],
        })
        profile = request(client, "PUT", "/profile/preferences", json={"revision": profile["revision"],
                          "preferences": {"locations": ["上海", "杭州"], "work_types": ["internship"], "target_roles": ["后端开发"]}})
        pdf = pymupdf.open()
        try:
            # Match the approved fixture without clipping its longest line.
            page = pdf.new_page(width=1190, height=842)
            for index, line in enumerate(SYNTHETIC_RESUME_TEXT.splitlines()):
                page.insert_text((40, 50 + index * 24), line, fontname="china-s", fontsize=10)
            pdf_bytes = pdf.tobytes()
        finally:
            pdf.close()
        uploaded = request(client, "POST", "/resumes", expected=201,
                           files={"file": ("synthetic-demo-resume.pdf", pdf_bytes, "application/pdf")})
        capability = request(client, "GET", f'/resumes/{uploaded["id"]}/extraction')
        run = request(client, "POST", f'/resumes/{uploaded["id"]}/extraction-runs', expected=202,
                      json={"idempotency_key": "portfolio-mock-v1", "mode": "mock", "source_version": capability["source_version"]})
        deadline = time.monotonic() + 30
        while run["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.1)
            run = request(client, "GET", f'/extraction-runs/{run["id"]}')
        if run["status"] != "succeeded" or not run["test_result"]:
            raise ValueError("Synthetic Mock run did not complete")
        profile = request(client, "GET", "/profile")
        jobs, applications = [], []
        today = datetime.now(timezone(timedelta(hours=8))).date()
        for index, (title, action, offset, status) in enumerate([
            ("后端开发实习生（合成）", "准备 Python 技术面试（合成）", 0, "interview"),
            ("数据平台实习生（合成）", "补充项目说明（合成）", -1, "submitted"),
            ("软件开发实习生（合成）", "核对岗位要求（合成）", 3, "preparing"),
        ]):
            job = request(client, "POST", "/jobs", expected=201, json={
                "idempotency_key": f"portfolio-job-{index}-v1", "title": title, "company": "合成示例科技",
                "location": "上海", "work_type": "internship",
                "jd_text": "合成招聘 JD，仅供演示。\n本科及以上学历。\n必须熟悉 Python。\n熟悉 Java 优先。",
            })
            for requirement in job["requirements"]:
                job = request(client, "POST", f'/jobs/{job["id"]}/requirements/{requirement["id"]}/review',
                              json={"revision": job["revision"], "action": "confirm"})
            match = request(client, "POST", f'/jobs/{job["id"]}/matches', json={"profile_revision": profile["revision"],
                            "job_revision": job["revision"], "idempotency_key": f"portfolio-match-{index}-v1"})
            created = request(client, "POST", "/applications", expected=201,
                              json={"job_id": job["id"], "job_revision": job["revision"], "match_id": match["id"],
                                    "idempotency_key": f"portfolio-application-{index}-v1"})
            application = request(client, "PUT", f'/applications/{created["id"]}', json={
                "revision": created["revision"], "idempotency_key": str(uuid4()), "status": status,
                "submitted_at": None, "notes": "合成演示记录；没有实际申请、招聘方或外部提交。",
                "next_action": {"title": action, "notes": "合成行动", "due_date": str(today + timedelta(days=offset)),
                                "due_time": None, "status": "pending"},
            })
            jobs.append(job["id"])
            applications.append(application["id"])
        request(client, "POST", "/auth/logout", expected=204)
    write_private(marker, {"version": 1, "resume_id": uploaded["id"], "mock_run_id": run["id"],
                           "jobs": jobs, "applications": applications, "seed_date_shanghai": str(today),
                           "provider": "mock", "paid_calls": 0})
    print(f"Synthetic demo created; local login details: {credentials_path}")
    print("AI candidates are explicitly Mock test results and all remain pending. Paid provider calls: 0.")
    verify(directory)


def verify(directory: Path):
    marker = json.loads((directory / "seed.json").read_text(encoding="utf-8"))
    credentials = json.loads((directory / "demo-credentials.json").read_text(encoding="utf-8"))
    if engine.dialect.name == "postgresql":
        verify_runtime_database_role(engine)
    with TestClient(create_app()) as client:
        health = client.get("/health")
        if health.status_code != 200:
            raise ValueError("Local health failed")
        login(client, credentials["password"])
        dashboard = request(client, "GET", "/dashboard")
        profile = request(client, "GET", "/profile")
        run = request(client, "GET", f'/extraction-runs/{marker["mock_run_id"]}')
        if dashboard["summary"]["total"] < 3 or not profile["education"] or not profile["skills"]:
            raise ValueError("Synthetic demo data is incomplete")
        if run["mode"] != "mock" or not run["test_result"] or not all(c["status"] == "pending" for c in profile["candidates"]):
            raise ValueError("Demo candidates must remain pending and marked Mock")
        request(client, "POST", "/auth/logout", expected=204)
    print(f"Verified real API login, health, profile, 3+ applications and pending Mock candidates ({engine.dialect.name}).")
