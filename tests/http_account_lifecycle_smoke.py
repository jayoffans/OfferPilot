"""Real loopback HTTP: streamed ZIP, revoke, restart recovery and isolation."""
import contextlib
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from uuid import UUID, uuid4
import httpx

from http_file_lifecycle_smoke import (
    BACKEND_ROOT, PASSWORD, interpreter_environment, register_client, require,
    running_server, synthetic_pdf,
)


def run():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    original = dict(os.environ)
    engine = module_engine = None
    with tempfile.TemporaryDirectory(prefix="offerpilot-http-account-") as temporary:
        root = Path(temporary)
        environment = interpreter_environment(f"sqlite:///{(root / 'test.db').as_posix()}", root / "objects", port)
        environment.update(OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED="false", OFFERPILOT_MOCK_EXTRACTION_ENABLED="false",
                           OFFERPILOT_EXPORT_DIR=str(root / "exports"), OFFERPILOT_DELETION_LEDGER_DIR=str(root / "registry"))
        try:
            os.environ.update(environment)
            from alembic import command
            from alembic.config import Config
            from sqlalchemy.orm import Session
            from offerpilot_api.core.config import get_settings
            from offerpilot_api.db.session import build_engine, engine as module_engine
            from offerpilot_api.db.models import User, AccountDeletion
            from offerpilot_api.identity.services import create_invitation
            get_settings.cache_clear()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
            engine = build_engine(environment["OFFERPILOT_DATABASE_URL"])
            with Session(engine) as db:
                invitations = [create_invitation(db, email=email) for email in ("account-a@example.com", "account-b@example.com")]
            options = dict(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False)
            with contextlib.ExitStack() as stack:
                owner, other = stack.enter_context(httpx.Client(**options)), stack.enter_context(httpx.Client(**options))
                with running_server(environment, port):
                    owner_id = register_client(owner, email="account-a@example.com", invite_token=invitations[0])
                    other_id = register_client(other, email="account-b@example.com", invite_token=invitations[1])
                    pdf = synthetic_pdf("private account export")
                    uploaded = owner.post("/api/v1/resumes", files={"file": ("synthetic.pdf", pdf, "application/pdf")})
                    require(uploaded.status_code == 201, "private upload")
                    resume_id = uploaded.json()["id"]
                    created = owner.post("/api/v1/me/export", json={"password": PASSWORD, "idempotency_key": uuid4().hex})
                    require(created.status_code == 202, "reauthenticated export creation")
                    export_id = created.json()["id"]
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        status = owner.get(f"/api/v1/me/export/{export_id}").json()
                        if status["status"] == "ready":
                            break
                        time.sleep(.2)
                    require(status["status"] == "ready", "durable export worker completion")
                    downloaded = owner.get(f"/api/v1/me/export/{export_id}/download")
                    require(downloaded.status_code == 200, "private streaming download")
                    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                        require(archive.read(f"resumes/{resume_id}.pdf") == pdf, "exact original bytes")
                        manifest = json.loads(archive.read("manifest.json"))
                        require(manifest["complete"] and manifest["schema_version"] == "offerpilot-user-export/1.0", "explicit export schema")
                    require(other.get(f"/api/v1/me/export/{export_id}/download").status_code == 404, "cross-user ZIP denied")
                    csrf = owner.headers.pop("X-CSRF-Token")
                    body = {"password": PASSWORD, "confirmation": "注销账户", "idempotency_key": uuid4().hex}
                    require(owner.post("/api/v1/me/deletion", json=body).status_code == 403, "deletion CSRF")
                    owner.headers["X-CSRF-Token"] = csrf
                    deleted = owner.post("/api/v1/me/deletion", json=body)
                    require(deleted.status_code == 202, "reauthenticated confirmed deletion")
                    require(owner.get("/api/v1/me").status_code == 401, "immediate revocation")
                # Restart the actual service: persistent deletion intent and
                # independent registry, not in-memory request work, are enough.
                with running_server(environment, port):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        status = owner.get("/api/v1/me/deletion/status").json()
                        if status["status"] == "completed":
                            break
                        time.sleep(.2)
                    require(status["status"] == "completed", "restart-safe cleanup completion")
                    require(other.get("/api/v1/me").status_code == 200, "other user access preserved")
                    require(owner.get(f"/api/v1/me/export/{export_id}/download").status_code == 401, "deleted exports inaccessible")
                    with Session(engine) as db:
                        require(db.get(User, owner_id) is None and db.get(User, other_id) is not None, "physical account row isolation")
                        require(db.get(AccountDeletion, UUID(deleted.json()["id"])).status == "completed", "durable final receipt")
                    require(not list((root / "objects").glob("*")), "PDF and sidecars removed")
                    require(not list((root / "exports").glob("*")), "ZIP and temporary files removed")
        finally:
            if engine is not None:
                engine.dispose()
            if module_engine is not None:
                module_engine.dispose()
            if "get_settings" in locals():
                get_settings.cache_clear()
            os.environ.clear()
            os.environ.update(original)
    print("HTTP account lifecycle smoke passed")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        print("HTTP account lifecycle smoke failed; inspect isolated assertions", file=sys.stderr)
        raise SystemExit(1) from None
