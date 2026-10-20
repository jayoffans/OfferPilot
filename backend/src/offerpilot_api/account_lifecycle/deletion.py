"""Persistent erasure with an independent restore-time deny registry."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from offerpilot_api.account_lifecycle.guards import reauthenticate
from offerpilot_api.account_lifecycle.ledger import DeletionLedger, LedgerError
from offerpilot_api.db import models as m
from offerpilot_api.identity.security import new_token, token_hash
from offerpilot_api.identity.services import IdentityError
from offerpilot_api.resume_ingestion.lifecycle import delete_owned_resume, _finish_cleanup, ResumeLifecycleError
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, StorageError


def now():
    return datetime.now(timezone.utc)


def begin_deletion(db, *, user, password, idempotency_key, session_token, settings, vault):
    reauthenticate(db, user, password, settings)
    existing = db.scalar(select(m.AccountDeletion).where(m.AccountDeletion.user_id == user.id))
    if existing:
        return existing, None
    user_id = user.id
    receipt = m.AccountDeletion(id=uuid4(), user_id=user_id, idempotency_digest=token_hash(idempotency_key),
        status="pending", attempt_count=0, status_expires_at=now() + timedelta(hours=1),
        status_session_hash=token_hash(session_token), created_at=now(), updated_at=now())
    status_token = new_token()
    receipt.status_token_hash = token_hash(status_token)
    # Write the independent intent FIRST. If DB commit is interrupted, auth is
    # denied by this registry and recovery reconstructs the missing DB receipt.
    try:
        recorded = DeletionLedger(settings.deletion_ledger_dir).record(user_id, receipt.id, receipt.created_at)
    except LedgerError:
        db.rollback()
        raise IdentityError("deletion_registry_unavailable", "注销尚未开始，删除清单暂不可用", 503) from None
    receipt.id = UUID(recorded["deletion_id"])
    db.info["account_cleanup"] = True
    session_ids = list(db.scalars(select(m.AuthSession.id).where(m.AuthSession.user_id == user_id)))
    # Revoke memory before committing identity denial; resolution also checks
    # live DB identity. Already submitted provider requests may still be billed.
    for session_id in session_ids:
        vault.revoke(session_id)
    db.execute(update(m.User).where(m.User.id == user_id).values(is_active=False))
    db.execute(update(m.AuthSession).where(m.AuthSession.user_id == user_id).values(revoked_at=now()))
    db.execute(update(m.ResumeExtractionRun).where(m.ResumeExtractionRun.user_id == user_id,
        m.ResumeExtractionRun.status.in_(("queued", "running"))).values(
            status="cancelled", execution_token=None, lease_deadline=None, error_code="account_deleted",
            revision=m.ResumeExtractionRun.revision + 1, finished_at=now(), updated_at=now()))
    db.execute(update(m.AccountExport).where(m.AccountExport.user_id == user_id,
        m.AccountExport.status.in_(("queued", "running", "ready"))).values(
            status="cancelled", execution_token=None, lease_deadline=None, updated_at=now()))
    db.add(receipt)
    db.commit()
    return receipt, status_token


def _receipt_lock(db, deletion_id):
    # Lock the receipt for the current lease/status transaction.
    row = db.execute(update(m.AccountDeletion).where(m.AccountDeletion.id == deletion_id)
                     .values(updated_at=m.AccountDeletion.updated_at))
    return db.get(m.AccountDeletion, deletion_id, populate_existing=True) if row.rowcount else None


def process_deletion(db, *, deletion_id, settings, storage):
    db.info["account_cleanup"] = True
    receipt = _receipt_lock(db, deletion_id)
    if receipt is None:
        db.rollback()
        return False
    if receipt.status == "completed":
        db.rollback()
        return True
    if receipt.status == "processing" and receipt.lease_deadline and (
        receipt.lease_deadline.replace(tzinfo=timezone.utc) if receipt.lease_deadline.tzinfo is None else receipt.lease_deadline) > now():
        db.rollback()
        return False
    if receipt.attempt_count >= settings.deletion_max_attempts:
        receipt.status = "manual_review"
        receipt.error_code = "cleanup_attempts_exhausted"
        db.commit()
        return False
    user_id = receipt.user_id
    receipt.status, receipt.error_code = "processing", None
    receipt.lease_deadline = now() + timedelta(minutes=5)
    receipt.attempt_count += 1
    receipt.updated_at = now()
    db.execute(update(m.User).where(m.User.id == user_id).values(is_active=False))
    db.execute(update(m.AuthSession).where(m.AuthSession.user_id == user_id).values(revoked_at=now()))
    db.execute(update(m.ResumeExtractionRun).where(m.ResumeExtractionRun.user_id == user_id,
        m.ResumeExtractionRun.status.in_(("queued", "running"))).values(status="cancelled", execution_token=None,
        lease_deadline=None, error_code="account_deleted", finished_at=now(), updated_at=now()))
    db.commit()
    try:
        from offerpilot_api.account_lifecycle.exports import delete_user_exports
        db.execute(update(m.User).where(m.User.id == user_id).values(is_active=False))
        delete_user_exports(db, user_id=user_id, settings=settings)
        # Existing journal/claim checks prove object ownership. A physical error
        # preserves the account receipt and file references for bounded retry.
        started = time.monotonic()
        ids = list(db.scalars(select(m.ResumeDocument.id).where(m.ResumeDocument.user_id == user_id).limit(100)))
        db.rollback()
        for resume_id in ids:
            if time.monotonic() - started > 20:
                break
            delete_owned_resume(db, resume_id=resume_id, user_id=user_id, storage=storage)
        operations = list(db.scalars(select(m.ResumeFileOperation.resume_id).where(
            m.ResumeFileOperation.user_id == user_id, m.ResumeFileOperation.status != "deleted").limit(100)))
        db.rollback()
        for resume_id in operations:
            if time.monotonic() - started > 20:
                break
            if not _finish_cleanup(db, resume_id=resume_id, user_id=user_id, storage=storage):
                raise StorageError("file_delete_failed", "Cleanup incomplete")
        receipt = _receipt_lock(db, deletion_id)
        if db.scalar(select(m.ResumeDocument.id).where(m.ResumeDocument.user_id == user_id).limit(1)) or db.scalar(
            select(m.ResumeFileOperation.resume_id).where(m.ResumeFileOperation.user_id == user_id,
                m.ResumeFileOperation.status != "deleted").limit(1)):
            receipt.status, receipt.lease_deadline = "pending", None
            receipt.attempt_count -= 1
            db.commit()
            return False
        # Explicit child-first deletion, including digest tombstones and ledgers.
        # Parent-chain legacy Profile/Fact rows were erased by file lifecycle.
        for model in (m.ApplicationEvent, m.ApplicationReceipt, m.Application,
                      m.JobMatchReceipt, m.JobMatch, m.JobCreateReceipt, m.SavedJob,
                      m.CareerProfile, m.ResumeExtractionRun, m.ResumeFileOperation,
                      m.AccountExport, m.AuthSession):
            db.execute(delete(model).where(model.user_id == user_id))
        user = db.get(m.User, user_id, populate_existing=True)
        if user:
            # Invitations have no owner FK: only exact normalized account email.
            db.execute(delete(m.RegistrationInvitation).where(m.RegistrationInvitation.email_normalized == user.email_normalized))
            db.execute(delete(m.AuthThrottle).where(m.AuthThrottle.key_hash.in_([
                token_hash(f"login-account:{user.email_normalized}"), token_hash(f"reauth:{user_id}")])))
            db.execute(delete(m.User).where(m.User.id == user_id))
        receipt.status, receipt.error_code = "completed", None
        receipt.lease_deadline = None
        receipt.completed_at, receipt.updated_at = now(), now()
        db.commit()
        return True
    except Exception:
        # Never persist exception text, paths, personal content or SQL parameters.
        db.rollback()
        receipt = _receipt_lock(db, deletion_id)
        if receipt and receipt.status != "completed":
            receipt.status = "manual_review" if receipt.attempt_count >= settings.deletion_max_attempts else "failed"
            receipt.error_code = "account_cleanup_failed"
            receipt.lease_deadline = None
            receipt.updated_at = now()
            db.commit()
        return False


def replay_deletion_registry(db, *, settings, storage, limit=100):
    if not 1 <= limit <= 1000:
        raise ValueError("Recovery limit must be 1..1000")
    db.info["account_cleanup"] = True
    result = dict(attempted=0, completed=0, failed=0, skipped=0)
    for entry in DeletionLedger(settings.deletion_ledger_dir).entries():
        user_id, deletion_id = UUID(entry["user_id"]), UUID(entry["deletion_id"])
        # Serialize reconstruction when two recovery workers see an old backup
        # without the receipt. The inactive row remains a valid lock target.
        db.execute(update(m.User).where(m.User.id == user_id).values(is_active=False))
        receipt = db.scalar(select(m.AccountDeletion).where(m.AccountDeletion.user_id == user_id))
        # Completed receipts in an old partially restored DB are not proof when
        # a user row is present. Restore-time replay must erase that row again.
        user_exists = db.get(m.User, user_id) is not None
        if receipt and receipt.status == "completed" and not user_exists:
            result["skipped"] += 1
            db.rollback()
            continue
        if result["attempted"] >= limit:
            db.rollback()
            break
        if receipt is None:
            receipt = m.AccountDeletion(id=deletion_id, user_id=user_id, idempotency_digest=token_hash(str(deletion_id)),
                status="pending", attempt_count=0, created_at=datetime.fromisoformat(entry["created_at"]), updated_at=now())
            db.add(receipt)
        elif receipt.status == "completed" and user_exists:
            receipt.status, receipt.attempt_count, receipt.completed_at = "pending", 0, None
        db.commit()
        result["attempted"] += 1
        if process_deletion(db, deletion_id=receipt.id, settings=settings, storage=storage):
            result["completed"] += 1
        else:
            current = db.get(m.AccountDeletion, receipt.id, populate_existing=True)
            if current and current.status in ("pending", "processing", "completed"):
                result["skipped"] += 1
            else:
                result["failed"] += 1
    # Access hashes are short-lived even if the service is otherwise quiet.
    db.execute(update(m.AccountDeletion).where(m.AccountDeletion.status_expires_at <= now(), or_(
        m.AccountDeletion.status_token_hash.is_not(None), m.AccountDeletion.status_session_hash.is_not(None))).values(
        status_token_hash=None, status_session_hash=None))
    db.commit()
    return result


async def deletion_dispatcher(app, session_factory):
    while True:
        if app.state.lifecycle_dispatcher_enabled:
            settings = app.state.lifecycle_settings
            try:
                with session_factory() as db:
                    await asyncio.to_thread(replay_deletion_registry, db, settings=settings,
                        storage=LocalResumeStorage(settings.upload_dir), limit=3)
            except (SQLAlchemyError, StorageError, LedgerError):
                # API authentication also fails closed; faults leave only safe
                # status codes. The operator CLI returns failure counts.
                pass
        await asyncio.sleep(2)
