"""Isolated, synthetic PDF evaluation through the existing application services.

This module never selects a network provider or reads a key.  Its caller chooses
a provider after the disclosure; paid authorization remains the caller's duty.
Only the five fixed synthetic fixtures can enter the public report path.
"""

import copy
import hashlib
import json
import secrets
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pymupdf
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from offerpilot_api.agent_runtime.byok import (
    ExtractionPreview, MODEL, OUTPUT_TOKENS, REDACTION_VERSION,
    input_token_upper, redact_resume,
)
from offerpilot_api.agent_runtime.extraction_runs import execute_extraction_run, source_version
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.db.models import ResumeDocument
from offerpilot_api.db.session import build_engine, get_db
from offerpilot_api.identity.services import create_invitation
from offerpilot_api.main import create_app
from offerpilot_api.profile.schemas import VALUE_MODELS

try:
    from .deepseek_v2_cases import CASESET_VERSION, evaluation_cases
except ImportError:
    from deepseek_v2_cases import CASESET_VERSION, evaluation_cases


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PDF_FACTORY_VERSION = "s2-2b-synthetic-pdf-v1"
MOCK_NOTICE = "Mock 测试结果，不代表 DeepSeek 真实模型质量。"
GROUPS = ("education", "skills", "projects", "experiences")
# Fictional labels, deliberately invalid mobile number, and reserved domain.
SYNTHETIC_CONTACTS = ("合成评测甲", "000-0000-0000", "synthetic-evaluation@example.com")
SYNTHETIC_HEADER = ("合成测试简历（全部人物和经历均为虚构）\n姓名：合成评测甲\n"
                    "联系方式：000-0000-0000 synthetic-evaluation@example.com\n")


class PdfEvaluationError(Exception):
    """Safe evaluation failure; no provider response, credentials or headers."""


def synthetic_case(case_id):
    case = next((item for item in evaluation_cases() if item["id"] == case_id), None)
    if case is None:
        raise PdfEvaluationError("Only registered synthetic PDF samples are allowed")
    return copy.deepcopy(case)


def synthetic_pdf_bytes(case_id):
    """Deterministic, selectable Chinese PDF; each fixture line fits one row."""
    case = synthetic_case(case_id)
    lines = (SYNTHETIC_HEADER + case["text"]).splitlines()
    boundary = max(4, len(lines) // 2)
    with pymupdf.open() as document:
        for group in (lines[:boundary], lines[boundary:]):
            page = document.new_page()
            page.insert_text((36, 36), "\n".join(group), fontname="china-s", fontsize=6)
        document.set_metadata({"title": f"OfferPilot SYNTHETIC {case_id}",
                               "author": "OfferPilot synthetic evaluation"})
        return document.tobytes(no_new_id=True)


def write_synthetic_pdf(case_id, path):
    """Write only generated white-listed content, never copy an external PDF."""
    path = Path(path)
    content = synthetic_pdf_bytes(case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(content)
    return path


def _require_response(response, status):
    if response.status_code != status:
        # Deliberately do not include untrusted body, cookies or request details.
        raise PdfEvaluationError(f"Isolated application operation failed ({response.status_code})")
    return response.json() if response.content else None


def _contains_secret(value, secret):
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(_contains_secret(key, secret) or _contains_secret(item, secret)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item, secret) for item in value)
    return False


class MockResponseSpy:
    """One fixed response, no SDK/HTTP transport and no invented token usage."""

    def __init__(self, response):
        self.response = copy.deepcopy(response)
        self.calls = 0
        self.last_usage = None

    def generate_json(self, **kwargs):
        self.calls += 1
        if self.calls != 1:
            raise LLMProviderError("Single request only", code="provider_invalid_response")
        return json.dumps(self.response, ensure_ascii=False)


class _DisclosureSpy:
    """Validate the actual provider boundary and redact secrets before parsing."""

    def __init__(self, delegate, prepared, *, secret=None, before_call=None):
        self.delegate, self.prepared = delegate, prepared
        self.secret, self.before_call = secret, before_call
        self.calls = 0
        self.input_text = None
        self.sent_sha256 = None

    @property
    def last_usage(self):
        return getattr(self.delegate, "last_usage", None)

    @property
    def last_response_model(self):
        return getattr(self.delegate, "last_response_model", None)

    def generate_json(self, *, system_prompt, user_prompt):
        try:
            payload = json.loads(user_prompt.split("\n", 1)[1])
        except (ValueError, IndexError):
            raise LLMProviderError("Invalid disclosure wrapper", code="provider_invalid_response") from None
        if (set(payload) != {"resume_text"} or
                payload["resume_text"] != self.prepared.preview["redacted_text"]):
            raise LLMProviderError("Disclosure changed", code="source_version_changed")
        if self.calls:
            raise LLMProviderError("Single request only", code="provider_invalid_response")
        if self.before_call:
            # Failure here prevents the actual invocation; caller may fsync ledger.
            self.before_call()
        self.input_text = payload["resume_text"]
        self.sent_sha256 = hashlib.sha256(self.input_text.encode()).hexdigest()
        self.calls += 1
        response = self.delegate.generate_json(system_prompt=system_prompt, user_prompt=user_prompt)
        try:
            decoded = json.loads(response) if isinstance(response, str) else response
        except (TypeError, ValueError):
            decoded = response
        if _contains_secret(decoded, self.secret):
            raise LLMProviderError("Unsafe response content", code="provider_invalid_response")
        return response

    def close(self):
        try:
            if hasattr(self.delegate, "close"):
                self.delegate.close()
        finally:
            self.secret = None


class PdfEvaluationSession:
    """Temporary migrated SQLite, production APIs/storage, no background runner.

    prepare(case_id) is free and keyless. create_run requires the displayed hash.
    deepseek mode additionally configures BYOK through the real API and checks
    its consent snapshot. execute only uses an explicitly supplied factory.
    """

    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="offerpilot-pdf-evaluation-")
        self.root = Path(self.temporary.name)
        self.engine = None
        self.clients = []
        self.spies = {}
        self.run_modes = {}
        self._active_secrets = {}
        self._prepared = {}
        self._password = secrets.token_urlsafe(32)

    def __enter__(self):
        try:
            self.settings = Settings(_env_file=None, environment="development",
                database_url=f"sqlite:///{(self.root / 'evaluation.db').as_posix()}",
                upload_dir=self.root / "private-uploads", session_cookie_secure=False,
                mock_extraction_enabled=True, deepseek_extraction_enabled=False,
                extraction_max_calls=1, extraction_max_attempts=1)
            config = Config(str(BACKEND_ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
            # Pin migrations to our settings, never a developer environment DB.
            with patch("offerpilot_api.core.config.get_settings", return_value=self.settings):
                command.upgrade(config, "head")
            self.engine = build_engine(self.settings.database_url)
            with patch("offerpilot_api.main.get_settings", return_value=self.settings):
                self.app = create_app()
            self.app.state.extraction_dispatcher_enabled = False
            self.app.state.extraction_settings = self.settings
            self.app.dependency_overrides[get_settings] = lambda: self.settings

            def isolated_db():
                with self.factory() as db:
                    yield db
            self.app.dependency_overrides[get_db] = isolated_db
            self.owner, self.owner_id = self._register("pdf-owner@example.com")
            self.other, self.other_id = self._register("pdf-other@example.com")
            self.anonymous = self.client()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_args):
        for client in self.clients:
            client.close()
        if hasattr(self, "app"):
            self.app.state.byok_vault.clear()
        self._active_secrets.clear()
        if self.engine is not None:
            self.engine.dispose()
        self.temporary.cleanup()

    def factory(self):
        return Session(self.engine, autoflush=False)

    def client(self):
        client = TestClient(self.app)
        self.clients.append(client)
        return client

    def _login(self, client, email):
        value = _require_response(client.post("/api/v1/auth/login", json={
            "email": email, "password": self._password}), 200)
        client.headers["X-CSRF-Token"] = value["csrf_token"]
        return UUID(value["user"]["id"])

    def _register(self, email):
        client = self.client()
        with self.factory() as db:
            invitation = create_invitation(db, email=email)
        _require_response(client.post("/api/v1/auth/register", json={
            "email": email, "password": self._password, "invite_token": invitation}), 201)
        return client, self._login(client, email)

    def relogin(self):
        _require_response(self.owner.post("/api/v1/auth/logout"), 204)
        self._login(self.owner, "pdf-owner@example.com")
        return self.profile()

    def profile(self, client=None):
        return _require_response((client or self.owner).get("/api/v1/profile"), 200)

    def prepare(self, case_id):
        case = synthetic_case(case_id)
        path = write_synthetic_pdf(case_id, self.root / f"{case_id}.pdf")
        content = path.read_bytes()  # Evaluation genuinely starts from a PDF file.
        if content != synthetic_pdf_bytes(case_id):
            raise PdfEvaluationError("Synthetic PDF bytes changed before upload")
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            parsed = "\n".join(page.get_text("text", sort=True) for page in pdf).strip()
            # Check no line was clipped, substituted or accidentally imported.
            # Joining pages adds a blank separator, which is not user text.
            if [line for line in parsed.splitlines() if line] != (SYNTHETIC_HEADER + case["text"]).splitlines():
                raise PdfEvaluationError("Synthetic PDF text differs from registered source")
            page_count = pdf.page_count
        uploaded = _require_response(self.owner.post("/api/v1/resumes", files={
            "file": (f"合成评测-{case_id}.pdf", content, "application/pdf")}), 201)
        resume_id = UUID(uploaded["id"])
        download = self.owner.get(f"/api/v1/resumes/{resume_id}/download")
        if download.status_code != 200 or download.content != content:
            raise PdfEvaluationError("Private PDF download bytes differ")
        lifecycle = _require_response(self.owner.get(f"/api/v1/resumes/{resume_id}/lifecycle"), 200)
        with self.factory() as db:
            document = db.get(ResumeDocument, resume_id)
            if (document.user_id != self.owner_id or document.raw_text != parsed or
                    document.processing_status != "parsed" or document.lifecycle_status != "active" or
                    document.sha256 != hashlib.sha256(content).hexdigest() or document.byte_size != len(content)):
                raise PdfEvaluationError("Stored PDF parse or integrity check failed")
            raw_text, layout, version = document.raw_text, copy.deepcopy(document.text_layout), source_version(document)
        final_text = redact_resume(raw_text)
        if any(value in final_text for value in SYNTHETIC_CONTACTS):
            raise PdfEvaluationError("Direct synthetic contact information was not redacted")
        preview = {"model": MODEL, "source_version": version, "redacted_text": final_text,
            "sent_text_sha256": hashlib.sha256(final_text.encode()).hexdigest(),
            "redaction_version": REDACTION_VERSION, "max_calls": 1,
            "max_input_tokens": input_token_upper(final_text), "max_output_tokens": OUTPUT_TOKENS,
            "privacy_notice": ExtractionPreview.model_fields["privacy_notice"].default,
            "cost_notice": ExtractionPreview.model_fields["cost_notice"].default,
            "input_scope": "仅最终脱敏 resume_text 与固定版本 Prompt；不发送 PDF 原件",
            "synthetic_only": True}
        metadata = {"file_id": str(resume_id), "file_name": uploaded["file_name"],
            "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content),
            "factory_version": PDF_FACTORY_VERSION, "caseset_version": CASESET_VERSION,
            "page_count": page_count, "text_layout": layout, "processing_status": lifecycle["processing_status"],
            "private_storage_byte_roundtrip": True, "synthetic_whitelist_verified": True}
        prepared = SimpleNamespace(case=case, case_id=case_id, pdf=metadata, pdf_path=path,
            resume_id=resume_id, raw_text=raw_text, text_layout=layout, preview=preview)
        self._prepared[resume_id] = prepared
        return prepared

    def create_run(self, prepared, *, mode="mock", confirmed_sha256, api_key=None):
        if mode not in ("mock", "deepseek") or self._prepared.get(prepared.resume_id) is not prepared:
            raise PdfEvaluationError("Unknown evaluation mode or synthetic document")
        if mode == "mock" and api_key is not None:
            raise PdfEvaluationError("Free Mock mode must not receive a key")
        preview = prepared.preview
        if (preview.get("model") != MODEL or preview.get("max_calls") != 1 or
                preview.get("max_output_tokens") != OUTPUT_TOKENS or
                preview.get("redaction_version") != REDACTION_VERSION or
                preview.get("max_input_tokens") != input_token_upper(preview.get("redacted_text", "")) or
                preview.get("sent_text_sha256") != hashlib.sha256(preview.get("redacted_text", "").encode()).hexdigest()):
            raise PdfEvaluationError("Disclosure metadata changed; prepare a fresh preview")
        with self.factory() as db:
            document = db.get(ResumeDocument, prepared.resume_id)
            if (document is None or source_version(document) != prepared.preview["source_version"] or
                    redact_resume(document.raw_text) != prepared.preview["redacted_text"] or
                    confirmed_sha256 != prepared.preview["sent_text_sha256"]):
                raise PdfEvaluationError("Disclosure changed; prepare and explicitly confirm a new preview")
        payload = {"mode": mode, "source_version": prepared.preview["source_version"],
                   "idempotency_key": uuid4().hex}
        if mode == "deepseek":
            if not api_key:
                raise PdfEvaluationError("Explicit session key is required")
            self.settings.deepseek_extraction_enabled = True
            _require_response(self.owner.put("/api/v1/ai/deepseek-key", json={"api_key": api_key}), 200)
            preview = _require_response(self.owner.get(f"/api/v1/resumes/{prepared.resume_id}/extraction-preview"), 200)
            if any(preview[key] != prepared.preview[key] for key in
                   ("model", "source_version", "redacted_text", "sent_text_sha256", "max_calls", "max_input_tokens", "max_output_tokens")):
                raise PdfEvaluationError("Authenticated disclosure differs; fresh user confirmation required")
            payload["authorization"] = {"authorization_id": preview["authorization_id"], "accepted": True, "max_calls": 1}
        run = _require_response(self.owner.post(f"/api/v1/resumes/{prepared.resume_id}/extraction-runs", json=payload), 202)
        run_id = run["id"]
        self.run_modes[run_id] = mode
        if api_key:
            self._active_secrets[run_id] = api_key
        run["evaluation_create_payload"] = payload  # IDs only; useful for replay tests.
        return run

    def execute(self, prepared, run, *, provider_factory, before_call=None):
        mode = self.run_modes[run["id"]]
        def factory(**kwargs):
            delegate = provider_factory(**kwargs) if mode == "deepseek" else provider_factory()
            spy = _DisclosureSpy(delegate, prepared, secret=self._active_secrets.pop(run["id"], None), before_call=before_call)
            self.spies[run["id"]] = spy
            return spy
        return execute_extraction_run(self.factory, run_id=UUID(run["id"]), settings=self.settings,
            byok_vault=self.app.state.byok_vault,
            **({"deepseek_provider_factory": factory} if mode == "deepseek" else {"provider_factory": factory}))

    def collect(self, prepared, run, *, evaluation_kind="mock"):
        state = _require_response(self.owner.get(f"/api/v1/extraction-runs/{run['id']}"), 200)
        candidates = _require_response(self.owner.get(f"/api/v1/extraction-runs/{run['id']}/candidates"), 200)["candidates"]
        profile = self.profile()
        spy = self.spies.get(run["id"])
        evidence = []
        for candidate in candidates:
            for field in candidate["extraction_metadata"]["fields"]:
                evidence.append({"candidate_id": candidate["id"], "category": candidate["category"],
                    "field": field["path"], "raw_value": field["raw_value"], "source_text": field["source_text"],
                    "locations": field["locations"], "verified_against_pdf_text": bool(field["locations"]) and
                    all(prepared.raw_text[loc["start"]:loc["end"]] == field["source_text"] for loc in field["locations"]),
                    "review_reasons": field["review_reasons"]})
        with self.factory() as db:
            migration = db.scalar(text("SELECT version_num FROM alembic_version"))
        report = {"case_id": prepared.case_id, "evaluation_kind": evaluation_kind,
            "notice": MOCK_NOTICE if evaluation_kind != "real" else "真实合成评测，模型响应不等于抽取准确。",
            "pdf": prepared.pdf, "provider_type": state["mode"], "model": state["model"] or "mock-synthetic",
            "target_model": MODEL, "prompt_version": state["prompt_version"], "schema_version": state["schema_version"],
            "migration_head": migration, "preview": prepared.preview,
            "synthetic_pdf_extracted_text": prepared.raw_text,
            "preview_matches_actual_provider_input": bool(spy and spy.input_text == prepared.preview["redacted_text"]
                                                        and spy.sent_sha256 == prepared.preview["sent_text_sha256"]),
            "actual_sent_sha256": spy.sent_sha256 if spy else None,
            "run": state, "candidates": candidates, "field_evidence": evidence,
            "schema_validation": "passed" if state["status"] == "succeeded" else "failed_or_not_completed",
            "review_required_candidates": [item["id"] for item in candidates if item["evidence_status"] != "supported"],
            "formal_profile_before_review": {group: profile[group] for group in GROUPS},
            "unconfirmed_candidates_not_in_profile": all(not profile[group] for group in GROUPS),
            "provider_invocations": spy.calls if spy else 0,
            "paid_calls": spy.calls if spy and evaluation_kind == "real" else 0,
            "token_usage": {name: state[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
                           if evaluation_kind == "real" and state["total_tokens"] is not None else None,
            "token_usage_kind": "provider_reported" if evaluation_kind == "real" else "unknown_not_measured_mock",
            "actual_cost_cny": None if evaluation_kind == "real" else "0"}
        # Reuse persisted, verified candidates. Never run extraction a second time.
        result = SimpleNamespace(batch=SimpleNamespace(candidates=[SimpleNamespace(category=item["category"],
            value=VALUE_MODELS[item["category"]].model_validate(item["value"])) for item in candidates]) if candidates else None,
            candidate_metadata=[item["extraction_metadata"] for item in candidates])
        return report, result

    def review(self, candidate_id, action):
        profile = self.profile()
        return _require_response(self.owner.post(f"/api/v1/profile/candidates/{candidate_id}/review",
            json={"revision": profile["revision"], "action": action}), 200)

    def review_demonstration(self, run, *, confirm_candidate_id, reject_candidate_id):
        """Only explicit choices, via the same public review API as the UI."""
        before = self.profile()
        candidates = {item["id"]: item for item in before["candidates"] if item["extraction_run_id"] == run["id"]}
        if (confirm_candidate_id not in candidates or reject_candidate_id not in candidates or
                confirm_candidate_id == reject_candidate_id):
            raise PdfEvaluationError("Review choices must belong to this run")
        confirmed = self.review(confirm_candidate_id, "confirm")
        rejected = self.review(reject_candidate_id, "reject")
        restored = self.relogin()
        reviewed_candidates = {item["id"]: item for item in rejected["candidates"]}
        return {"actions": [{"candidate_id": confirm_candidate_id, "action": "confirm"},
                            {"candidate_id": reject_candidate_id, "action": "reject"}],
            "profile_before": {group: before[group] for group in GROUPS},
            "profile_after_confirmation": {group: confirmed[group] for group in GROUPS},
            "profile_after_rejection": {group: rejected[group] for group in GROUPS},
            "candidate_statuses_after_review": {key: reviewed_candidates[key]["status"]
                                               for key in (confirm_candidate_id, reject_candidate_id)},
            "rejected_candidate_has_no_formal_entry": reviewed_candidates[reject_candidate_id]["entry_id"] is None,
            "relogin_preserved_profile_and_candidates": restored == rejected}
