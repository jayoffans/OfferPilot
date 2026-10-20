"""User row lock is the ordering boundary for business writes and erasure."""
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from offerpilot_api.account_lifecycle.ledger import DeletionLedger, LedgerError
from offerpilot_api.core.config import get_settings
from offerpilot_api.db.models import User
from offerpilot_api.identity.security import verify_password
from offerpilot_api.identity.services import IdentityError, _take_attempt


def check_registry(user_id, settings):
    try:
        if DeletionLedger(settings.deletion_ledger_dir).contains(user_id):
            raise IdentityError("authentication_required", "账户已进入注销流程", 401)
    except LedgerError:
        raise IdentityError("deletion_registry_unavailable", "数据安全检查暂不可用，请联系维护者", 503) from None


def require_active_account(db, user_id, settings=None):
    settings = settings or db.info.get("account_settings") or get_settings()
    # Use the connection directly: ORM event hooks must not recurse/autoflush.
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        changed = connection.execute(update(User).where(User.id == user_id, User.is_active.is_(True))
                                     .values(is_active=User.is_active))
        active = changed.rowcount == 1
    else:
        active = connection.scalar(select(User.is_active).where(User.id == user_id).with_for_update())
    if not active:
        raise IdentityError("authentication_required", "账户已失效，请重新登录", 401)
    check_registry(user_id, settings)
    return db.get(User, user_id)


def reauthenticate(db, user, password, settings):
    _take_attempt(db, key=f"reauth:{user.id}", limit=5, window_seconds=settings.auth_attempt_window_seconds)
    if not verify_password(user.password_hash, password):
        raise IdentityError("reauthentication_failed", "密码不正确，请重新验证身份", 403)
    require_active_account(db, user.id, settings)


def _fence(db):
    owner = db.info.get("account_user_id")
    if owner is not None and not db.info.get("account_cleanup"):
        require_active_account(db, owner)


@event.listens_for(Session, "do_orm_execute")
def _before_statement(state):
    if state.is_insert or state.is_update or state.is_delete:
        _fence(state.session)


@event.listens_for(Session, "before_flush")
def _before_flush(db, _context, _instances):
    if db.new or db.dirty or db.deleted:
        _fence(db)


@event.listens_for(Session, "before_commit")
def _before_commit(db):
    _fence(db)
