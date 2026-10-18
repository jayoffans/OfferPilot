"""Synthetic S5-2 restore rehearsal; never restore over a supplied database.

The deletion ledger deliberately remains outside the database/file snapshot.
Every database is freshly created by the reviewed PostgreSQL S5 fixture, and
the report contains counts, digests and booleans only. No credentials, session
tokens, email addresses or row contents are written to the report.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT))

from fastapi import UploadFile
from fastapi.testclient import TestClient
import pymupdf
from sqlalchemy import inspect, select
from starlette.datastructures import Headers

from offerpilot_api.application_tracker import services as applications
from offerpilot_api.application_tracker.schemas import ApplicationCreateRequest, ApplicationUpdateRequest
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import AuthSession, Profile, ProfileFact, ResumeDocument, User
from offerpilot_api.db.session import get_db
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, hash_password, new_token, token_hash
from offerpilot_api.identity.services import create_invitation
from offerpilot_api.job_catalog import services as jobs
from offerpilot_api.job_catalog.schemas import JobCreateRequest
from offerpilot_api.job_intelligence.match_api_schemas import JobMatchCreateRequest
from offerpilot_api.job_intelligence.match_services import create_match
from offerpilot_api.main import create_app
from offerpilot_api.profile import services as profiles
from offerpilot_api.profile.schemas import ProfileSaveRequest
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, StorageError
from offerpilot_api.services.resume import save_resume_document
from scripts.postgres.backup_restore import pg_command, require, secure_runtime_role
from tests.postgres.support import isolated_database

PASSWORD = "Synthetic-lifecycle-password-2026"


def client_for(database, settings):
    """Use the actual API dependencies without starting a second worker."""
    app = create_app()

    def sessions():
        with database.session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False)


def synthetic_account(database, settings, label):
    """Persist the full existing ownership graph through its domain services."""
    owner = uuid4()
    email = f"restore-{label}-{owner.hex}@example.com"
    token = new_token()
    storage = LocalResumeStorage(settings.upload_dir)
    with database.session_factory() as db:
        db.add(User(id=owner, email_normalized=email, password_hash=hash_password(PASSWORD)))
        db.commit()
        db.add(AuthSession(user_id=owner, token_hash=token_hash(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()
        create_invitation(db, email=email)
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), f"Synthetic {label} resume: Python")
            original = pdf.tobytes()
        upload = UploadFile(file=io.BytesIO(original), filename=f"synthetic-{label}.pdf",
            headers=Headers({"content-type": "application/pdf"}))
        try:
            document = save_resume_document(upload, db, settings.upload_dir, user_id=owner, storage=storage)
        finally:
            upload.file.close()
        resume_id = document.id
        profile = profiles.save_profile(db, user_id=owner, payload=ProfileSaveRequest(
            revision=0, education=[], skills=[{"name": "Python"}], projects=[], experiences=[]))
        profiles.ingest_profile_candidates(db, user_id=owner, resume_id=resume_id,
            batch_key=uuid4().hex, payload={"schema_version": "1.0", "extraction_version": "restore-synthetic-1",
                "candidates": [{"category": "skill", "value": {"name": "Python"},
                    "evidence": [{"field": "name", "source_text": "Python"}]}]})
        legacy_profile = Profile(resume_id=resume_id, education=[], skills=["Python"], projects=[], internships=[])
        db.add(legacy_profile)
        db.flush()
        db.add(ProfileFact(profile_id=legacy_profile.id, field_name="skills", value={"name": "Python"},
            confidence=0.5, source_text="Python"))
        db.commit()
        profile = profiles.get_profile(db, user_id=owner)
        job = jobs.create_job(db, user_id=owner, payload=JobCreateRequest(
            idempotency_key=uuid4().hex, title=f"合成恢复验收岗位 {label}", jd_text="必须掌握 Python",
            requirements=[{"type": "technical_skill", "content": "必须掌握 Python", "priority": "must",
                "review_status": "confirmed", "evidence_text": "必须掌握 Python"}]))
        match = create_match(db, user_id=owner, job_id=job.id, payload=JobMatchCreateRequest(
            profile_revision=profile.revision, job_revision=job.revision, idempotency_key=uuid4().hex))
        application = applications.create_application(db, user_id=owner, payload=ApplicationCreateRequest(
            job_id=job.id, job_revision=job.revision, match_id=match.id, idempotency_key=uuid4().hex))
        applications.update_application(db, user_id=owner, application_id=application.id,
            payload=ApplicationUpdateRequest(revision=application.revision, idempotency_key=uuid4().hex,
                status="submitted", submitted_at=None, notes=f"合成备注 {label}",
                next_action={"title": "准备合成面试", "notes": "仅恢复验收"}))
        object_key = db.get(ResumeDocument, resume_id).object_key
    from offerpilot_api.account_lifecycle.exports import create_export, execute_export
    with database.session_factory() as db:
        export = create_export(db, user_id=owner, idempotency_key=uuid4().hex, settings=settings)
        export_id = export.id
        require(execute_export(db, export_id=export_id, storage=storage, settings=settings),
            "synthetic account export completes")
    export_path = settings.export_dir / f"{export_id.hex}.zip"
    return {"user_id": owner, "email": email, "token": token, "resume_id": resume_id,
        "object_key": object_key, "original_sha256": hashlib.sha256(original).hexdigest(),
        "export_id": export_id, "export_sha256": hashlib.sha256(export_path.read_bytes()).hexdigest()}


def scoped_fingerprint(engine, owner: UUID, email: str):
    """Hash this user's rows, following existing ownership FK paths."""
    tables = Base.metadata.tables
    scopes = {"users": tables["users"].c.id == owner}
    for name, table in tables.items():
        if "user_id" in table.c:
            scopes[name] = table.c.user_id == owner
    scopes["registration_invitations"] = tables["registration_invitations"].c.email_normalized == email
    while True:
        changed = False
        for name, table in tables.items():
            if name in scopes:
                continue
            for foreign_key in table.foreign_keys:
                parent = foreign_key.column.table
                if parent.name in scopes:
                    scopes[name] = foreign_key.parent.in_(select(foreign_key.column).where(scopes[parent.name]))
                    changed = True
                    break
        if not changed:
            break
    result = {}
    with engine.connect() as connection:
        for name, scope in sorted(scopes.items()):
            rows = connection.execute(select(tables[name]).where(scope)).mappings().all()
            canonical = sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str,
                separators=(",", ":")) for row in rows)
            result[name] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()}
    return result


def assert_revoked(database, settings, account):
    with client_for(database, settings) as client:
        client.cookies.set(SESSION_COOKIE_NAME, account["token"])
        require(client.get("/api/v1/me").status_code in {401, 403}, "restored old session remains inaccessible")
        client.cookies.clear()
        require(client.post("/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}).status_code
            in {401, 403}, "restored account cannot log in")


class FailingDeletionStorage:
    """Inject one physical failure, preserving the reviewed storage adapter."""
    def __init__(self, root, object_key):
        self.delegate = LocalResumeStorage(root)
        self.object_key = object_key
        self.failed = False

    def delete(self, object_key, **kwargs):
        if object_key == self.object_key and not self.failed:
            self.failed = True
            raise StorageError("synthetic_restore_cleanup_failure", "Synthetic cleanup failure")
        return self.delegate.delete(object_key, **kwargs)

    def read(self, *args, **kwargs):
        return self.delegate.read(*args, **kwargs)

    def save(self, *args, **kwargs):
        return self.delegate.save(*args, **kwargs)


def rehearse(output: Path):
    """Delete after a snapshot, restore that snapshot, and safely replay deletion."""
    from offerpilot_api.account_lifecycle.deletion import replay_deletion_registry
    from offerpilot_api.account_lifecycle.ledger import DeletionLedger

    output = Path(output).resolve()
    artifacts = (ROOT / "frontend" / "artifacts").resolve()
    require(output.is_relative_to(artifacts) and output != artifacts, "new report directory beneath ignored artifacts")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    with isolated_database("life_source") as source:
        source_settings = Settings(_env_file=None, database_url=source.database_url,
            upload_dir=output / "source-private-objects", deletion_ledger_dir=output / "deletion-ledger",
            trusted_origins=["http://testserver"], auth_login_attempt_limit=100, auth_ip_attempt_limit=1000)
        ledger = DeletionLedger(source_settings.deletion_ledger_dir)
        ledger.initialize()
        deleted = synthetic_account(source, source_settings, "deleted")
        retained = synthetic_account(source, source_settings, "retained")
        legacy_id = uuid4()
        legacy_name = f"{legacy_id.hex}.pdf"
        legacy_bytes = b"%PDF-synthetic-legacy-is-not-owned"
        source_settings.upload_dir.joinpath(legacy_name).write_bytes(legacy_bytes)
        with source.session_factory() as db:
            db.add(ResumeDocument(id=legacy_id, user_id=None, file_name="synthetic-legacy.pdf",
                file_path=str(source_settings.upload_dir / legacy_name), file_type="application/pdf",
                raw_text="合成无归属数据"))
            db.commit()
        retained_before = scoped_fingerprint(source.engine, retained["user_id"], retained["email"])
        snapshot = output / "snapshot-private-objects"
        shutil.copytree(source_settings.upload_dir, snapshot)
        export_snapshot = output / "snapshot-private-exports"
        shutil.copytree(source_settings.export_dir, export_snapshot)
        dump_path = output / "synthetic-before-deletion.dump"
        pg_command("pg_dump", source.migration_url, dump_path=dump_path)
        ledger.record(deleted["user_id"], uuid4(), datetime.now(timezone.utc))
        with source.session_factory() as db:
            source_replay = replay_deletion_registry(db, settings=source_settings,
                storage=LocalResumeStorage(source_settings.upload_dir), limit=100)
        require(source_replay["failed"] == 0, "source account deletion completes")
        with source.session_factory() as db:
            require(db.get(User, deleted["user_id"]) is None, "source account is actually deleted after snapshot")
        require(scoped_fingerprint(source.engine, retained["user_id"], retained["email"]) == retained_before,
            "source deletion preserves every unrelated user row")
        restored_storage = output / "restored-private-objects"
        shutil.copytree(snapshot, restored_storage)
        restored_exports = output / "restored-private-exports"
        shutil.copytree(export_snapshot, restored_exports)
        with isolated_database("life_restore", migrate_head=False) as restored:
            require(not inspect(restored.engine).get_table_names(), "fresh restore target starts empty")
            pg_command("pg_restore", restored.migration_url, dump_path=dump_path, restore=True)
            privileges = secure_runtime_role(restored)
            restored_settings = source_settings.model_copy(update={"database_url": restored.database_url,
                "upload_dir": restored_storage, "export_dir": restored_exports})
            with restored.session_factory() as db:
                require(db.get(User, deleted["user_id"]) is not None, "old snapshot contains previously deleted user")
            assert_revoked(restored, restored_settings, deleted)
            failing_storage = FailingDeletionStorage(restored_storage, deleted["object_key"])
            with restored.session_factory() as db:
                failed_replay = replay_deletion_registry(db, settings=restored_settings, storage=failing_storage, limit=100)
            require(failing_storage.failed and failed_replay["failed"] >= 1, "physical cleanup failure is observable")
            assert_revoked(restored, restored_settings, deleted)
            with restored.session_factory() as db:
                recovered_replay = replay_deletion_registry(db, settings=restored_settings,
                    storage=LocalResumeStorage(restored_storage), limit=100)
            require(recovered_replay["failed"] == 0, "retry recovers restored account cleanup")
            with restored.session_factory() as db:
                require(db.get(User, deleted["user_id"]) is None, "restored deleted account removed")
                legacy = db.get(ResumeDocument, legacy_id)
                require(legacy is not None and legacy.user_id is None and legacy.raw_text == "合成无归属数据",
                    "legacy NULL-owner row is preserved")
            require(scoped_fingerprint(restored.engine, retained["user_id"], retained["email"]) == retained_before,
                "restored unrelated user rows match original snapshot")
            for suffix in ("", ".part", ".claim"):
                require(not (restored_storage / (deleted["object_key"] + suffix)).exists(), "deleted user's PDF/part/claim removed")
            retained_pdf = restored_storage / retained["object_key"]
            require(hashlib.sha256(retained_pdf.read_bytes()).hexdigest() == retained["original_sha256"], "other user's PDF bytes remain unchanged")
            require((restored_storage / legacy_name).read_bytes() == legacy_bytes, "legacy file is not adopted or deleted")
            for suffix in (".zip", ".part"):
                require(not (restored_exports / f"{deleted['export_id'].hex}{suffix}").exists(),
                    "deleted user's restored export archive and temporary file removed")
            require(hashlib.sha256((restored_exports / f"{retained['export_id'].hex}.zip").read_bytes()).hexdigest()
                == retained["export_sha256"], "other user's restored export archive is byte-identical")
            with client_for(restored, restored_settings) as client:
                client.cookies.set(SESSION_COOKIE_NAME, retained["token"])
                require(client.get("/api/v1/me").status_code == 200, "other user's old session remains valid")
            with restored.session_factory() as db:
                repeated_replay = replay_deletion_registry(db, settings=restored_settings,
                    storage=LocalResumeStorage(restored_storage), limit=100)
            require(repeated_replay["failed"] == 0, "completed restore deletion replay is idempotent")
            report = {"schema_version": "1.0", "completed_at": datetime.now(timezone.utc).isoformat(),
                "scenario": "isolated PostgreSQL snapshot predates account deletion",
                "source": "fresh synthetic fixture database", "target": "fresh isolated database removed on exit",
                "ledger": "independent current deletion ledger; excluded from database and private object snapshot",
                "dump_bytes": dump_path.stat().st_size, "dump_sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
                "old_snapshot_contains_deleted_account": True, "old_session_denied_before_replay": True,
                "password_login_denied_before_replay": True, "cleanup_failure_observed": True,
                "failure_keeps_account_inaccessible": True, "retry_completed": True,
                "deleted_account_removed": True, "deleted_pdf_part_claim_removed": True,
                "deleted_export_and_temporary_file_removed": True, "unrelated_user_export_checksum_equal": True,
                "unrelated_user_row_fingerprints_equal": True, "unrelated_user_pdf_checksum_equal": True,
                "unrelated_user_session_preserved": True, "legacy_null_owner_row_and_file_preserved": True,
                "replay_idempotent": True, "unrelated_user_fingerprints": retained_before,
                "source_deletion_replay": source_replay, "failed_restore_replay": failed_replay,
                "recovered_restore_replay": recovered_replay, "repeated_replay": repeated_replay,
                "runtime_privileges_after_restore": privileges, "elapsed_seconds": round(time.perf_counter() - started, 3),
                "limits": "Local synthetic stopped-writer rehearsal only. Production backup retention, encryption, operator isolation, ledger durability and provider-side retention remain separately reviewed deployment requirements."}
            (output / "lifecycle-restore-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory beneath frontend/artifacts")
    arguments = parser.parse_args()
    try:
        report = rehearse(arguments.output)
    except Exception as error:
        # SQL/client/OS errors can include credentials or private paths. Keep
        # details inside the debugger, never copy arbitrary exception text.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    print(json.dumps({"status": "passed", "checks": 17, "elapsed_seconds": report["elapsed_seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
