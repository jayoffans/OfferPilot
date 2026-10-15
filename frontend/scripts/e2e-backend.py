"""Isolated real API for Playwright; never imported by the application.

Only this launcher adds authenticated loopback fixture controls. It migrates a
new temporary SQLite database, uses synthetic data, suppresses request logs and
passes its random control secret to the parent through a private stdout pipe.
Closing stdin gracefully stops HTTP and removes this launcher's temporary root.
"""

import contextlib
import hmac
import io
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def run() -> None:
    api_port = int(os.environ.get("OFFERPILOT_E2E_API_PORT", "8001"))
    web_port = int(os.environ.get("OFFERPILOT_E2E_WEB_PORT", "3001"))
    if not (1024 <= api_port <= 65535 and 1024 <= web_port <= 65535):
        raise ValueError("Invalid isolated test ports")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", api_port))
    with tempfile.TemporaryDirectory(prefix="offerpilot-web-e2e-") as temporary:
        private_root = Path(temporary).resolve()
        storage_root = private_root / "objects"
        os.environ.update({
            "OFFERPILOT_DATABASE_URL": f"sqlite:///{(private_root / 'e2e.db').as_posix()}",
            "OFFERPILOT_UPLOAD_DIR": str(storage_root),
            "OFFERPILOT_ENVIRONMENT": "development",
            "OFFERPILOT_SESSION_COOKIE_SECURE": "false",
            "OFFERPILOT_TRUSTED_ORIGINS": json.dumps([f"http://127.0.0.1:{web_port}"]),
            "OFFERPILOT_AUTH_IP_ATTEMPT_LIMIT": "1000",
            "OFFERPILOT_AUTH_REGISTRATION_ATTEMPT_LIMIT": "100",
            "OFFERPILOT_MOCK_EXTRACTION_ENABLED": "true",
            "OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED": "true",
        })
        sys.path.insert(0, str(BACKEND / "src"))
        import pymupdf
        import uvicorn
        from alembic import command
        from alembic.config import Config
        from fastapi import HTTPException, Request
        from fastapi.responses import Response
        from sqlalchemy import delete, func, select, update
        from sqlalchemy.orm import Session
        from offerpilot_api.db.models import (
            AuthSession, AuthThrottle, Profile, ProfileFact,
            ResumeDocument, ResumeFileOperation, User,
        )
        from offerpilot_api.db.session import engine
        from offerpilot_api.identity.services import create_invitation
        from offerpilot_api.main import create_app

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
        with engine.connect() as connection:
            revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        if revision != "0009_application_tracker":
            raise RuntimeError("E2E migration baseline changed; review the harness")

        app = create_app()
        # The paid-mode transport is replaced only inside this isolated test
        # launcher. It cannot create a SDK client or access the provider network.
        from offerpilot_api.agent_runtime.llm_provider import LLMProviderError, ProviderUsage
        from offerpilot_api.resume_ingestion.synthetic_fixtures import fixture_cases, fixture_response
        app.state.ai_e2e_mode = "default"

        class FreeDeepSeekFaultProvider:
            last_usage = None
            def generate_json(self, **_kwargs):
                if app.state.ai_e2e_mode == "timeout":
                    raise LLMProviderError("Synthetic timeout", code="provider_timeout")
                self.last_usage = ProviderUsage(100, 200, 300)
                return fixture_response(fixture_cases()[0])

        app.state.deepseek_provider_factory = lambda **_kwargs: FreeDeepSeekFaultProvider()
        from offerpilot_api.agent_runtime.providers.mock import MockResumeProvider
        from offerpilot_api.agent_runtime import extraction_runs
        from offerpilot_api.resume_ingestion.synthetic_fixtures import SYNTHETIC_RESUME_TEXT
        blocked_release = threading.Event()
        blocked_started = threading.Event()
        blocked_returned = threading.Event()
        blocked_batch_completed = threading.Event()
        original_process_batch = extraction_runs.process_extraction_batch

        def observed_process_batch(*args, **kwargs):
            # Observe completion of the real finalization transaction; do not
            # change execution, results, or the application's business checks.
            result = original_process_batch(*args, **kwargs)
            if blocked_returned.is_set():
                blocked_batch_completed.set()
            return result

        extraction_runs.process_extraction_batch = observed_process_batch

        class FaultProvider:
            def __init__(self, mode: str):
                self.mode = mode

            def generate_json(self, *, system_prompt: str, user_prompt: str) -> str:
                if self.mode == "blocked":
                    blocked_started.set()
                    if not blocked_release.wait(15):
                        raise RuntimeError("Isolated provider gate timed out")
                if self.mode == "invalid":
                    return "{not valid JSON"
                result = MockResumeProvider().generate_json(system_prompt=system_prompt, user_prompt=user_prompt)
                if self.mode == "blocked":
                    blocked_returned.set()
                return result
        control_secret = secrets.token_urlsafe(32)
        objects: dict[UUID, Path] = {}
        fault_backups: dict[UUID, tuple[Path, str]] = {}
        stop_event = threading.Event()
        failed_event = threading.Event()
        server_lock = threading.Lock()
        server: uvicorn.Server | None = None
        server_thread: threading.Thread | None = None

        def controlled(request: Request) -> None:
            supplied = request.headers.get("x-offerpilot-e2e-control", "")
            if request.client is None or request.client.host != "127.0.0.1" or not hmac.compare_digest(supplied, control_secret):
                raise HTTPException(status_code=404)
            if request.headers.get("origin"):
                raise HTTPException(status_code=404)

        def synthetic_email(value: object) -> str:
            if not isinstance(value, str) or not re.fullmatch(r"e2e-[a-z0-9-]+@example\.com", value):
                raise HTTPException(status_code=400)
            return value

        # These paths are absent from create_app() and from production OpenAPI.
        @app.post("/__e2e/ai-mode", include_in_schema=False)
        async def ai_mode(request: Request):
            controlled(request)
            mode = (await request.json()).get("mode")
            if mode not in {"default", "timeout"}:
                raise HTTPException(status_code=400)
            app.state.ai_e2e_mode = mode
            return {"mode": mode, "real_paid_calls": 0}

        @app.get("/__e2e/ready", include_in_schema=False)
        async def private_readiness(request: Request):
            controlled(request)
            return {"ready": True, "revision": revision}

        @app.post("/__e2e/invite", include_in_schema=False)
        async def invite(request: Request):
            controlled(request)
            email = synthetic_email((await request.json()).get("email"))
            with Session(engine) as db:
                token = create_invitation(db, email=email)
            return {"invite_token": token}

        @app.post("/__e2e/pdf", include_in_schema=False)
        async def pdf(request: Request):
            controlled(request)
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "Synthetic OfferPilot E2E resume. Python project evidence.")
                page.insert_text((72, 94), "Not proficient in Java.")
                content = document.tobytes()
            return Response(content=content, media_type="application/pdf")

        @app.post("/__e2e/extraction-pdf", include_in_schema=False)
        async def extraction_pdf(request: Request):
            controlled(request)
            with pymupdf.open() as document:
                page = document.new_page(width=1190, height=842)
                for index, line in enumerate(SYNTHETIC_RESUME_TEXT.splitlines()):
                    page.insert_text((72, 72 + index * 24), line, fontname="china-s", fontsize=11)
                content = document.tobytes()
            return Response(content=content, media_type="application/pdf")

        @app.post("/__e2e/extraction-mode", include_in_schema=False)
        async def extraction_mode(request: Request):
            controlled(request)
            mode = (await request.json()).get("mode")
            if mode not in {"default", "paused", "invalid", "blocked"}:
                raise HTTPException(status_code=400)
            if mode != "blocked":
                blocked_release.set()
            else:
                blocked_release.clear()
                blocked_started.clear()
                blocked_returned.clear()
                blocked_batch_completed.clear()
            app.state.extraction_dispatcher_enabled = mode != "paused"
            app.state.extraction_provider_factory = lambda: FaultProvider(mode)
            return {"ok": True, "provider_blocked": blocked_started.is_set()}

        @app.post("/__e2e/extraction-state", include_in_schema=False)
        async def extraction_state(request: Request):
            controlled(request)
            return {"provider_started": blocked_started.is_set(), "batch_completed": blocked_batch_completed.is_set()}

        @app.post("/__e2e/reset-throttle", include_in_schema=False)
        async def reset_throttle(request: Request):
            controlled(request)
            with Session(engine) as db:
                db.execute(delete(AuthThrottle))
                db.commit()
            return {"ok": True}

        @app.post("/__e2e/expire-session", include_in_schema=False)
        async def expire_session(request: Request):
            controlled(request)
            email = synthetic_email((await request.json()).get("email"))
            with Session(engine) as db:
                user_id = db.scalar(select(User.id).where(User.email_normalized == email))
                db.execute(update(AuthSession).where(AuthSession.user_id == user_id).values(
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                ))
                db.commit()
            return {"ok": True}

        @app.post("/__e2e/resume/{resume_id}/derived", include_in_schema=False)
        async def derived(resume_id: UUID, request: Request):
            controlled(request)
            with Session(engine) as db:
                if db.get(ResumeDocument, resume_id) is None:
                    raise HTTPException(status_code=404)
                profile = Profile(resume_id=resume_id, name="Synthetic test fixture")
                profile.facts.append(ProfileFact(
                    field_name="skills[0]", value="Python", confidence=0.9,
                    source_text="Synthetic fixture; not generated by AI",
                ))
                db.add(profile)
                db.commit()
            return {"ok": True}

        @app.post("/__e2e/resume/{resume_id}/candidates", include_in_schema=False)
        async def candidates(resume_id: UUID, request: Request):
            controlled(request)
            # Exercise the same validated domain entry point intended for S2-2.
            # Fixed synthetic data only; this is not a public extraction API.
            from offerpilot_api.profile.services import ingest_profile_candidates

            with Session(engine) as db:
                document = db.get(ResumeDocument, resume_id)
                if document is None or document.user_id is None:
                    raise HTTPException(status_code=404)
                profile = ingest_profile_candidates(
                    db, user_id=document.user_id, resume_id=resume_id,
                    batch_key="synthetic-e2e-v1",
                    payload={
                        "schema_version": "1.0", "extraction_version": "synthetic-e2e-v1",
                        "candidates": [
                            {"category": "skill", "value": {"name": "Python", "level": None},
                             "evidence": [{"field": "name", "source_text": "Python project evidence."}]},
                            {"category": "skill", "value": {"name": "Java", "level": "proficient"},
                             "evidence": [{"field": "name", "source_text": "Java"},
                                          {"field": "level", "source_text": "proficient"}]},
                            {"category": "skill", "value": {"name": "Go", "level": None}, "evidence": []},
                        ],
                    },
                )
                return profile.model_dump(mode="json")

        @app.post("/__e2e/resume/{resume_id}/inspect", include_in_schema=False)
        async def inspect(resume_id: UUID, request: Request):
            controlled(request)
            with Session(engine) as db:
                document = db.get(ResumeDocument, resume_id)
                operation = db.get(ResumeFileOperation, resume_id)
                if document is not None and document.object_key:
                    # Only backend-generated keys are resolved under this root.
                    objects[resume_id] = storage_root / document.object_key
                target = objects.get(resume_id)
                profile_ids = select(Profile.id).where(Profile.resume_id == resume_id)
                return {
                    "document_exists": document is not None,
                    "text_exists": bool(document and document.raw_text),
                    "profile_count": db.scalar(select(func.count()).select_from(Profile).where(Profile.resume_id == resume_id)),
                    "fact_count": db.scalar(select(func.count()).select_from(ProfileFact).where(ProfileFact.profile_id.in_(profile_ids))),
                    "object_exists": bool(target and target.exists()),
                    "claim_exists": bool(target and target.with_name(target.name + ".claim").exists()),
                    "partial_exists": bool(target and target.with_name(target.name + ".part").exists()),
                    "operation_status": operation.status if operation else None,
                    "operation_has_reference": bool(operation and (operation.object_key or operation.legacy_path)),
                    "lifecycle_status": document.lifecycle_status if document else None,
                }

        @app.post("/__e2e/resume/{resume_id}/fault", include_in_schema=False)
        async def fault(resume_id: UUID, request: Request):
            controlled(request)
            mode = (await request.json()).get("mode")
            if mode not in {"delete", "integrity", "restore"}:
                raise HTTPException(status_code=400)
            if mode == "restore":
                previous = fault_backups.pop(resume_id, None)
                if previous:
                    backup, previous_mode = previous
                    target = objects[resume_id]
                    if previous_mode == "delete":
                        target.rmdir()
                    else:
                        target.unlink()
                    backup.replace(target)
                return {"ok": True}
            with Session(engine) as db:
                document = db.get(ResumeDocument, resume_id)
                if document is None or not document.object_key or resume_id in fault_backups:
                    raise HTTPException(status_code=409)
                target = storage_root / document.object_key
                objects[resume_id] = target
                backup = private_root / f"{resume_id.hex}.backup"
                target.replace(backup)
                if mode == "delete":
                    # Actual filesystem condition: a directory cannot be unlinked
                    # as a managed regular PDF. No business response is mocked.
                    target.mkdir()
                else:
                    content = bytearray(backup.read_bytes())
                    content[-1] ^= 1
                    target.write_bytes(content)
                fault_backups[resume_id] = (backup, mode)
            return {"ok": True}

        def start_server() -> None:
            nonlocal server, server_thread
            with server_lock:
                if stop_event.is_set():
                    return
                launched = uvicorn.Server(uvicorn.Config(
                    app, host="127.0.0.1", port=api_port, log_level="critical", access_log=False,
                ))
                server = launched

                def serve():
                    try:
                        launched.run()
                    except BaseException:
                        failed_event.set()
                        stop_event.set()

                server_thread = threading.Thread(target=serve, daemon=True)
                server_thread.start()

        @app.post("/__e2e/backend-outage", include_in_schema=False)
        async def outage(request: Request):
            controlled(request)

            def restart_after_outage():
                time.sleep(0.3)  # Allow this control response to finish.
                with server_lock:
                    active_server, active_thread = server, server_thread
                    if active_server:
                        active_server.should_exit = True
                if active_thread:
                    active_thread.join(timeout=10)
                if not stop_event.wait(6):
                    start_server()

            threading.Thread(target=restart_after_outage, daemon=True).start()
            return {"ok": True}

        def stop_on_stdin_eof():
            sys.stdin.buffer.read()
            blocked_release.set()
            stop_event.set()
            with server_lock:
                if server:
                    server.should_exit = True

        threading.Thread(target=stop_on_stdin_eof, daemon=True).start()
        start_server()
        # Private machine protocol: parent captures this line, never reports it.
        print(json.dumps({"control_secret": control_secret, "api_port": api_port}), flush=True)
        try:
            while not stop_event.wait(0.1):
                pass
        finally:
            with server_lock:
                if server:
                    server.should_exit = True
                thread = server_thread
            if thread:
                thread.join(timeout=15)
                if thread.is_alive():
                    raise RuntimeError("Isolated API did not stop cleanly")
            engine.dispose()
        if failed_event.is_set():
            raise RuntimeError("Isolated E2E API listener failed")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        # Avoid traceback paths, SQL, tokens and request contents in runner logs.
        print("Isolated E2E API failed; check local dependencies and free test ports", file=sys.stderr)
        raise SystemExit(1) from None
