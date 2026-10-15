"""S4-1 real authenticated API/SQLite application history and safety checks.

Only generated identities, synthetic jobs/resumes and a temporary database are
used. No provider or recruitment website is contacted.
"""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import test_job_catalog as harness
import test_job_matches as match_harness
import test_trusted_profile as profile_harness
from offerpilot_api.application_tracker import services
from offerpilot_api.application_tracker.schemas import NextActionInput
from offerpilot_api.db.models import Application, ApplicationEvent, ApplicationReceipt, JobMatch, ResumeDocument
from offerpilot_api.profile.services import ingest_profile_candidates


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class ApplicationTests(unittest.TestCase):
    setUp = harness.JobCatalogTests.setUp
    client = harness.JobCatalogTests.client
    account = harness.JobCatalogTests.account
    login = harness.JobCatalogTests.login
    payload = harness.JobCatalogTests.payload
    create = harness.JobCatalogTests.create
    save = harness.JobCatalogTests.save
    profile = match_harness.JobMatchTests.profile
    job = match_harness.JobMatchTests.job
    calculate = match_harness.JobMatchTests.calculate

    def application_payload(self, job, **changes):
        return {"job_id": job["id"], "job_revision": job["revision"],
                "idempotency_key": uuid4().hex, **changes}

    def application(self, job=None, **changes):
        response = self.owner.post("/api/v1/applications", json=self.application_payload(job or self.create(), **changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def edit_payload(self, application, **changes):
        return {**{key: application[key] for key in ("revision", "status", "submitted_at", "notes", "next_action")},
                "idempotency_key": uuid4().hex, **changes}

    def edit(self, application, expected=200, **changes):
        response = self.owner.put(f"/api/v1/applications/{application['id']}", json=self.edit_payload(application, **changes))
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def events(self, application, **query):
        response = self.owner.get(f"/api/v1/applications/{application['id']}/events", params=query)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action(self, **changes):
        return {"title": "准备合成技术面试", "notes": "复习接口设计", "due_date": "2099-10-16",
                "due_time": None, "timezone": "Asia/Shanghai", "status": "pending", **changes}

    def test_create_from_saved_job_has_real_defaults_and_does_not_change_profile_or_job(self):
        profile = self.owner.get("/api/v1/profile").json()
        job = self.create()
        before = datetime.now(timezone.utc)
        application = self.application(job)
        after = datetime.now(timezone.utc)
        self.assertEqual(application["job_id"], job["id"])
        self.assertEqual((application["job_title"], application["company"], application["location"]),
                         (job["title"], job["company"], job["location"]))
        self.assertEqual((application["status"], application["attempt_number"], application["revision"]), ("preparing", 1, 1))
        self.assertIsNone(application["submitted_at"])
        self.assertIsNone(application["next_action"])
        self.assertIsNone(application["match"])
        self.assertFalse(application["is_overdue"])
        self.assertLessEqual(before.replace(microsecond=0), instant(application["created_at"]))
        self.assertLessEqual(instant(application["created_at"]), after)
        self.assertEqual(self.owner.get("/api/v1/profile").json(), profile)
        self.assertEqual(self.owner.get(f"/api/v1/jobs/{job['id']}").json(), job)
        event = self.events(application)
        self.assertEqual(event["total"], 1)
        self.assertEqual(event["items"][0]["type"], "application_created")
        self.assertEqual(event["items"][0]["revision"], 1)

    def test_favorite_and_application_are_independent(self):
        job = self.create()
        favorite = self.owner.patch(f"/api/v1/jobs/{job['id']}/favorite", json={"revision": job["revision"], "is_favorite": True})
        self.assertEqual(favorite.status_code, 200, favorite.text)
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 0)
        application = self.application(favorite.json())
        self.assertEqual(application["status"], "preparing")
        self.assertTrue(self.owner.get(f"/api/v1/jobs/{job['id']}").json()["is_favorite"])

    def test_create_retry_recovers_unknown_network_result_without_duplicate_history(self):
        payload = self.application_payload(self.create(), idempotency_key="create-network-retry")
        first = self.owner.post("/api/v1/applications", json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        # The response may have been lost after commit. An explicit retry uses
        # the original key rather than producing another application/event.
        second = self.owner.post("/api/v1/applications", json=payload)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 1)
        self.assertEqual(self.events(first.json())["total"], 1)

    def test_duplicate_create_with_new_key_and_active_reapply_are_explicit_conflicts(self):
        job = self.create()
        application = self.application(job)
        for reapply in (False, True):
            response = self.owner.post("/api/v1/applications", json=self.application_payload(job, reapply=reapply))
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "application_already_exists")
        self.assertEqual(self.events(application)["total"], 1)

    def test_terminal_reapplication_requires_explicit_choice_and_preserves_prior_attempt(self):
        job = self.create()
        first = self.edit(self.application(job), status="rejected", notes="本次合成流程已结束")
        self.assertEqual(self.owner.post("/api/v1/applications", json=self.application_payload(job)).status_code, 409)
        second = self.application(job, reapply=True)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((second["attempt_number"], second["status"]), (2, "preparing"))
        self.assertEqual(self.owner.get(f"/api/v1/applications/{first['id']}").json(), first)
        self.assertEqual(self.owner.get("/api/v1/applications", params={"job_id": job["id"]}).json()["total"], 2)
        # Reopening the earlier attempt would create two active records.
        conflict = self.edit(first, expected=409, status="interview")
        self.assertEqual(conflict["detail"]["code"], "application_already_exists")

    def test_same_create_key_cannot_be_rebound_to_another_job_or_request(self):
        job = self.create()
        payload = self.application_payload(job, idempotency_key="bound-create")
        first = self.owner.post("/api/v1/applications", json=payload).json()
        other_job = self.create(self.payload(title="另一个合成实习岗位"))
        for changes in ({"reapply": True}, {"job_id": other_job["id"]}, {"job_revision": job["revision"] + 1}):
            response = self.owner.post("/api/v1/applications", json={**payload, **changes})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.events(first)["total"], 1)

    def test_job_revision_must_still_match_when_application_is_created(self):
        job = self.create()
        self.save(job, {"company": "合成公司修正版"})
        response = self.owner.post("/api/v1/applications", json=self.application_payload(job))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 0)

    def test_submitted_timestamp_uses_explicit_user_fact_and_normalizes_utc(self):
        application = self.application()
        application = self.edit(application, status="submitted")
        self.assertIsNone(application["submitted_at"], "Changing status must not invent an actual submission time")
        application = self.edit(application, submitted_at="2026-10-14T00:15:00+08:00")
        self.assertEqual(instant(application["submitted_at"]), datetime(2026, 10, 13, 16, 15, tzinfo=timezone.utc))
        with Session(self.engine) as db:
            stored = db.get(Application, UUID(application["id"]))
            self.assertEqual(stored.submitted_at.replace(tzinfo=timezone.utc), datetime(2026, 10, 13, 16, 15, tzinfo=timezone.utc))
        self.assertEqual(application["revision"], 3)
        self.assertEqual(sum(row["type"] == "submitted_at_changed" for row in self.events(application)["items"]), 1)
        application = self.edit(application, submitted_at=None)
        self.assertIsNone(application["submitted_at"])
        self.assertEqual(sum(row["type"] == "submitted_at_changed" for row in self.events(application)["items"]), 2)

    def test_user_can_skip_stages_correct_status_and_offer_never_means_accepted(self):
        application = self.application()
        for status in ("offer", "preparing", "assessment", "submitted", "interview", "withdrawn", "closed", "rejected", "preparing"):
            application = self.edit(application, status=status)
            self.assertEqual(application["status"], status)
        self.assertIsNone(application["submitted_at"])
        self.assertEqual(sum(row["type"] == "status_changed" for row in self.events(application)["items"]), 9)
        invalid = self.owner.put(f"/api/v1/applications/{application['id']}", json=self.edit_payload(application, status="accepted"))
        self.assertEqual(invalid.status_code, 422)

    def test_notes_add_edit_and_clear_keep_honest_before_after_timeline(self):
        application = self.application()
        for notes in ("合成备注：等待本人补充材料", "更正：材料已准备，尚未实际提交", ""):
            application = self.edit(application, notes=notes)
            self.assertEqual(application["notes"], notes)
        events = [row for row in reversed(self.events(application)["items"]) if row["type"] == "notes_changed"]
        self.assertEqual(len(events), 3)
        self.assertIn("等待本人补充材料", str(events[0]["changes"]["after"]))
        self.assertIn("等待本人补充材料", str(events[1]["changes"]["before"]))
        self.assertIn("尚未实际提交", str(events[1]["changes"]["after"]))
        self.assertIn("尚未实际提交", str(events[2]["changes"]["before"]))
        application = self.edit(application)
        self.assertEqual(self.events(application)["total"], 4, "A save with no changed values must not fabricate a business event")

    def test_event_business_time_is_separate_from_recorded_time_and_corrections_append(self):
        application = self.application()
        recorded_before = datetime.now(timezone.utc)
        application = self.edit(application, status="interview", occurred_at="2026-09-25T09:30:00+08:00")
        first = next(row for row in self.events(application)["items"] if row["type"] == "status_changed")
        self.assertEqual(instant(first["occurred_at"]), datetime(2026, 9, 25, 1, 30, tzinfo=timezone.utc))
        self.assertGreaterEqual(instant(first["recorded_at"]), recorded_before.replace(microsecond=0))
        application = self.edit(application, status="assessment", occurred_at="2026-09-24T13:00:00+08:00")
        events = self.events(application)["items"]
        self.assertEqual(next(row for row in events if row["id"] == first["id"]), first)
        self.assertEqual(len([row for row in events if row["type"] == "status_changed"]), 2)
        self.assertEqual([row["revision"] for row in events], sorted((row["revision"] for row in events), reverse=True))

    def test_date_only_action_stays_date_only_and_can_complete_cancel_and_clear(self):
        application = self.application()
        application = self.edit(application, next_action=self.action(due_date="2000-01-01"))
        self.assertEqual(application["next_action"]["due_date"], "2000-01-01")
        self.assertIsNone(application["next_action"]["due_time"])
        self.assertEqual(application["next_action"]["timezone"], "Asia/Shanghai")
        self.assertTrue(application["is_overdue"])
        for status in ("completed", "pending", "cancelled"):
            application = self.edit(application, next_action={**application["next_action"], "status": status})
            self.assertEqual(application["is_overdue"], status == "pending")
        application = self.edit(application, next_action=None)
        self.assertIsNone(application["next_action"])
        self.assertFalse(application["is_overdue"])
        self.assertEqual(sum(row["type"] == "next_action_changed" for row in self.events(application)["items"]), 5)

    def test_timed_and_unscheduled_actions_retain_exact_user_input(self):
        application = self.application()
        for action in (self.action(due_date="2099-10-16", due_time="00:05"), self.action(due_date=None)):
            application = self.edit(application, next_action=action)
            self.assertEqual(application["next_action"], action)
            self.assertFalse(application["is_overdue"])

    def test_update_replay_after_lost_response_and_later_edit_never_replays_events(self):
        application = self.application()
        payload = self.edit_payload(application, status="submitted", notes="同一操作的合成备注", next_action=self.action(),
                                    submitted_at="2026-10-13T10:00:00+08:00", idempotency_key="lost-response")
        url = f"/api/v1/applications/{application['id']}"
        first = self.owner.put(url, json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        original_events = self.events(application)
        replay = self.owner.put(url, json=payload)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["revision"], first.json()["revision"])
        self.assertEqual(self.events(application), original_events)
        current = self.edit(first.json(), notes="后续真实修改")
        replay = self.owner.put(url, json=payload)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(self.owner.get(url).json(), current)
        self.assertEqual(self.events(application)["total"], original_events["total"] + 1)

    def test_update_key_is_owner_scoped_but_cannot_change_payload_or_application(self):
        application = self.application()
        payload = self.edit_payload(application, notes="一次记录", idempotency_key="edit-once")
        url = f"/api/v1/applications/{application['id']}"
        response = self.owner.put(url, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.owner.put(url, json={**payload, "notes": "秘密不可出现在错误里"}).status_code, 409)
        second = self.application(self.create(self.payload(title="第二个合成岗位")))
        other_url = f"/api/v1/applications/{second['id']}"
        self.assertEqual(self.owner.put(other_url, json={**payload, "revision": second["revision"]}).status_code, 409)
        self.assertEqual(self.events(application)["total"], 2)
        self.assertEqual(self.events(second)["total"], 1)
        foreign_job = self.create(self.payload(title="另一用户的合成岗位"), client=self.other)
        same_key_different_owner = self.other.post("/api/v1/applications", json=self.application_payload(foreign_job, idempotency_key="edit-once"))
        self.assertEqual(same_key_different_owner.status_code, 201, same_key_different_owner.text)

    def test_stale_revision_conflict_keeps_saved_state_and_adds_no_history(self):
        original = self.application()
        changed = self.edit(original, notes="较早保存的真实输入")
        conflict = self.edit(original, expected=409, notes="不能覆盖先前版本")
        self.assertIn("code", conflict["detail"])
        self.assertEqual(self.owner.get(f"/api/v1/applications/{original['id']}").json(), changed)
        self.assertEqual(self.events(original)["total"], 2)

    def test_list_search_filters_pagination_sort_and_overdue_are_real_owner_data(self):
        first = self.application(self.create(self.payload(title="后端合成岗位 A", company="甲合成公司")))
        first = self.edit(first, status="submitted", submitted_at="2026-10-10T09:00:00+08:00", next_action=self.action(due_date="2000-01-01"))
        second = self.application(self.create(self.payload(title="前端合成岗位 B", company="乙合成公司")))
        second = self.edit(second, status="interview", submitted_at="2026-10-11T09:00:00+08:00", next_action=self.action())
        third = self.application(self.create(self.payload(title="算法合成岗位 C", company=None)))
        self.assertEqual(self.other.get("/api/v1/applications").json()["total"], 0)
        filtered = self.owner.get("/api/v1/applications", params={"q": "甲合成", "status": "submitted"}).json()
        self.assertEqual([row["id"] for row in filtered["items"]], [first["id"]])
        self.assertTrue(filtered["items"][0]["is_overdue"])
        for sort in ("updated_desc", "created_desc", "submitted_desc", "next_action_asc"):
            result = self.owner.get("/api/v1/applications", params={"sort": sort, "page_size": 1})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual((result.json()["total"], len(result.json()["items"])), (3, 1))
        submitted = self.owner.get("/api/v1/applications", params={"sort": "submitted_desc"}).json()["items"]
        self.assertEqual([row["id"] for row in submitted], [second["id"], first["id"], third["id"]])
        upcoming = self.owner.get("/api/v1/applications", params={"sort": "next_action_asc"}).json()["items"]
        self.assertEqual([row["id"] for row in upcoming], [first["id"], second["id"], third["id"]])
        empty = self.owner.get("/api/v1/applications", params={"q": "从未保存的合成公司"}).json()
        self.assertEqual((empty["items"], empty["total"]), ([], 0))

    def test_events_pagination_does_not_hide_total_or_allow_mutating_old_event(self):
        application = self.edit(self.application(), status="interview", notes="多种同次修改", next_action=self.action())
        first = self.events(application, page=1, page_size=2)
        second = self.events(application, page=2, page_size=2)
        self.assertEqual((first["total"], second["total"]), (4, 4))
        self.assertEqual(len({row["id"] for row in first["items"] + second["items"]}), 4)
        event_url = f"/api/v1/applications/{application['id']}/events/{first['items'][0]['id']}"
        for method in ("PUT", "PATCH", "DELETE"):
            self.assertIn(self.owner.request(method, event_url, json={"notes": "伪造过去"}).status_code, (404, 405))
        self.assertEqual(self.events(application)["total"], 4)

    def test_logout_login_and_new_http_session_restore_all_persisted_data(self):
        application = self.edit(self.application(), status="interview", notes="重登后仍在", next_action=self.action(due_time="14:30"))
        history = self.events(application)
        self.assertEqual(self.owner.post("/api/v1/auth/logout").status_code, 204)
        self.assertEqual(self.owner.get(f"/api/v1/applications/{application['id']}").status_code, 401)
        self.login(self.owner, "job-owner@example.com")
        self.assertEqual(self.owner.get(f"/api/v1/applications/{application['id']}").json(), application)
        self.assertEqual(self.events(application), history)

    def test_anonymous_and_other_owner_cannot_read_edit_delete_or_access_history(self):
        job = self.create()
        application = self.application(job)
        url = f"/api/v1/applications/{application['id']}"
        delete_payload = {"revision": application["revision"], "idempotency_key": "foreign-delete", "confirmed": True}
        for client, code in ((self.anonymous, 401), (self.other, 404)):
            for path in (url, url + "/events"):
                self.assertEqual(client.get(path).status_code, code)
            self.assertEqual(client.put(url, json=self.edit_payload(application, notes="禁止越权")).status_code, code)
            self.assertEqual(client.request("DELETE", url, json=delete_payload).status_code, code)
            self.assertEqual(client.post("/api/v1/applications", json=self.application_payload(job)).status_code, code)
        self.assertEqual(self.anonymous.get("/api/v1/applications").status_code, 401)
        self.assertEqual(self.owner.get(f"/api/v1/applications/{uuid4()}").status_code, 404)
        self.assertEqual(self.owner.get(url).json(), application)

    def test_csrf_and_untrusted_origin_block_all_application_writes(self):
        job = self.create()
        application = self.application(job)
        no_csrf = self.client()
        no_csrf.cookies.update(self.owner.cookies)
        for client, headers in ((no_csrf, {}), (self.owner, {"Origin": "https://evil.example"}),
                                (self.owner, {"Sec-Fetch-Site": "cross-site"})):
            requests = [("POST", "/api/v1/applications", self.application_payload(job)),
                        ("PUT", f"/api/v1/applications/{application['id']}", self.edit_payload(application, status="interview")),
                        ("DELETE", f"/api/v1/applications/{application['id']}", {"revision": 1, "idempotency_key": uuid4().hex, "confirmed": True})]
            for method, url, payload in requests:
                self.assertEqual(client.request(method, url, json=payload, headers=headers).status_code, 403)
        self.assertEqual(self.events(application)["total"], 1)

    def test_forged_owner_and_internal_fields_fail_schema_without_echoing_personal_text(self):
        job = self.create()
        application = self.application(job)
        url = f"/api/v1/applications/{application['id']}"
        for changes in ({"user_id": str(self.other_id)}, {"attempt_number": 3}, {"created_at": "2026-01-01T00:00:00Z"}):
            for method, path, payload in (("POST", "/api/v1/applications", self.application_payload(job)),
                                          ("PUT", url, self.edit_payload(application, notes="SECRET-PRIVATE-INPUT"))):
                response = self.owner.request(method, path, json={**payload, **changes})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("SECRET-PRIVATE-INPUT", response.text)
        self.assertEqual(self.events(application)["total"], 1)

    def test_validation_rejects_invalid_dates_times_bounds_and_unknown_status_without_mutation(self):
        application = self.application()
        invalid = [{"status": "accepted"}, {"revision": True}, {"revision": 0}, {"revision": 2**100},
                   {"idempotency_key": "../unsafe"}, {"submitted_at": "2026-10-14T09:00:00"},
                   {"occurred_at": "2026-10-14T09:00:00"}, {"notes": "SECRET" * 20000},
                   {"next_action": self.action(title="   ")}, {"next_action": self.action(due_time="09:00", due_date=None)},
                   {"next_action": self.action(due_date="2026-02-30")}, {"next_action": self.action(due_time="25:00")},
                   {"next_action": self.action(due_time="09:00:01")}, {"next_action": self.action(timezone="UTC")},
                   {"next_action": self.action(status="sent")}, {"next_action": self.action(user_id=str(self.other_id))}]
        for changes in invalid:
            with self.subTest(changes=str(changes)[:100]):
                response = self.owner.put(f"/api/v1/applications/{application['id']}", json=self.edit_payload(application, **changes))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("SECRET", response.text)
        for query in ({"page": 0}, {"page_size": 101}, {"status": "accepted"}, {"sort": "user_id"}, {"q": "x" * 201}, {"job_id": "bad"}):
            self.assertEqual(self.owner.get("/api/v1/applications", params=query).status_code, 422)
        self.assertEqual(self.owner.get("/api/v1/applications/not-a-uuid").status_code, 422)
        self.assertEqual(self.events(application)["total"], 1)

    def test_utc_boundary_offsets_return_safe_422_or_persist_representable_instants(self):
        application = self.application()
        url = f"/api/v1/applications/{application['id']}"
        original_history = self.events(application)
        invalid = ("0001-01-01T00:00:00+00:01", "0001-01-01T00:00:00+08:00",
                   "0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59.999999-00:01",
                   "9999-12-31T23:59:59-08:00", "9999-12-31T23:59:59-23:59")
        for field in ("submitted_at", "occurred_at"):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    response = self.owner.put(url, json=self.edit_payload(application,
                        **{field: value}, notes="PRIVATE-TIMESTAMP-INPUT"))
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"]["code"], "invalid_request")
                    self.assertNotIn("PRIVATE-TIMESTAMP-INPUT", response.text)
                    self.assertNotIn("OverflowError", response.text)
                    self.assertNotIn(value, response.text)
        self.assertEqual(self.owner.get(url).json(), application)
        self.assertEqual(self.events(application), original_history)
        valid = ("0001-01-01T00:00:00Z", "0001-01-02T00:00:00+23:59",
                 "9999-12-30T23:59:59-23:59", "9999-12-31T23:59:59.999999Z")
        for index, value in enumerate(valid):
            with self.subTest(valid=value):
                application = self.edit(application, submitted_at=value, occurred_at=value,
                    notes=f"合成时间边界 {index}")
                self.assertEqual(instant(application["submitted_at"]), instant(value))
                newest = [event for event in self.events(application)["items"]
                          if event["revision"] == application["revision"]]
                self.assertTrue(newest)
                self.assertTrue(all(instant(event["occurred_at"]) == instant(value) for event in newest))

    def test_html_in_notes_is_inert_json_and_errors_never_echo_it(self):
        malicious = '<script>alert("synthetic")</script><img src=x onerror=alert(1)>'
        application = self.edit(self.application(), notes=malicious, next_action=self.action(notes=malicious))
        response = self.owner.get(f"/api/v1/applications/{application['id']}")
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(response.json()["notes"], malicious)
        self.assertEqual(response.json()["next_action"]["notes"], malicious)
        conflict = self.owner.put(f"/api/v1/applications/{application['id']}", json=self.edit_payload(application, revision=1, notes=malicious))
        self.assertEqual(conflict.status_code, 409)
        self.assertNotIn("<script>", conflict.text)
        self.assertNotIn("Traceback", conflict.text)

    def test_private_application_responses_are_not_cacheable(self):
        application = self.application()
        for path in ("/api/v1/applications", f"/api/v1/applications/{application['id']}", f"/api/v1/applications/{application['id']}/events"):
            self.assertEqual(self.owner.get(path).headers.get("cache-control"), "no-store")

    def test_application_blocks_job_deletion_until_explicit_application_deletion(self):
        job = self.create()
        application = self.edit(self.application(job), status="closed", notes="历史仍需保留")
        response = self.owner.request("DELETE", f"/api/v1/jobs/{job['id']}", json={"revision": job["revision"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "job_has_applications")
        self.assertEqual(self.owner.get(f"/api/v1/applications/{application['id']}").json(), application)
        deletion = self.owner.request("DELETE", f"/api/v1/applications/{application['id']}",
            json={"revision": application["revision"], "idempotency_key": "delete-first", "confirmed": True})
        self.assertEqual(deletion.status_code, 204, deletion.text)
        self.assertEqual(self.owner.request("DELETE", f"/api/v1/jobs/{job['id']}", json={"revision": job["revision"]}).status_code, 204)

    def test_delete_confirmation_revision_and_tombstones_prevent_accidental_loss_or_resurrection(self):
        job = self.create()
        create_payload = self.application_payload(job, idempotency_key="creation-before-delete")
        application = self.owner.post("/api/v1/applications", json=create_payload).json()
        url = f"/api/v1/applications/{application['id']}"
        deletion = {"revision": 1, "idempotency_key": "delete-safe", "confirmed": True}
        self.assertEqual(self.owner.request("DELETE", url, json={**deletion, "confirmed": False}).status_code, 422)
        changed = self.edit(application, notes="需要保留的合成历史")
        self.assertEqual(self.owner.request("DELETE", url, json=deletion).status_code, 409)
        deletion["revision"] = changed["revision"]
        self.assertEqual(self.owner.request("DELETE", url, json=deletion).status_code, 204)
        self.assertEqual(self.owner.request("DELETE", url, json=deletion).status_code, 204)
        self.assertEqual(self.owner.get(url).status_code, 404)
        self.assertEqual(self.owner.get(url + "/events").status_code, 404)
        replay = self.owner.post("/api/v1/applications", json=create_payload)
        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual(replay.json()["detail"]["code"], "application_deleted")
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 0)

    def test_match_is_optional_hard_conflict_does_not_prevent_manual_application(self):
        profile = self.profile(skills=[{"name": "JavaScript"}], education=[{"school": "合成大学", "degree": "本科", "major": "软件工程", "end_date": "2028-06", "end_date_kind": "expected"}])
        job = self.job(jd_text="必须2027届毕业生", requirements=[{"type": "graduation", "content": "2027届毕业生", "priority": "must", "review_status": "confirmed", "evidence_text": "2027届毕业生"}])
        result = self.calculate(job, profile)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["evaluation"]["conclusion"], "hard_conflict")
        application = self.application(job, match_id=result.json()["id"])
        self.assertEqual(application["status"], "preparing")
        self.assertEqual(application["match"]["id"], result.json()["id"])
        self.assertFalse(application["match"]["stale"])

    def test_profile_and_job_changes_stale_match_without_losing_application_history(self):
        profile, job = self.profile(), self.job()
        match = self.calculate(job, profile).json()
        application = self.application(job, match_id=match["id"])
        history = self.events(application)
        self.profile(skills=[{"name": "Java"}])
        self.save(job, {"title": "岗位后续更名", "jd_text": "必须掌握 Java"})
        restored = self.owner.get(f"/api/v1/applications/{application['id']}").json()
        self.assertEqual(restored["id"], application["id"])
        self.assertEqual(restored["status"], "preparing")
        self.assertEqual(restored["job_title"], application["job_title"], "Creation snapshot must preserve the actual historical target")
        self.assertTrue(restored["match"]["stale"])
        self.assertIn("profile_changed", restored["match"]["stale_reasons"])
        self.assertIn("job_changed", restored["match"]["stale_reasons"])
        self.assertEqual(self.events(application), history)

    def test_foreign_or_wrong_job_match_id_cannot_be_attached(self):
        profile, job = self.profile(), self.job()
        match = self.calculate(job, profile).json()
        second_job = self.create(self.payload(title="无关合成岗位"))
        response = self.owner.post("/api/v1/applications", json=self.application_payload(second_job, match_id=match["id"]))
        self.assertEqual(response.status_code, 404, response.text)
        foreign_job = self.create(self.payload(title="他人的合成岗位"), client=self.other)
        response = self.other.post("/api/v1/applications", json=self.application_payload(foreign_job, match_id=match["id"]))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 0)

    def test_resume_deletion_cleans_matching_evidence_and_preserves_minimal_application_reference(self):
        upload = self.owner.post("/api/v1/resumes", files={"file": ("synthetic.pdf", profile_harness.synthetic_pdf("Synthetic resume: Python"), "application/pdf")})
        self.assertEqual(upload.status_code, 201, upload.text)
        resume_id = UUID(upload.json()["id"])
        with Session(self.engine) as db:
            proposed = ingest_profile_candidates(db, user_id=self.owner_id, resume_id=resume_id, batch_key="application-source",
                payload={"schema_version": "1.0", "extraction_version": "synthetic", "candidates": [{"category": "skill", "value": {"name": "Python"}, "evidence": [{"field": "name", "source_text": "Python"}]}]})
        confirmed = self.owner.post(f"/api/v1/profile/candidates/{proposed.candidates[0].id}/review", json={"revision": proposed.revision, "action": "confirm"})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        job = self.job()
        match = self.calculate(job, confirmed.json()).json()
        application = self.application(job, match_id=match["id"])
        history = self.events(application)
        self.assertEqual(self.owner.delete(f"/api/v1/resumes/{resume_id}").status_code, 204)
        restored = self.owner.get(f"/api/v1/applications/{application['id']}").json()
        self.assertEqual(restored["match"]["id"], match["id"])
        self.assertTrue(restored["match"]["stale"])
        self.assertEqual(self.events(application), history)
        self.assertNotIn(str(resume_id), str(restored))
        self.assertNotIn("source_text", str(restored))
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ResumeDocument, resume_id))
            stored = db.get(JobMatch, UUID(match["id"]))
            for item in stored.evaluation["items"]:
                for fact in item["profile_evidence"]:
                    self.assertIsNone(fact["resume_id"])
                    self.assertIsNone(fact["source_text"])

    def concurrent_requests(self, requests):
        barrier = threading.Barrier(len(requests))
        clients = [self.client() for _ in requests]
        for client in clients:
            client.cookies.update(self.owner.cookies)
            client.headers.update({"X-CSRF-Token": self.owner.headers["X-CSRF-Token"]})
        def worker(index):
            method, path, payload = requests[index]
            barrier.wait(timeout=10)
            response = clients[index].request(method, path, json=payload)
            return response.status_code, response.json() if response.content else None
        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            return list(executor.map(worker, range(len(requests))))

    def test_true_two_session_same_key_create_race_creates_one_record_and_event(self):
        payload = self.application_payload(self.create(), idempotency_key="race-same-create")
        results = self.concurrent_requests([("POST", "/api/v1/applications", payload)] * 2)
        self.assertEqual([code for code, _ in results], [201, 201], results)
        self.assertEqual(results[0][1]["id"], results[1][1]["id"])
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 1)
        self.assertEqual(self.events(results[0][1])["total"], 1)

    def test_true_two_session_different_key_create_race_prevents_duplicate_active_attempt(self):
        job = self.create()
        results = self.concurrent_requests([("POST", "/api/v1/applications", self.application_payload(job)) for _ in range(2)])
        self.assertEqual(sorted(code for code, _ in results), [201, 409], results)
        application = next(value for code, value in results if code == 201)
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 1)
        self.assertEqual(self.events(application)["total"], 1)

    def test_true_two_session_revision_race_has_one_winner_and_one_409_without_lost_update(self):
        application = self.application()
        path = f"/api/v1/applications/{application['id']}"
        results = self.concurrent_requests([("PUT", path, self.edit_payload(application, notes=notes)) for notes in ("并发输入甲", "并发输入乙")])
        self.assertEqual(sorted(code for code, _ in results), [200, 409], results)
        winner = next(value for code, value in results if code == 200)
        self.assertEqual(self.owner.get(path).json(), winner)
        self.assertEqual(winner["revision"], application["revision"] + 1)
        self.assertEqual(self.events(application)["total"], 2)

    def test_true_two_session_same_key_update_race_is_one_operation(self):
        application = self.application()
        payload = self.edit_payload(application, notes="并发重试的同一输入", status="submitted", idempotency_key="race-edit-once")
        results = self.concurrent_requests([("PUT", f"/api/v1/applications/{application['id']}", payload)] * 2)
        self.assertEqual([code for code, _ in results], [200, 200], results)
        self.assertEqual(results[0][1]["revision"], 2)
        self.assertEqual(results[1][1]["revision"], 2)
        self.assertEqual(self.events(application)["total"], 3)

    def test_application_quota_rejects_new_record_atomically_but_still_allows_deletion(self):
        application = self.application()
        job = self.create(self.payload(title="额度外的合成职位"))
        with patch.object(services, "MAX_APPLICATIONS_PER_USER", 1):
            response = self.owner.post("/api/v1/applications", json=self.application_payload(job))
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "application_limit")
            self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 1)
            deletion = self.owner.request("DELETE", f"/api/v1/applications/{application['id']}",
                json={"revision": 1, "idempotency_key": "quota-delete", "confirmed": True})
            self.assertEqual(deletion.status_code, 204, deletion.text)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Application)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(ApplicationEvent)), 0)

    def test_attempt_quota_preserves_terminal_history_and_refuses_new_attempt(self):
        job = self.create()
        application = self.edit(self.application(job), status="closed")
        with patch.object(services, "MAX_ATTEMPTS_PER_JOB", 1):
            response = self.owner.post("/api/v1/applications", json=self.application_payload(job, reapply=True))
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "application_attempt_limit")
        self.assertEqual(self.owner.get(f"/api/v1/applications/{application['id']}").json(), application)
        self.assertEqual(self.events(application)["total"], 2)

    def test_history_quota_rolls_back_revision_and_all_fields_in_multi_change_operation(self):
        application = self.application()
        with patch.object(services, "MAX_EVENTS_PER_APPLICATION", 2):
            conflict = self.edit(application, expected=409, status="interview", notes="不能部分提交的合成输入")
            self.assertEqual(conflict["detail"]["code"], "application_history_limit")
        self.assertEqual(self.owner.get(f"/api/v1/applications/{application['id']}").json(), application)
        self.assertEqual(self.events(application)["total"], 1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ApplicationReceipt)), 1)

    def test_receipt_quota_rolls_back_create_update_and_keeps_explicit_delete_available(self):
        application = self.application()
        job = self.create(self.payload(title="回执额度外合成职位"))
        with patch.object(services, "MAX_RECEIPTS_PER_USER", 1):
            create_response = self.owner.post("/api/v1/applications", json=self.application_payload(job))
            self.assertEqual(create_response.status_code, 409, create_response.text)
            conflict = self.edit(application, expected=409, status="interview", notes="额度失败不能残留的备注")
            self.assertEqual(conflict["detail"]["code"], "application_request_limit")
            self.assertEqual(self.owner.get(f"/api/v1/applications/{application['id']}").json(), application)
            self.assertEqual(self.events(application)["total"], 1)
            deletion = self.owner.request("DELETE", f"/api/v1/applications/{application['id']}",
                json={"revision": 1, "idempotency_key": "receipt-quota-delete", "confirmed": True})
            self.assertEqual(deletion.status_code, 204, deletion.text)
        self.assertEqual(self.owner.get("/api/v1/applications").json()["total"], 0)


class ActionTimeBoundaryTests(unittest.TestCase):
    def action(self, **changes):
        return NextActionInput(title="合成日期边界行动", due_date="2026-10-15", **changes)

    def test_date_only_stays_pending_through_whole_shanghai_day_then_overdue_at_midnight(self):
        action = self.action()
        for current, expected in (("2026-10-14T16:00:00+00:00", False),
                                  ("2026-10-15T15:59:59.999999+00:00", False),
                                  ("2026-10-15T16:00:00+00:00", True)):
            with self.subTest(current=current):
                self.assertEqual(services.is_action_overdue(action, now=datetime.fromisoformat(current)), expected)
        self.assertIsNone(action.due_time)

    def test_explicit_time_is_due_at_exact_instant_and_overdue_after_it(self):
        action = self.action(due_time="00:05")
        for current, expected in (("2026-10-14T16:04:59+00:00", False),
                                  ("2026-10-14T16:05:00+00:00", False),
                                  ("2026-10-14T16:05:00.000001+00:00", True)):
            self.assertEqual(services.is_action_overdue(action, now=datetime.fromisoformat(current)), expected)

    def test_equivalent_offset_instants_have_same_overdue_result(self):
        action = self.action(due_time="09:00")
        current = datetime(2026, 10, 15, 1, 1, tzinfo=timezone.utc)
        for offset in (-7, 0, 8):
            self.assertTrue(services.is_action_overdue(action, now=current.astimezone(timezone(timedelta(hours=offset)))))

    def test_completed_cancelled_unscheduled_or_absent_actions_cannot_be_overdue(self):
        late = datetime(2099, 1, 1, tzinfo=timezone.utc)
        for action in (None, NextActionInput(title="无截止日期"), self.action(status="completed"), self.action(status="cancelled")):
            self.assertFalse(services.is_action_overdue(action, now=late))

    def test_leap_day_date_only_rolls_over_on_shanghai_calendar(self):
        action = NextActionInput(title="合成闰日行动", due_date="2028-02-29")
        self.assertFalse(services.is_action_overdue(action, now=datetime.fromisoformat("2028-02-29T15:59:59Z")))
        self.assertTrue(services.is_action_overdue(action, now=datetime.fromisoformat("2028-02-29T16:00:00Z")))


if __name__ == "__main__":
    unittest.main()
