"""Run an isolated migration + Uvicorn + real-HTTP file lifecycle check.

Run with the locked backend interpreter. This script intentionally lives outside
unittest discovery and never connects to the configured development database.
It emits only a fixed success/failure message; temporary paths and credentials
are never printed.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

import httpx
import pymupdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
PASSWORD = "Synthetic-http-lifecycle-password-2026"

# stdin EOF is a private process-control channel, not a public HTTP endpoint.
# The base Python executable avoids the Windows venv launcher leaving its child
# interpreter alive when only the launcher process is terminated.
SERVER_RUNNER = """
import sys
import threading
import uvicorn
from offerpilot_api.main import create_app
from offerpilot_api.db.session import engine

server = uvicorn.Server(uvicorn.Config(
    create_app(), host='127.0.0.1', port=int(sys.argv[1]),
    log_level='critical', access_log=False,
))

def stop_on_stdin_eof():
    sys.stdin.buffer.read()
    server.should_exit = True

control = threading.Thread(target=stop_on_stdin_eof, daemon=True)
control.start()
try:
    server.run()
finally:
    server.should_exit = True
    control.join(timeout=5)
    engine.dispose()
"""


def synthetic_pdf(label: str) -> bytes:
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), f"Synthetic lifecycle resume: {label}")
        return pdf.tobytes()


def require(condition: bool, check: str) -> None:
    if not condition:
        raise RuntimeError(f"HTTP lifecycle check failed: {check}")


def interpreter_environment(database_url: str, upload_dir: Path, port: int) -> dict[str, str]:
    environment = dict(os.environ)
    package_paths = [str(BACKEND_ROOT / "src")]
    site_packages = BACKEND_ROOT / ".venv" / "Lib" / "site-packages"
    if site_packages.is_dir():
        package_paths.append(str(site_packages))
    if environment.get("PYTHONPATH"):
        package_paths.append(environment["PYTHONPATH"])
    environment.update({
        "PYTHONPATH": os.pathsep.join(package_paths),
        "OFFERPILOT_DATABASE_URL": database_url,
        "OFFERPILOT_UPLOAD_DIR": str(upload_dir),
        "OFFERPILOT_DELETION_LEDGER_DIR": str(upload_dir.parent / "deletion-ledger"),
        "OFFERPILOT_ENVIRONMENT": "development",
        "OFFERPILOT_SESSION_COOKIE_SECURE": "false",
        "OFFERPILOT_TRUSTED_ORIGINS": f'["http://127.0.0.1:{port}"]',
    })
    return environment


@contextlib.contextmanager
def running_server(environment: dict[str, str], port: int):
    # Windows needs the base interpreter plus the explicit venv package path
    # above. POSIX must retain the venv interpreter so installed dependencies
    # remain available; its base interpreter does not share those packages.
    interpreter = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    command = [interpreter, "-u", "-c", SERVER_RUNNER, str(port)]
    process = subprocess.Popen(
        command,
        cwd=BACKEND_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    shutdown_failed = False
    try:
        deadline = time.monotonic() + 20
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1, trust_env=False) as probe:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("HTTP lifecycle runner stopped before readiness")
                try:
                    if probe.get("/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError("HTTP lifecycle runner did not become ready")
        yield
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            shutdown_failed = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if shutdown_failed or process.returncode != 0:
            raise RuntimeError("HTTP lifecycle runner did not shut down cleanly")


def register_client(client: httpx.Client, *, email: str, invite_token: str) -> UUID:
    registration = client.post("/api/v1/auth/register", json={
        "email": email, "password": PASSWORD, "invite_token": invite_token,
    })
    require(registration.status_code == 201, "invited registration")
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    require(login.status_code == 200, "cookie-backed login")
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return UUID(login.json()["user"]["id"])


def run() -> None:
    # Reserve a free local port briefly; readiness also checks that our process
    # remains alive, so an unrelated listener cannot be mistaken for this app.
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="offerpilot-http-lifecycle-") as temporary:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'http-lifecycle.db').as_posix()}"
        upload_dir = root / "private-objects"
        environment = interpreter_environment(database_url, upload_dir, port)
        original_environment = dict(os.environ)
        engine = None
        module_engine = None
        try:
            os.environ.update(environment)
            sys.path.insert(0, str(BACKEND_ROOT / "src"))
            # Imports happen only after the isolated database configuration.
            from alembic import command
            from alembic.config import Config
            from sqlalchemy import select
            from sqlalchemy.orm import Session
            from offerpilot_api.account_lifecycle.ledger import DeletionLedger
            from offerpilot_api.core.config import get_settings
            from offerpilot_api.db.models import Profile, ProfileFact, ResumeDocument
            from offerpilot_api.db.session import build_engine, engine as module_engine
            from offerpilot_api.identity.services import create_invitation

            get_settings.cache_clear()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
            DeletionLedger(get_settings().deletion_ledger_dir).initialize()
            engine = build_engine(database_url)
            with Session(engine) as db:
                first_invite = create_invitation(db, email="http-owner@example.com")
                second_invite = create_invitation(db, email="http-other@example.com")
            owner_pdf, other_pdf = synthetic_pdf("owner"), synthetic_pdf("other")
            with running_server(environment, port), contextlib.ExitStack() as stack:
                options = {"base_url": f"http://127.0.0.1:{port}", "timeout": 10, "trust_env": False}
                owner = stack.enter_context(httpx.Client(**options))
                other = stack.enter_context(httpx.Client(**options))
                anonymous = stack.enter_context(httpx.Client(**options))
                owner_id = register_client(owner, email="http-owner@example.com", invite_token=first_invite)
                register_client(other, email="http-other@example.com", invite_token=second_invite)
                uploaded = owner.post("/api/v1/resumes", files={"file": ("owner.pdf", owner_pdf, "application/pdf")})
                other_uploaded = other.post("/api/v1/resumes", files={"file": ("other.pdf", other_pdf, "application/pdf")})
                require(uploaded.status_code == other_uploaded.status_code == 201, "two private uploads")
                resume_id = UUID(uploaded.json()["id"])
                other_resume_id = UUID(other_uploaded.json()["id"])
                endpoint = f"/api/v1/resumes/{resume_id}"
                require("file_path" not in uploaded.text and "object_key" not in uploaded.text, "safe upload metadata")
                with Session(engine) as db:
                    document = db.get(ResumeDocument, resume_id)
                    require(document is not None and document.user_id == owner_id, "persisted owner")
                    require(not document.file_path and bool(document.object_key), "managed private object")
                    require(document.byte_size == len(owner_pdf), "persisted byte size")
                    require(document.sha256 == hashlib.sha256(owner_pdf).hexdigest(), "persisted PDF hash")
                    profile = Profile(resume_id=resume_id, name="Synthetic lifecycle student")
                    fact = ProfileFact(profile=profile, field_name="skills[0]", value="Python", confidence=0.9, source_text="Synthetic Python")
                    db.add(profile)
                    db.commit()
                    profile_id, fact_id = profile.id, fact.id
                download = owner.get(f"{endpoint}/download")
                require(download.status_code == 200 and download.content == owner_pdf, "original PDF download")
                require(download.headers.get("cache-control") == "no-store", "private download caching")
                require(anonymous.get(f"{endpoint}/download").status_code == 401, "anonymous download denied")
                for method, suffix in (("GET", "/download"), ("GET", "/lifecycle"), ("DELETE", "")):
                    require(other.request(method, f"{endpoint}{suffix}").status_code == 404, "cross-user isolation")
                csrf = owner.headers.pop("X-CSRF-Token")
                require(owner.delete(endpoint).status_code == 403, "delete CSRF")
                owner.headers["X-CSRF-Token"] = csrf
                objects = [path for path in upload_dir.rglob("*") if path.is_file() and path.suffix == ".pdf"]
                owner_file = next(path for path in objects if path.read_bytes() == owner_pdf)
                other_file = next(path for path in objects if path.read_bytes() == other_pdf)
                owner_claim = owner_file.with_name(f"{owner_file.name}.claim")
                other_claim = other_file.with_name(f"{other_file.name}.claim")
                require(owner_claim.read_bytes() == resume_id.hex.encode("ascii"), "persisted owner object claim")
                require(other_claim.read_bytes() == other_resume_id.hex.encode("ascii"), "persisted other object claim")
                require(owner.delete(endpoint).status_code == 204, "owner delete")
                require(owner.delete(endpoint).status_code == 204, "idempotent delete")
                for suffix in ("", "/download", "/profile", "/profile/facts"):
                    require(owner.get(f"{endpoint}{suffix}").status_code == 404, "deleted content inaccessible")
                lifecycle = owner.get(f"{endpoint}/lifecycle")
                require(lifecycle.status_code == 200 and lifecycle.json()["lifecycle_status"] == "deleted", "owner tombstone")
                require(lifecycle.json()["processing_status"] is None, "deleted processing removed")
                for sensitive_key in ("file_name", "raw_text", "file_path", "object_key", "sha256"):
                    require(sensitive_key not in lifecycle.json(), "minimal lifecycle response")
                with Session(engine) as db:
                    require(db.get(ResumeDocument, resume_id) is None, "resume hard deletion")
                    require(db.get(Profile, profile_id) is None and db.get(ProfileFact, fact_id) is None, "derived hard deletion")
                    require(db.get(ResumeDocument, other_resume_id) is not None, "other record preserved")
                    require(db.scalar(select(Profile.id).where(Profile.resume_id == resume_id)) is None, "no derived resurrection")
                require(not owner_file.exists(), "original file removed")
                require(not owner_claim.exists(), "original claim removed")
                require(other_file.read_bytes() == other_pdf, "other original preserved")
                require(other_claim.read_bytes() == other_resume_id.hex.encode("ascii"), "other claim preserved")
                require(other.get(f"/api/v1/resumes/{other_resume_id}/download").content == other_pdf, "other download preserved")
                require(owner.post("/api/v1/auth/logout").status_code == 204, "owner logout")
                require(owner.get("/api/v1/me").status_code == 401, "revoked owner session")
                require(other.post("/api/v1/auth/logout").status_code == 204, "other logout")
        finally:
            if engine is not None:
                engine.dispose()
            if module_engine is not None:
                module_engine.dispose()
            if "get_settings" in locals():
                get_settings.cache_clear()
            os.environ.clear()
            os.environ.update(original_environment)
        # TemporaryDirectory cleans only this isolated root after HTTP stops and
        # all SQL/file handles close; an actual cleanup failure is not swallowed.
    print("HTTP file lifecycle smoke passed")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        print("HTTP file lifecycle smoke failed; inspect isolated checks", file=sys.stderr)
        raise SystemExit(1) from None
