"""Authentication boundaries exercised with real passwords and an isolated DB."""

import hashlib
import io
import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from offerpilot_api.core.config import Settings, get_settings
from offerpilot_api.core.api_errors import ErrorResponse
from offerpilot_api.db.base import Base
from offerpilot_api.db.models import AuthSession, AuthThrottle, RegistrationInvitation, User
from offerpilot_api.db.session import build_engine, get_db
from offerpilot_api.identity.security import SESSION_COOKIE_NAME, new_token, token_hash
from offerpilot_api.identity.services import create_invitation
from offerpilot_api.main import create_app

PASSWORD = "Synthetic-auth-password-2026"


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_url = f"sqlite:///{(self.root / 'authentication.db').as_posix()}"
        self.engine = build_engine(self.database_url)
        Base.metadata.create_all(self.engine)
        self.settings = Settings(
            _env_file=None,
            environment="development",
            database_url=self.database_url,
            upload_dir=self.root / "uploads",
            trusted_origins=["http://testserver", "https://testserver"],
            auth_login_attempt_limit=20,
            auth_ip_attempt_limit=100,
            auth_registration_attempt_limit=40,
        )
        self.clients: list[TestClient] = []
        self.client = self.make_client()

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def make_client(self, *, settings: Settings | None = None, https: bool = False) -> TestClient:
        app = create_app()

        def isolated_db():
            with Session(self.engine) as session:
                yield session

        # Inject a real temporary database and settings only. Identity/session
        # dependencies and Argon2 verification remain the production code.
        app.dependency_overrides[get_db] = isolated_db
        app.dependency_overrides[get_settings] = lambda: settings or self.settings
        client = TestClient(
            app, base_url="https://testserver" if https else "http://testserver", raise_server_exceptions=False
        )
        self.clients.append(client)
        return client

    def invitation(self, email: str) -> str:
        with Session(self.engine) as session:
            return create_invitation(session, email=email)

    def register(self, email: str, *, client: TestClient | None = None, token: str | None = None):
        return (client or self.client).post(
            "/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "invite_token": token or self.invitation(email)},
        )

    def account(self, email: str = "student@example.com", *, client: TestClient | None = None) -> UUID:
        response = self.register(email, client=client)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(set(response.json()), {"id", "email", "created_at"})
        return UUID(response.json()["id"])

    def login(self, email: str = "student@example.com", *, client: TestClient | None = None):
        response = (client or self.client).post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def replay_client(self, token: str) -> TestClient:
        client = self.make_client()
        client.cookies.set(SESSION_COOKIE_NAME, token)
        return client

    def test_two_users_register_login_and_me_with_real_password_hashes(self) -> None:
        other = self.make_client()
        first_id = self.account("first@example.com")
        second_id = self.account("second@example.com", client=other)
        self.assertNotEqual(first_id, second_id)
        self.login("first@example.com")
        self.login("second@example.com", client=other)
        for client, user_id in ((self.client, first_id), (other, second_id)):
            response = client.get("/api/v1/me")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], str(user_id))
            self.assertEqual(set(response.json()), {"id", "email", "created_at"})
            self.assertEqual(response.headers["cache-control"], "no-store")
        with Session(self.engine) as session:
            users = session.scalars(select(User)).all()
            self.assertEqual(len(users), 2)
            for user in users:
                self.assertTrue(user.password_hash.startswith("$argon2id$"))
                self.assertTrue(PasswordHasher().verify(user.password_hash, PASSWORD))
                self.assertNotIn(PASSWORD, repr(user))
            self.assertTrue(all(invite.consumed_at is not None for invite in session.scalars(select(RegistrationInvitation))))

    def test_invitation_invalid_expired_used_and_wrong_email_have_same_safe_error(self) -> None:
        expired = self.invitation("expired@example.com")
        bound = self.invitation("bound@example.com")
        used = self.invitation("used@example.com")
        self.assertEqual(self.register("used@example.com", token=used).status_code, 201)
        with Session(self.engine) as session:
            session.execute(update(RegistrationInvitation).where(
                RegistrationInvitation.token_hash == token_hash(expired)
            ).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            session.commit()
        attempts = [
            ("unknown@example.com", new_token()),
            ("expired@example.com", expired),
            ("used@example.com", used),
            ("different@example.com", bound),
        ]
        responses = []
        for email, token in attempts:
            with self.subTest(reason=email):
                response = self.register(email, token=token)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(token, response.text)
                self.assertNotIn(email, response.text)
                self.assertNotIn(PASSWORD, response.text)
                responses.append(response.json())
        self.assertTrue(all(body == responses[0] for body in responses))
        self.assertEqual(responses[0]["detail"]["code"], "invalid_invitation")
        self.assertEqual(self.register("bound@example.com", token=bound).status_code, 201)

    def test_email_normalization_and_duplicate_registration_preserve_unconsumed_invitation(self) -> None:
        token = self.invitation(" Student@Example.COM ")
        registered = self.register(" STUDENT@example.com ", token=token)
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(registered.json()["email"], "student@example.com")
        login = self.login(" STUDENT@EXAMPLE.COM ")
        self.assertEqual(login.json()["user"]["id"], registered.json()["id"])
        duplicate_token = self.invitation("student@example.com")
        duplicate = self.register("Student@example.com", token=duplicate_token)
        self.assertEqual(duplicate.status_code, 409)
        self.assertNotIn(PASSWORD, duplicate.text)
        self.assertNotIn(duplicate_token, duplicate.text)
        with Session(self.engine) as session:
            self.assertEqual(len(session.scalars(select(User)).all()), 1)
            invitation = session.scalar(select(RegistrationInvitation).where(
                RegistrationInvitation.token_hash == token_hash(duplicate_token)
            ))
            self.assertIsNone(invitation.consumed_at)

    def test_password_bounds_and_validation_errors_redact_secret_values(self) -> None:
        token = self.invitation("student@example.com")
        base = {"email": "student@example.com", "password": PASSWORD, "invite_token": token}
        invalid = [
            {**base, "password": "SHORTSECRET"},
            {**base, "password": "LONGSECRET" * 20},
            {**base, "invite_token": "INVITESECRET"},
            {**base, "email": "PRIVATEEMAIL_INVALID"},
            {**base, "EXTRA_PRIVATE_SECRET": "PRIVATE_FIELD_VALUE"},
        ]
        for payload in invalid:
            with self.subTest(field=set(payload) - set(base)):
                response = self.client.post("/api/v1/auth/register", json=payload)
                self.assertEqual(response.status_code, 422)
                for secret in (PASSWORD, token, "SHORTSECRET", "LONGSECRET", "INVITESECRET", "PRIVATEEMAIL", "PRIVATE_FIELD"):
                    self.assertNotIn(secret, response.text)
                self.assertEqual(response.json()["detail"]["code"], "invalid_request")
        login = self.client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "LOGINSECRET" * 20})
        self.assertEqual(login.status_code, 422)
        self.assertNotIn("LOGINSECRET", login.text)
        malformed = self.client.post("/api/v1/auth/login", content='{"password":"JSONSECRET",', headers={"content-type": "application/json"})
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn("JSONSECRET", malformed.text)
        with Session(self.engine) as session:
            self.assertEqual(session.scalars(select(User)).all(), [])
            self.assertIsNone(session.scalar(select(RegistrationInvitation.consumed_at)))

    def test_wrong_password_and_unknown_user_return_identical_responses(self) -> None:
        self.account()
        known = self.client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": "WRONGPASSWORDSECRET"})
        unknown = self.client.post("/api/v1/auth/login", json={"email": "unknown@example.com", "password": "WRONGPASSWORDSECRET"})
        self.assertEqual(known.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(known.json(), unknown.json())
        self.assertNotIn("WRONGPASSWORDSECRET", known.text)
        self.assertIsNone(self.client.cookies.get(SESSION_COOKIE_NAME))
        with Session(self.engine) as session:
            self.assertEqual(session.scalars(select(AuthSession)).all(), [])

    def test_opaque_session_and_invitation_store_only_hashes_and_cookie_is_private(self) -> None:
        token = self.invitation("student@example.com")
        self.assertEqual(self.register("student@example.com", token=token).status_code, 201)
        response = self.login()
        cookie_token = self.client.cookies.get(SESSION_COOKIE_NAME)
        self.assertEqual(len(cookie_token), 43)
        self.assertNotIn(".", cookie_token)
        self.assertNotIn(cookie_token, response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/", cookie)
        self.assertNotIn("; secure", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        with Session(self.engine) as session:
            stored = session.scalar(select(AuthSession))
            self.assertEqual(stored.token_hash, hashlib.sha256(cookie_token.encode()).hexdigest())
            self.assertNotEqual(stored.token_hash, cookie_token)
            invite = session.scalar(select(RegistrationInvitation))
            self.assertEqual(invite.token_hash, hashlib.sha256(token.encode()).hexdigest())
            self.assertNotIn(cookie_token, repr(stored))
            self.assertNotIn(token, repr(invite))
            self.assertNotIn("token", {column.name for column in AuthSession.__table__.columns})
            self.assertNotIn("token", {column.name for column in RegistrationInvitation.__table__.columns})
        csrf = self.client.get("/api/v1/auth/csrf")
        self.assertEqual(csrf.json()["csrf_token"], response.json()["csrf_token"])
        self.assertEqual(csrf.headers["cache-control"], "no-store")

    def test_production_cookie_defaults_secure_and_explicit_development_secure_cookie(self) -> None:
        for settings in (
            self.settings.model_copy(update={"environment": "production", "session_cookie_secure": None}),
            self.settings.model_copy(update={"session_cookie_secure": True}),
        ):
            with self.subTest(environment=settings.environment):
                client = self.make_client(settings=settings, https=True)
                email = f"cookie-{uuid4().hex}@example.com"
                self.account(email, client=client)
                response = self.login(email, client=client)
                cookie = response.headers["set-cookie"].lower()
                self.assertIn("; secure", cookie)
                self.assertIn("httponly", cookie)
                self.assertIn("samesite=lax", cookie)
                self.assertEqual(client.get("/api/v1/me").status_code, 200)

    def test_logout_revokes_cookie_and_replay_fails(self) -> None:
        self.account()
        response = self.login()
        token = self.client.cookies.get(SESSION_COOKIE_NAME)
        logout = self.client.post("/api/v1/auth/logout", headers={"x-csrf-token": response.json()["csrf_token"]})
        self.assertEqual(logout.status_code, 204)
        self.assertIn("max-age=0", logout.headers["set-cookie"].lower())
        self.assertIsNone(self.client.cookies.get(SESSION_COOKIE_NAME))
        self.assertEqual(self.replay_client(token).get("/api/v1/me").status_code, 401)
        with Session(self.engine) as session:
            self.assertIsNotNone(session.scalar(select(AuthSession.revoked_at).where(AuthSession.token_hash == token_hash(token))))

    def test_expired_revoked_and_inactive_sessions_are_rejected(self) -> None:
        for reason in ("expired", "revoked", "inactive"):
            with self.subTest(reason=reason):
                client = self.make_client()
                email = f"{reason}@example.com"
                user_id = self.account(email, client=client)
                self.login(email, client=client)
                token = client.cookies.get(SESSION_COOKIE_NAME)
                with Session(self.engine) as session:
                    stored = session.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
                    if reason == "expired":
                        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    elif reason == "revoked":
                        stored.revoked_at = datetime.now(timezone.utc)
                    else:
                        session.get(User, user_id).is_active = False
                    session.commit()
                self.assertEqual(client.get("/api/v1/me").status_code, 401)
                self.assertEqual(client.get("/api/v1/auth/csrf").status_code, 401)
                if reason == "inactive":
                    rejected_login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
                    self.assertEqual(rejected_login.status_code, 401)
                    self.assertEqual(rejected_login.json()["detail"]["code"], "invalid_credentials")

    def test_login_rotates_previous_session_and_csrf(self) -> None:
        self.account()
        first = self.login()
        previous = self.client.cookies.get(SESSION_COOKIE_NAME)
        second = self.login()
        current = self.client.cookies.get(SESSION_COOKIE_NAME)
        self.assertNotEqual(current, previous)
        self.assertNotEqual(first.json()["csrf_token"], second.json()["csrf_token"])
        self.assertEqual(self.replay_client(previous).get("/api/v1/me").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)
        stale_csrf = self.client.post("/api/v1/auth/logout", headers={"x-csrf-token": first.json()["csrf_token"]})
        self.assertEqual(stale_csrf.status_code, 403)
        with Session(self.engine) as session:
            old = session.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(previous)))
            self.assertIsNotNone(old.revoked_at)

    def test_csrf_missing_wrong_and_other_sessions_cannot_logout(self) -> None:
        self.account()
        own = self.login()
        other = self.make_client()
        self.account("other@example.com", client=other)
        other_login = self.login("other@example.com", client=other)
        headers = [{}, {"x-csrf-token": "incorrect"}, {"x-csrf-token": other_login.json()["csrf_token"]}]
        for supplied in headers:
            with self.subTest(source=supplied.keys()):
                response = self.client.post("/api/v1/auth/logout", headers=supplied)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"]["code"], "csrf_failed")
                self.assertEqual(self.client.get("/api/v1/me").status_code, 200)
        accepted = self.client.post("/api/v1/auth/logout", headers={"x-csrf-token": own.json()["csrf_token"]})
        self.assertEqual(accepted.status_code, 204)

    def test_malformed_non_ascii_csrf_header_fails_safely(self) -> None:
        self.account()
        self.login()
        response = self.client.post("/api/v1/auth/logout", headers=[(b"x-csrf-token", b"\xff")])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "csrf_failed")
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_untrusted_origins_and_cross_site_fetch_cannot_register_login_or_logout(self) -> None:
        self.account()
        login = self.login()
        invitation = self.invitation("blocked@example.com")
        for source in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
            with self.subTest(source=source):
                registration = self.client.post("/api/v1/auth/register", headers=source, json={
                    "email": "blocked@example.com", "password": PASSWORD, "invite_token": invitation,
                })
                self.assertEqual(registration.status_code, 403)
                blocked_login = self.client.post("/api/v1/auth/login", headers=source, json={"email": "student@example.com", "password": PASSWORD})
                self.assertEqual(blocked_login.status_code, 403)
                logout = self.client.post("/api/v1/auth/logout", headers={**source, "x-csrf-token": login.json()["csrf_token"]})
                self.assertEqual(logout.status_code, 403)
                self.assertEqual(self.client.get("/api/v1/me").status_code, 200)
        with Session(self.engine) as session:
            self.assertIsNone(session.scalar(select(RegistrationInvitation.consumed_at).where(
                RegistrationInvitation.token_hash == token_hash(invitation)
            )))
        allowed = self.client.post("/api/v1/auth/logout", headers={
            "origin": "http://testserver", "sec-fetch-site": "same-origin", "x-csrf-token": login.json()["csrf_token"],
        })
        self.assertEqual(allowed.status_code, 204)

    def test_login_limit_is_shared_between_apps_and_expired_window_recovers(self) -> None:
        self.settings.auth_login_attempt_limit = 2
        self.settings.auth_attempt_window_seconds = 60
        self.account()
        second_app = self.make_client()
        payload = {"email": "student@example.com", "password": "incorrect-password"}
        self.assertEqual(self.client.post("/api/v1/auth/login", json=payload).status_code, 401)
        self.assertEqual(second_app.post("/api/v1/auth/login", json=payload).status_code, 401)
        limited = self.client.post("/api/v1/auth/login", json={"email": "student@example.com", "password": PASSWORD})
        self.assertEqual(limited.status_code, 429)
        self.assertTrue(int(limited.headers["retry-after"]) > 0)
        self.assertLessEqual(int(limited.headers["retry-after"]), self.settings.auth_attempt_window_seconds)
        with Session(self.engine) as session:
            session.execute(update(AuthThrottle).values(window_started_at=datetime.now(timezone.utc) - timedelta(seconds=61)))
            session.commit()
        recovered = self.login(client=second_app)
        self.assertEqual(second_app.get("/api/v1/me").json()["id"], recovered.json()["user"]["id"])
        with Session(self.engine) as session:
            self.assertIsNone(session.get(AuthThrottle, token_hash("login-account:student@example.com")))
            self.assertTrue(all(len(row.key_hash) == 64 for row in session.scalars(select(AuthThrottle))))

    def test_ip_and_registration_limits_persist_across_apps(self) -> None:
        self.settings.auth_ip_attempt_limit = 2
        self.settings.auth_registration_attempt_limit = 2
        second = self.make_client()
        for index, client in enumerate((self.client, second)):
            response = client.post("/api/v1/auth/login", json={"email": f"unknown-{index}@example.com", "password": PASSWORD})
            self.assertEqual(response.status_code, 401)
        response = second.post("/api/v1/auth/login", json={"email": "unknown-third@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 429)
        for client in (self.client, second):
            self.assertEqual(self.register("unknown@example.com", client=client, token=new_token()).status_code, 400)
        self.assertEqual(self.register("unknown@example.com", client=second, token=new_token()).status_code, 429)

    def test_client_supplied_identity_and_forged_cookies_do_not_authenticate(self) -> None:
        self.account()
        logged_in = self.login()
        user_id = logged_in.json()["user"]["id"]
        attacker = self.make_client()
        for token in ("forged", new_token(), user_id):
            with self.subTest(token_type=len(token)):
                attacker.cookies.set(SESSION_COOKIE_NAME, token)
                response = attacker.get("/api/v1/me", params={"user_id": user_id}, headers={"x-user-id": user_id, "authorization": f"Bearer {user_id}"})
                self.assertEqual(response.status_code, 401)
        own = self.client.get("/api/v1/me", params={"user_id": str(uuid4())}, headers={"x-user-id": str(uuid4())})
        self.assertEqual(own.json()["id"], user_id)
        forged_body = attacker.post("/api/v1/auth/login", json={"email": "student@example.com", "password": PASSWORD, "user_id": user_id})
        self.assertEqual(forged_body.status_code, 422)

    def test_error_contract_documents_safe_actual_responses(self) -> None:
        schema = self.client.app.openapi()
        for path, method, status_code in (
            ("/api/v1/auth/login", "post", "422"),
            ("/api/v1/me", "get", "401"),
            ("/api/v1/resumes/{resume_id}", "get", "404"),
        ):
            response_schema = schema["paths"][path][method]["responses"][status_code]["content"]["application/json"]["schema"]
            self.assertEqual(response_schema, {"$ref": "#/components/schemas/ErrorResponse"})
        unauthorized = self.client.get("/api/v1/me")
        invalid = self.client.post("/api/v1/auth/login", json={"email": "invalid", "password": PASSWORD})
        for response in (unauthorized, invalid):
            ErrorResponse.model_validate(response.json())
            self.assertNotIn(PASSWORD, response.text)

    def test_invalid_unicode_password_is_rejected_and_valid_unicode_works(self) -> None:
        token = self.invitation("unicode@example.com")
        for path, payload in (
            ("/api/v1/auth/register", {"email": "unicode@example.com", "invite_token": token}),
            ("/api/v1/auth/login", {"email": "unicode@example.com"}),
        ):
            response = self.client.post(
                path, content=json.dumps({**payload, "password": chr(0xD800) * 12}),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["fields"], ["password"])
            self.assertNotIn(token, response.text)
        password = "合成测试密码-2026-UTF8"
        registered = self.client.post("/api/v1/auth/register", json={
            "email": "unicode@example.com", "password": password, "invite_token": token,
        })
        self.assertEqual(registered.status_code, 201)
        logged_in = self.client.post("/api/v1/auth/login", json={"email": "unicode@example.com", "password": password})
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_concurrent_registration_consumes_one_invitation_once(self) -> None:
        token = self.invitation("concurrent@example.com")
        clients = [self.make_client() for _ in range(2)]
        start = Barrier(len(clients))

        def register_concurrently(client):
            start.wait(timeout=10)
            return self.register("concurrent@example.com", client=client, token=token)

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            responses = list(pool.map(register_concurrently, clients))
        statuses = [response.status_code for response in responses]
        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertTrue(all(status in (201, 400, 409) for status in statuses), statuses)
        with Session(self.engine) as db:
            self.assertEqual(len(db.scalars(select(User)).all()), 1)
            self.assertIsNotNone(db.scalar(select(RegistrationInvitation)).consumed_at)
        self.login("concurrent@example.com")

    def test_concurrent_login_cannot_exceed_persistent_ip_limit(self) -> None:
        self.settings.auth_ip_attempt_limit = 3
        clients = [self.make_client() for _ in range(6)]
        start = Barrier(len(clients))

        def attempt(index):
            start.wait(timeout=10)
            return clients[index].post("/api/v1/auth/login", json={
                "email": f"parallel-{index}@example.com", "password": PASSWORD,
            }).status_code

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            statuses = list(pool.map(attempt, range(len(clients))))
        self.assertEqual(statuses.count(401), 3, statuses)
        self.assertEqual(statuses.count(429), 3, statuses)
        with Session(self.engine) as db:
            self.assertEqual(db.get(AuthThrottle, token_hash("login-ip:testclient")).attempt_count, 3)

    def test_database_failures_and_validation_logs_hide_parameters_and_secrets(self) -> None:
        marker = "SYNTHETIC_SQL_PRIVATE_PARAMETER"
        with self.engine.connect() as connection:
            with self.assertRaises(SQLAlchemyError) as caught:
                connection.execute(text("SELECT :private FROM missing_auth_table"), {"private": marker})
        self.assertNotIn(marker, str(caught.exception))
        self.assertTrue(self.engine.hide_parameters)
        self.account()
        self.login()
        session_token = self.client.cookies.get(SESSION_COOKIE_NAME)
        if self.engine.dialect.name == "postgresql":
            # The isolated PG runner supplies this fixture's migration URL;
            # the application role deliberately cannot perform DDL or grants.
            from offerpilot_api.db.session import build_engine as build_fault_engine
            fault_engine = build_fault_engine(self.postgres_migration_url)
            try:
                with fault_engine.begin() as connection:
                    role = connection.dialect.identifier_preparer.quote_identifier(self.engine.url.username)
                    connection.execute(text(f"REVOKE SELECT ON users FROM {role}"))
            finally:
                fault_engine.dispose()
        else:
            with self.engine.begin() as connection:
                connection.execute(text("DROP TABLE users"))
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger()
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            unavailable = self.client.get("/api/v1/me")
            invalid = self.client.post("/api/v1/auth/login", json={"email": "PRIVATE_EMAIL_SENTINEL", "password": marker})
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["detail"]["code"], "storage_unavailable")
        self.assertEqual(invalid.status_code, 422)
        response_bodies = unavailable.text + invalid.text
        combined = response_bodies + stream.getvalue()
        for secret in (marker, PASSWORD, session_token, "PRIVATE_EMAIL_SENTINEL"):
            self.assertNotIn(secret, combined)
        for implementation_detail in ("SELECT", "sqlite3", "Traceback"):
            self.assertNotIn(implementation_detail, response_bodies)


if __name__ == "__main__":
    unittest.main()
