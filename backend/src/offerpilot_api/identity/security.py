"""Password hashing and opaque credentials; never log inputs or hashes."""

import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SESSION_COOKIE_NAME = "offerpilot_session"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_PASSWORD_HASHER = PasswordHasher()
_DUMMY_HASH = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def valid_token(token: str) -> bool:
    return bool(_TOKEN_PATTERN.fullmatch(token))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(session_token: str) -> str:
    # A purpose-specific MAC can be reissued after refresh without storing a
    # second secret. The opaque session token stays in its HttpOnly cookie.
    return hmac.new(
        session_token.encode("ascii"), b"offerpilot-csrf-v1", hashlib.sha256
    ).hexdigest()


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        matched = _PASSWORD_HASHER.verify(stored_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return stored_hash is not None and matched


def needs_rehash(stored_hash: str) -> bool:
    return _PASSWORD_HASHER.check_needs_rehash(stored_hash)
