"""Identity use cases with persistent sessions and atomic invitation consumption."""

from datetime import datetime, timedelta, timezone

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import AuthSession, AuthThrottle, RegistrationInvitation, User
from offerpilot_api.identity.security import (
    hash_password,
    needs_rehash,
    new_token,
    token_hash,
    valid_token,
    verify_password,
)

_EMAIL = TypeAdapter(EmailStr)


class IdentityError(Exception):
    def __init__(self, code: str, message: str, status_code: int, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return str(_EMAIL.validate_python(email.strip())).casefold()


def create_invitation(db: Session, *, email: str, ttl_seconds: int = 172800) -> str:
    """Trusted operator entry point; no public invitation-issuing endpoint."""
    if not 60 <= ttl_seconds <= 7 * 24 * 3600:
        raise ValueError("Invitation lifetime must be between one minute and seven days")
    normalized = normalize_email(email)
    if len(normalized) > 254:
        raise ValueError("Email exceeds the allowed length")
    token = new_token()
    db.add(
        RegistrationInvitation(
            email_normalized=normalized,
            token_hash=token_hash(token),
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
    )
    db.commit()
    return token


def _take_attempt(db: Session, *, key: str, limit: int, window_seconds: int) -> None:
    """Reserve an attempt atomically, including across API processes."""
    now = utc_now()
    cutoff = now - timedelta(seconds=window_seconds)
    digest = token_hash(key)
    # Only hashes are retained. Remove obsolete buckets without a new scheduler.
    db.execute(
        delete(AuthThrottle).where(
            AuthThrottle.window_started_at < now - timedelta(days=1)
        )
    )
    for _ in range(2):
        reset = db.execute(
            update(AuthThrottle)
            .where(
                AuthThrottle.key_hash == digest,
                AuthThrottle.window_started_at <= cutoff,
            )
            .values(window_started_at=now, attempt_count=1)
        )
        if reset.rowcount:
            db.commit()
            return
        reserved = db.execute(
            update(AuthThrottle)
            .where(
                AuthThrottle.key_hash == digest,
                AuthThrottle.window_started_at > cutoff,
                AuthThrottle.attempt_count < limit,
            )
            .values(attempt_count=AuthThrottle.attempt_count + 1)
        )
        if reserved.rowcount:
            db.commit()
            return
        if db.get(AuthThrottle, digest) is not None:
            db.commit()
            raise IdentityError("rate_limited", "尝试过于频繁，请稍后重试", 429, retry_after_seconds=window_seconds)
        db.add(AuthThrottle(key_hash=digest, window_started_at=now, attempt_count=1))
        try:
            db.commit()
            return
        except IntegrityError:
            db.rollback()
    raise IdentityError("rate_limited", "尝试过于频繁，请稍后重试", 429, retry_after_seconds=window_seconds)


def register_user(
    db: Session,
    *,
    email: str,
    password: str,
    invite_token: str,
    client_host: str,
    settings: Settings,
) -> User:
    from offerpilot_api.account_lifecycle.ledger import DeletionLedger, LedgerError
    registry = DeletionLedger(settings.deletion_ledger_dir)
    try:
        # Provision only in trusted new-environment setup, before serving requests.
        # An empty users table can also be a restored, previously erased database.
        registry._check()
    except (LedgerError, OSError):
        raise IdentityError("deletion_registry_unavailable", "数据安全检查暂不可用，请联系维护者", 503) from None
    _take_attempt(
        db,
        key=f"register-ip:{client_host}",
        limit=settings.auth_registration_attempt_limit,
        window_seconds=settings.auth_attempt_window_seconds,
    )
    normalized = normalize_email(email)
    now = utc_now()
    if not valid_token(invite_token):
        raise IdentityError("invalid_invitation", "邀请无效或已失效", 400)
    invitation_filter = (
        RegistrationInvitation.token_hash == token_hash(invite_token),
        RegistrationInvitation.email_normalized == normalized,
        RegistrationInvitation.consumed_at.is_(None),
        RegistrationInvitation.expires_at > now,
    )
    invitation = db.scalar(select(RegistrationInvitation.id).where(*invitation_filter))
    if invitation is None:
        raise IdentityError("invalid_invitation", "邀请无效或已失效", 400)
    encoded = hash_password(password)
    consumed = db.execute(
        update(RegistrationInvitation)
        .where(*invitation_filter)
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise IdentityError("invalid_invitation", "邀请无效或已失效", 400)
    user = User(email_normalized=normalized, password_hash=encoded)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # The invitation remains available if the user transaction fails.
        db.rollback()
        raise IdentityError("registration_failed", "无法创建账户，请检查邀请或联系项目负责人", 409) from None
    db.refresh(user)
    return user


def login_user(
    db: Session,
    *,
    email: str,
    password: str,
    client_host: str,
    settings: Settings,
    previous_token: str | None = None,
) -> tuple[User, str]:
    normalized = normalize_email(email)
    _take_attempt(
        db,
        key=f"login-ip:{client_host}",
        limit=settings.auth_ip_attempt_limit,
        window_seconds=settings.auth_attempt_window_seconds,
    )
    account_key = f"login-account:{normalized}"
    _take_attempt(
        db,
        key=account_key,
        limit=settings.auth_login_attempt_limit,
        window_seconds=settings.auth_attempt_window_seconds,
    )
    user = db.scalar(select(User).where(User.email_normalized == normalized))
    matched = verify_password(user.password_hash if user else None, password)
    if not matched or user is None or not user.is_active:
        raise IdentityError("invalid_credentials", "邮箱或密码不正确", 401)
    from offerpilot_api.account_lifecycle.guards import require_active_account
    require_active_account(db, user.id, settings)
    db.info["account_user_id"] = user.id
    db.info["account_settings"] = settings
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    if previous_token and valid_token(previous_token):
        from offerpilot_api.agent_runtime.byok import invalidate_session_runs
        previous = db.scalar(select(AuthSession.id).where(AuthSession.token_hash == token_hash(previous_token)))
        db.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == token_hash(previous_token))
            .values(revoked_at=utc_now())
        )
        if previous is not None:
            invalidate_session_runs(db, session_id=previous)
    token = new_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=utc_now() + timedelta(seconds=settings.session_ttl_seconds),
        )
    )
    db.execute(delete(AuthThrottle).where(AuthThrottle.key_hash == token_hash(account_key)))
    db.commit()
    return user, token


def authenticated_session(db: Session, token: str | None) -> AuthSession:
    if not token or not valid_token(token):
        raise IdentityError("authentication_required", "请先登录", 401)
    session = db.scalar(
        select(AuthSession)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == token_hash(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utc_now(),
            User.is_active.is_(True),
        )
    )
    if session is None:
        raise IdentityError("authentication_required", "会话已失效，请重新登录", 401)
    from offerpilot_api.account_lifecycle.guards import check_registry
    check_registry(session.user_id, db.info.get("account_settings") or get_settings())
    return session


def revoke_session(db: Session, session: AuthSession) -> None:
    from offerpilot_api.agent_runtime.byok import invalidate_session_runs
    session.revoked_at = utc_now()
    db.flush()
    invalidate_session_runs(db, session_id=session.id)
    db.commit()
