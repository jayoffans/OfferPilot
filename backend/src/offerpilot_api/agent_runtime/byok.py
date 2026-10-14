"""Process-local encrypted, bounded session credentials and consent snapshots.

No persistence or external credential broker. Independent workers cannot resolve
these references; restart safely destroys credentials. Not a multi-worker vault.
"""

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from offerpilot_api.core.config import Settings
from offerpilot_api.db.models import AuthSession, User, ResumeExtractionRun

MODEL = "deepseek-flash"
CONSENT_VERSION = "deepseek-consent-v1"
REDACTION_VERSION = "resume-redaction-v1"
OUTPUT_TOKENS = 4096


def input_token_upper(text):
    # JSON escaping can expand backslashes/control characters. Count the
    # serialized input bytes plus a conservative fixed-prompt/framing margin.
    return len(json.dumps({"resume_text": text}, ensure_ascii=False).encode()) + 16000


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=12, max_length=256)

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,256}", value.get_secret_value()):
            raise ValueError("Invalid credential format")
        return value


class KeyStatus(BaseModel):
    configured: bool
    expires_at: datetime | None
    ttl_seconds: int
    provider: str = "deepseek"
    enabled: bool
    unavailable_reason: str | None = None


class ExtractionPreview(BaseModel):
    authorization_id: UUID
    source_version: str
    preview_version: str = CONSENT_VERSION
    provider: str = "deepseek"
    model: str = MODEL
    redacted_text: str
    sent_text_sha256: str
    max_calls: int = 1
    max_input_tokens: int
    max_output_tokens: int = OUTPUT_TOKENS
    redaction_version: str = REDACTION_VERSION
    privacy_notice: str = "仅发送预览中的教育、技能、项目、实习/工作文本，不发送 PDF 文件。默认遮蔽联系信息和个人资料区。仍可能含个人信息，请检查预览。DeepSeek 可能保留数据、使用去标识内容训练；API 专门退出和确切保留期限尚未核实。"
    cost_notice: str = "本次最多调用 1 次，不自动修复或重试。输入 token 为保守上界估计，输出最多 4096 token。可能产生 API 费用，由您的 Key 账户承担；实际 token、缓存和时段价格以 DeepSeek 账单为准。超时、取消或断网仍可能收费。"
    expires_at: datetime


@dataclass(repr=False)
class _Credential:
    user_id: UUID
    session_id: UUID
    reference: UUID
    ciphertext: bytes = field(repr=False)
    expires_at: datetime
    password_fingerprint: str


@dataclass(repr=False)
class _Consent:
    user_id: UUID
    session_id: UUID
    reference: UUID
    resume_id: UUID
    preview: ExtractionPreview


class SessionKeyVault:
    def __init__(self):
        self._cipher = Fernet(Fernet.generate_key())
        self._keys = {}
        self._consents = {}
        self._lock = threading.RLock()

    def clear(self):
        with self._lock:
            self._keys.clear()
            self._consents.clear()
            self._cipher = Fernet(Fernet.generate_key())

    def _purge(self):
        for sid, credential in list(self._keys.items()):
            if credential.expires_at <= now():
                self.revoke(sid)
        for cid, consent in list(self._consents.items()):
            if consent.preview.expires_at <= now():
                self._consents.pop(cid, None)

    def revoke(self, session_id):
        with self._lock:
            self._keys.pop(session_id, None)
            for cid, consent in list(self._consents.items()):
                if consent.session_id == session_id:
                    self._consents.pop(cid, None)

    def put(self, *, user: User, session: AuthSession, api_key: str, ttl_seconds: int):
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError("Invalid credential lifetime")
        with self._lock:
            self._purge()
            self.revoke(session.id)
            if len(self._keys) >= 100:
                raise ValueError("Credential capacity reached")
            expiry = min(now() + timedelta(seconds=ttl_seconds), utc(session.expires_at))
            self._keys[session.id] = _Credential(user.id, session.id, uuid4(), self._cipher.encrypt(api_key.encode()), expiry,
                                                hashlib.sha256(user.password_hash.encode()).hexdigest())

    def credential(self, db: Session, *, user_id: UUID, session_id: UUID, reference: UUID | None = None):
        # Every resolution checks live server identity; password changes and
        # account deactivation invalidate credentials even without a UI endpoint.
        with self._lock:
            self._purge()
            item = self._keys.get(session_id)
            session = db.scalar(select(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user_id,
                                AuthSession.revoked_at.is_(None), AuthSession.expires_at > now()))
            user = db.get(User, user_id, populate_existing=True)
            if (not item or not session or not user or not user.is_active or item.user_id != user_id
                or hashlib.sha256(user.password_hash.encode()).hexdigest() != item.password_fingerprint):
                if item and item.user_id == user_id:
                    self.revoke(session_id)
                return None
            if reference is not None and item.reference != reference:
                # A stale worker must reject its old reference without wiping
                # a replacement key or newly granted consent in this session.
                return None
            return item

    def resolve(self, db, *, user_id, session_id, reference):
        with self._lock:
            item = self.credential(db, user_id=user_id, session_id=session_id, reference=reference)
            return self._cipher.decrypt(item.ciphertext).decode() if item else None

    def status(self, db, *, user_id, session_id, settings):
        item = self.credential(db, user_id=user_id, session_id=session_id)
        return KeyStatus(configured=item is not None, expires_at=item.expires_at if item else None,
                         ttl_seconds=settings.byok_ttl_seconds, enabled=settings.deepseek_extraction_available,
                         unavailable_reason=None if settings.deepseek_extraction_available else "真实解析未开启；生产供应商数据处理与 BYOK 条件待审核")

    def preview(self, *, user_id, session_id, reference, resume_id, source_version, text):
        with self._lock:
            self._purge()
            if len(self._consents) >= 100:
                # Replace only this session's unused snapshots, never other users'.
                for cid, value in list(self._consents.items()):
                    if value.session_id == session_id:
                        self._consents.pop(cid, None)
                if len(self._consents) >= 100:
                    raise ValueError("Consent capacity reached")
            snapshot = ExtractionPreview(authorization_id=uuid4(), source_version=source_version, redacted_text=text,
                sent_text_sha256=hashlib.sha256(text.encode()).hexdigest(), max_input_tokens=input_token_upper(text),
                expires_at=now() + timedelta(minutes=10))
            self._consents[snapshot.authorization_id] = _Consent(user_id, session_id, reference, resume_id, snapshot)
            return snapshot

    def consent(self, *, authorization_id, user_id, session_id, resume_id, reference, source_version):
        with self._lock:
            self._purge()
            item = self._consents.get(authorization_id)
            if (not item or item.user_id != user_id or item.session_id != session_id or item.resume_id != resume_id
                or item.reference != reference or item.preview.source_version != source_version):
                return None
            return item.preview


_SECTION = re.compile(r"^(?:[一二三四五六七八九十\d、.．\s]*)(?:教育(?:经历|背景)?|学术背景|专业技能|技术技能|技能(?:清单|特长)?|项目(?:经历|经验)?|实习(?:及工作)?(?:经历|经验)?|工作(?:经历|经验)?|实习及工作经历|education|skills|technical skills|projects|experience|work experience|internships)(?:\s*[:：].*|\s*)$", re.I)
_OTHER_SECTION = re.compile(r"^(?:个人(?:信息|资料|简介)|基本信息|联系方式|自我评价|兴趣爱好|求职意向|荣誉奖项|personal information|contact)\s*[:：]?", re.I)
_PRIVATE_LINE = re.compile(r"姓名|真实姓名|住址|地址|身份证|护照|出生|性别|民族|婚姻|微信|QQ|linkedin|github\.com|个人主页|name\s*[:：]|address\s*[:：]", re.I)
_CONTACT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)(?:\+?86[-\s]?)?1[3-9](?:[-\s]?\d){9}(?!\d)|(?<!\d)\d{17}[0-9Xx](?!\d)|https?://\S+|(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)")


def redact_resume(raw_text: str) -> str:
    """Conservative allowlisted sections; preserve offsets using same-size masks.

    This lexical minimizer is not a universal PII recognizer. The full preview
    and explicit consent remain mandatory; unlabeled document structures fail.
    """
    included = False
    lines = []
    for line in raw_text.splitlines(keepends=True):
        stripped = line.strip()
        if _SECTION.fullmatch(stripped):
            included = True
        elif _OTHER_SECTION.match(stripped):
            included = False
        if not included or _PRIVATE_LINE.search(line):
            lines.append("".join(char if char in "\r\n" else "*" for char in line))
        else:
            lines.append(_CONTACT.sub(lambda match: "*" * len(match[0]), line))
    return "".join(lines)


def invalidate_session_runs(db: Session, *, session_id: UUID):
    timestamp = now()
    db.execute(update(ResumeExtractionRun).where(ResumeExtractionRun.session_id == session_id,
        ResumeExtractionRun.mode == "deepseek", ResumeExtractionRun.status.in_(("queued", "running"))).values(
        status="cancelled", error_code="session_invalid", execution_token=None, lease_deadline=None,
        revision=ResumeExtractionRun.revision + 1, finished_at=timestamp, updated_at=timestamp))


def stop_unavailable_runs(db: Session, *, user_id: UUID, vault: SessionKeyVault):
    """Read-side recovery also terminates orphaned queues after a restart.

    No provider invocation, no replay, and CAS cannot alter a committed success.
    This works even when the paid dispatcher is disabled in the new process.
    """
    runs = db.scalars(select(ResumeExtractionRun).where(ResumeExtractionRun.user_id == user_id,
        ResumeExtractionRun.mode == "deepseek", ResumeExtractionRun.status.in_(("queued", "running"))).limit(100)).all()
    changed = False
    for run in runs:
        if vault.credential(db, user_id=user_id, session_id=run.session_id, reference=run.key_reference) is not None:
            continue
        timestamp = now()
        db.execute(update(ResumeExtractionRun).where(ResumeExtractionRun.id == run.id,
            ResumeExtractionRun.status.in_(("queued", "running"))).values(status="failed", error_code="key_unavailable",
            execution_token=None, lease_deadline=None, revision=ResumeExtractionRun.revision + 1, finished_at=timestamp, updated_at=timestamp))
        changed = True
    if changed:
        db.commit()
