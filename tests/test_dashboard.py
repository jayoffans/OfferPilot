"""S4-2 real authenticated dashboard projections, boundaries and bounded reads."""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import event, select
from sqlalchemy.orm import Session

import test_applications as harness
from offerpilot_api.application_tracker.dashboard import get_dashboard
from offerpilot_api.application_tracker import services
from offerpilot_api.db.models import (
    Application, ApplicationEvent, CareerProfile, CareerProfileEntry, JobMatch, ResumeDocument, SavedJob,
)
from offerpilot_api.job_intelligence import matching

NOW = datetime(2026, 12, 31, 4, 0, tzinfo=timezone.utc)  # Shanghai 12:00


class DashboardTests(unittest.TestCase):
    setUp = harness.ApplicationTests.setUp
    client = harness.ApplicationTests.client
    account = harness.ApplicationTests.account
    login = harness.ApplicationTests.login
    payload = harness.ApplicationTests.payload
    create = harness.ApplicationTests.create
    save = harness.ApplicationTests.save
    profile = harness.ApplicationTests.profile
    job = harness.ApplicationTests.job
    calculate = harness.ApplicationTests.calculate
    application_payload = harness.ApplicationTests.application_payload
    application = harness.ApplicationTests.application
    edit_payload = harness.ApplicationTests.edit_payload
    edit = harness.ApplicationTests.edit
    action = harness.ApplicationTests.action
    events = harness.ApplicationTests.events
    concurrent_requests = harness.ApplicationTests.concurrent_requests

    def dashboard(self, client=None, now=NOW, **params):
        with patch("offerpilot_api.application_tracker.dashboard._now", return_value=now):
            response = (client or self.owner).get("/api/v1/dashboard", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response.json()

    def seed(self, *, user_id=None, status="preparing", action_status=None, due_date=None,
             due_time=None, count=1, events=0):
        """Large fixtures use real isolated DB tables, never production or HTTP mocks."""
        result = []
        with Session(self.engine) as db:
            for index in range(count):
                job = SavedJob(user_id=user_id or self.owner_id, title=f"合成岗位 {index}", company="合成公司",
                    work_type="unknown", source_kind="manual", content_digest=uuid4().hex,
                    revision=1, jd_version=1, created_at=NOW, updated_at=NOW)
                db.add(job)
                db.flush()
                row = Application(user_id=job.user_id, job_id=job.id, job_title=job.title,
                    company=job.company, attempt_number=1, status=status, active_slot=None if status in services.TERMINAL_STATUSES else 1,
                    revision=1, notes="不应出现在轻量工作台的申请正文", created_at=NOW, updated_at=NOW,
                    action_title="合成行动" if action_status else None,
                    action_notes="合成行动备注" if action_status else None, action_due_date=due_date,
                    action_due_time=due_time, action_status=action_status)
                db.add(row)
                db.flush()
                for revision in range(events):
                    db.add(ApplicationEvent(user_id=job.user_id, application_id=row.id, revision=revision + 1,
                        type="status_changed", changes={"before": "preparing", "after": "submitted"},
                        occurred_at=NOW, recorded_at=NOW + timedelta(seconds=revision)))
                result.append((row.id, job.id))
            db.commit()
        return result

    def test_empty_dashboard_has_honest_counts_and_nonblocking_first_step(self):
        result = self.dashboard()
        self.assertEqual(result["summary"], {"total": 0, "preparing": 0, "submitted": 0,
            "assessment_interview": 0, "offer": 0, "ended": 0})
        self.assertTrue(all(group == {"total": 0, "items": []} for group in result["actions"].values()))
        self.assertEqual(result["jobs"], {"total": 0, "items": []})
        self.assertEqual(result["recent_activity"], [])
        self.assertEqual(result["onboarding"]["next_step"], "upload_resume")
        self.assertEqual((result["timezone"], result["today"]), ("Asia/Shanghai", "2026-12-31"))

    def test_pending_groups_are_disjoint_and_timed_today_can_be_overdue(self):
        definitions = [
            ("overdue", date(2026, 12, 30), None), ("overdue", date(2026, 12, 31), "11:59"),
            ("today", date(2026, 12, 31), "12:00"), ("today", date(2026, 12, 31), None),
            ("upcoming", date(2027, 1, 1), None), ("upcoming", date(2027, 1, 7), "23:59"),
            (None, date(2027, 1, 8), None), ("undated", None, None)]
        expected = {key: set() for key in ("overdue", "today", "upcoming", "undated")}
        for group, due_date, due_time in definitions:
            row_id, _ = self.seed(action_status="pending", due_date=due_date, due_time=due_time)[0]
            if group:
                expected[group].add(str(row_id))
        result = self.dashboard(limit=20)
        for key, ids in expected.items():
            group = result["actions"][key]
            self.assertEqual(group["total"], len(ids))
            self.assertEqual({row["application_id"] for row in group["items"]}, ids)
            self.assertTrue(all(row["is_overdue"] == (key == "overdue") for row in group["items"]))
        all_ids = [row["application_id"] for group in result["actions"].values() for row in group["items"]]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        date_only = next(row for row in result["actions"]["today"]["items"] if row["next_action"]["due_time"] is None)
        self.assertEqual(date_only["next_action"]["due_date"], "2026-12-31")

    def test_timed_exact_minute_and_microsecond_agree_between_list_dashboard_detail(self):
        row_id, _ = self.seed(action_status="pending", due_date=date(2026, 12, 31), due_time="12:00")[0]
        for now, overdue in ((NOW, False), (NOW + timedelta(microseconds=1), True)):
            result = self.dashboard(now=now)
            self.assertEqual(result["actions"]["overdue"]["total"], int(overdue))
            with patch.object(services, "_now", return_value=now):
                listed = self.owner.get("/api/v1/applications", params={"overdue": "true"}).json()
                detail = self.owner.get(f"/api/v1/applications/{row_id}").json()
            self.assertEqual(listed["total"], int(overdue))
            self.assertEqual(detail["is_overdue"], overdue)

    def test_date_only_midnight_month_year_and_leap_boundaries(self):
        for date_text, utc_before in (("2026-12-31", "2026-12-31T15:59:59.999999+00:00"),
                                     ("2028-02-29", "2028-02-29T15:59:59.999999+00:00"),
                                     ("2027-01-31", "2027-01-31T15:59:59.999999+00:00")):
            with self.subTest(date=date_text):
                row_id, _ = self.seed(action_status="pending", due_date=date.fromisoformat(date_text))[0]
                now = datetime.fromisoformat(utc_before)
                before = self.dashboard(now=now, limit=20)
                self.assertIn(str(row_id), [row["application_id"] for row in before["actions"]["today"]["items"]])
                after = self.dashboard(now=now + timedelta(microseconds=1), limit=20)
                self.assertIn(str(row_id), [row["application_id"] for row in after["actions"]["overdue"]["items"]])

    def test_completed_cancelled_and_terminal_pending_are_never_due(self):
        for status in ("completed", "cancelled"):
            self.seed(action_status=status, due_date=date(2000, 1, 1))
        # A valid historical S4-1 row may still have a pending terminal action.
        terminal_id, _ = self.seed(status="closed", action_status="pending", due_date=date(2000, 1, 1))[0]
        result = self.dashboard()
        self.assertEqual([result["actions"][key]["total"] for key in ("overdue", "today", "upcoming", "undated")], [0] * 4)
        self.assertEqual(result["actions"]["completed"]["total"], 1)
        self.assertEqual(result["actions"]["cancelled"]["total"], 1)
        self.assertFalse(self.owner.get(f"/api/v1/applications/{terminal_id}").json()["is_overdue"])
        self.assertEqual(self.owner.get("/api/v1/applications", params={"next_action": "pending"}).json()["total"], 0)

    def test_summary_counts_current_applications_once_and_never_favorites_or_events(self):
        for status in ("preparing", "submitted", "assessment", "interview", "offer", "rejected", "withdrawn", "closed"):
            self.seed(status=status, events=4)
        job = self.create()
        self.owner.patch(f"/api/v1/jobs/{job['id']}/favorite", json={"revision": job["revision"], "is_favorite": True})
        self.assertEqual(self.dashboard()["summary"], {"total": 8, "preparing": 1, "submitted": 1,
            "assessment_interview": 2, "offer": 1, "ended": 3})

    def test_dashboard_isolates_users_and_ignores_forged_owner_queries(self):
        own_id, own_job_id = self.seed(action_status="pending", due_date=date(2000, 1, 1), events=1)[0]
        foreign_id, foreign_job_id = self.seed(user_id=self.other_id, action_status="pending", due_date=date(2000, 1, 1), events=1)[0]
        own = self.dashboard(user_id=str(self.other_id), limit=20)
        other = self.dashboard(client=self.other, limit=20)
        self.assertEqual([row["application_id"] for row in own["actions"]["overdue"]["items"]], [str(own_id)])
        self.assertEqual([row["application_id"] for row in other["actions"]["overdue"]["items"]], [str(foreign_id)])
        self.assertEqual(own["summary"]["total"], 1)
        self.assertEqual(own["recent_activity"][0]["application_id"], str(own_id))
        self.assertEqual([row["id"] for row in own["jobs"]["items"]], [str(own_job_id)])
        self.assertEqual([row["id"] for row in other["jobs"]["items"]], [str(foreign_job_id)])
        response = self.anonymous.get("/api/v1/dashboard")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.owner.put(f"/api/v1/applications/{foreign_id}", json=self.edit_payload(
            self.other.get(f"/api/v1/applications/{foreign_id}").json(), next_action=None)).status_code, 404)

    def test_dashboard_limit_validation_and_bounded_group_totals(self):
        self.seed(action_status="pending", due_date=date(2000, 1, 1), count=9)
        result = self.dashboard(limit=2)
        self.assertEqual(result["actions"]["overdue"]["total"], 9)
        self.assertEqual(len(result["actions"]["overdue"]["items"]), 2)
        for limit in (0, 21, "all"):
            self.assertEqual(self.owner.get("/api/v1/dashboard", params={"limit": limit}).status_code, 422)

    def test_application_filters_and_pagination_use_current_pending_state(self):
        for status, due in ((None, None), ("pending", None), ("pending", date(2000, 1, 1)),
                            ("completed", date(2000, 1, 1)), ("cancelled", date(2000, 1, 1))):
            self.seed(action_status=status, due_date=due)
        for state, total in (("none", 1), ("pending", 2), ("completed", 1), ("cancelled", 1)):
            response = self.owner.get("/api/v1/applications", params={"next_action": state, "page_size": 1})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["total"], total)
            self.assertEqual(len(response.json()["items"]), 1)
        self.assertEqual(self.owner.get("/api/v1/applications", params={"overdue": "true"}).json()["total"], 1)
        self.assertEqual(self.owner.get("/api/v1/applications", params={"overdue": "false"}).json()["total"], 4)
        self.assertEqual(self.owner.get("/api/v1/applications", params={"next_action": "completed", "overdue": "true"}).json()["total"], 0)
        self.assertEqual(self.owner.get("/api/v1/applications", params={"next_action": "invalid"}).status_code, 422)

    def test_due_sort_places_timed_2359_before_date_only_and_unscheduled(self):
        date_only, _ = self.seed(action_status="pending", due_date=date(2026, 12, 31))[0]
        timed, _ = self.seed(action_status="pending", due_date=date(2026, 12, 31), due_time="23:59")[0]
        undated, _ = self.seed(action_status="pending")[0]
        listed = self.owner.get("/api/v1/applications", params={"sort": "next_action_asc"}).json()["items"]
        self.assertEqual([row["id"] for row in listed], [str(timed), str(date_only), str(undated)])
        today = self.dashboard()["actions"]["today"]["items"]
        self.assertEqual([row["application_id"] for row in today], [str(timed), str(date_only)])

    def test_terminal_transition_cancels_unfinished_action_once_and_reopen_is_explicit(self):
        application = self.edit(self.application(), next_action=self.action(due_date="2000-01-01"))
        payload = self.edit_payload(application, status="closed")
        first = self.owner.put(f"/api/v1/applications/{application['id']}", json=payload)
        second = self.owner.put(f"/api/v1/applications/{application['id']}", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        closed = first.json()
        self.assertEqual(closed["next_action"]["status"], "cancelled")
        self.assertFalse(closed["is_overdue"])
        changes = [row for row in self.events(closed)["items"] if row["type"] == "next_action_changed"]
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["changes"]["after"]["status"], "cancelled")
        reopened = self.edit(closed, status="submitted")
        self.assertEqual(reopened["next_action"]["status"], "cancelled")
        restored = self.edit(reopened, next_action={**reopened["next_action"], "status": "pending"})
        self.assertEqual(self.dashboard()["actions"]["overdue"]["items"][0]["revision"], restored["revision"])

    def test_completion_duplicate_replay_and_stale_edit_never_duplicate_action_events(self):
        application = self.edit(self.application(), next_action=self.action(due_date="2000-01-01"))
        payload = self.edit_payload(application, next_action={**application["next_action"], "status": "completed"})
        for _ in range(2):
            response = self.owner.put(f"/api/v1/applications/{application['id']}", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.dashboard()["actions"]["completed"]["total"], 1)
        self.assertEqual(self.dashboard()["actions"]["overdue"]["total"], 0)
        self.assertEqual(self.edit(application, expected=409)["detail"]["code"], "application_revision_conflict")
        self.assertEqual(sum(row["type"] == "next_action_changed" for row in self.events(application)["items"]), 2)

    def test_true_two_session_completion_same_key_has_single_event_and_current_dashboard(self):
        application = self.edit(self.application(), next_action=self.action(due_date="2000-01-01"))
        payload = self.edit_payload(application, next_action={**application["next_action"], "status": "completed"})
        results = self.concurrent_requests([("PUT", f"/api/v1/applications/{application['id']}", payload)] * 2)
        self.assertEqual([code for code, _ in results], [200, 200], results)
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(sum(row["type"] == "next_action_changed" for row in self.events(application)["items"]), 2)
        result = self.dashboard()
        self.assertEqual(result["actions"]["overdue"]["total"], 0)
        self.assertEqual(result["actions"]["completed"]["total"], 1)

    def test_action_date_edit_immediately_moves_groups_and_delete_removes_activity(self):
        application = self.edit(self.application(), next_action=self.action(due_date="2000-01-01"))
        self.assertEqual(self.dashboard()["actions"]["overdue"]["total"], 1)
        application = self.edit(application, next_action=self.action(due_date="2027-01-02"))
        result = self.dashboard()
        self.assertEqual(result["actions"]["overdue"]["total"], 0)
        self.assertEqual(result["actions"]["upcoming"]["total"], 1)
        response = self.owner.request("DELETE", f"/api/v1/applications/{application['id']}", json={
            "revision": application["revision"], "idempotency_key": uuid4().hex, "confirmed": True})
        self.assertEqual(response.status_code, 204, response.text)
        result = self.dashboard()
        self.assertEqual(result["summary"]["total"], 0)
        self.assertEqual(result["recent_activity"], [])

    def test_actionable_jobs_review_favorite_application_and_unmatched_are_real(self):
        pending = self.create()
        reviewed = self.job()
        favorite = self.owner.patch(f"/api/v1/jobs/{reviewed['id']}/favorite", json={
            "revision": reviewed["revision"], "is_favorite": True}).json()
        result = self.dashboard(limit=20)
        rows = {row["id"]: row for row in result["jobs"]["items"]}
        self.assertEqual(rows[pending["id"]]["reasons"], ["review_pending"])
        self.assertEqual(rows[pending["id"]]["next_step"], "review")
        self.assertEqual(set(rows[favorite["id"]]["reasons"]), {"favorite_without_application", "unmatched"})
        self.assertEqual(rows[favorite["id"]]["next_step"], "match")
        self.application(favorite)
        rows = {row["id"]: row for row in self.dashboard(limit=20)["jobs"]["items"]}
        self.assertNotIn("favorite_without_application", rows[favorite["id"]]["reasons"])
        self.assertEqual(self.dashboard()["onboarding"]["next_step"], "manage_actions")

    def test_match_staleness_tracks_profile_job_rule_and_latest_basis(self):
        profile, job = self.profile(), self.job()
        result = self.calculate(job, profile).json()
        self.assertNotIn(job["id"], [row["id"] for row in self.dashboard(limit=20)["jobs"]["items"]])
        self.profile()
        rows = {row["id"]: row for row in self.dashboard(limit=20)["jobs"]["items"]}
        self.assertEqual(rows[job["id"]]["reasons"], ["match_stale"])
        with Session(self.engine) as db:
            current_profile = db.scalar(select(CareerProfile).where(CareerProfile.user_id == self.owner_id))
            row = db.get(JobMatch, UUID(result["id"]))
            row.profile_revision = current_profile.revision
            row.rule_version = "older-rule"
            db.commit()
        self.assertIn("match_stale", self.dashboard()["jobs"]["items"][0]["reasons"])
        with Session(self.engine) as db:
            db.get(JobMatch, UUID(result["id"])).rule_version = matching.RULE_VERSION
            db.commit()
        self.assertEqual(self.dashboard()["jobs"]["total"], 0)
        self.save(job, {"company": "合成更新公司"})
        self.assertIn("match_stale", self.dashboard()["jobs"]["items"][0]["reasons"])

    def test_onboarding_uses_formal_progress_and_never_forces_repeat_upload(self):
        self.profile()
        result = self.dashboard()
        self.assertGreater(result["onboarding"]["profile_entry_count"], 0)
        self.assertEqual(result["onboarding"]["resume_count"], 0)
        self.assertEqual(result["onboarding"]["next_step"], "import_job")
        job = self.job()
        result = self.dashboard()
        self.assertEqual(result["onboarding"]["next_step"], "match_job")
        self.assertEqual(result["onboarding"]["job_id"], job["id"])
        self.calculate(job, self.owner.get("/api/v1/profile").json())
        self.assertEqual(self.dashboard()["onboarding"]["next_step"], "create_application")

    def test_list_batch_does_not_expose_same_owner_match_from_a_different_job(self):
        profile, job = self.profile(), self.job()
        match = self.calculate(job, profile).json()
        application = self.application(self.create(self.payload(title="另一合成岗位")))
        with Session(self.engine) as db:
            # A plain match FK cannot enforce the domain's same-job relation;
            # projections must keep the guard even for an inconsistent row.
            db.get(Application, UUID(application["id"])).match_id = UUID(match["id"])
            db.commit()
        listed = self.owner.get("/api/v1/applications").json()["items"]
        self.assertEqual(len(listed), 1)
        self.assertIsNone(listed[0]["match"])

    def test_preference_only_profile_is_ready_and_no_requirements_does_not_claim_reviewed(self):
        with Session(self.engine) as db:
            db.add(CareerProfile(user_id=self.owner_id, revision=1, preferences={"locations": ["上海"]}))
            db.commit()
        self.assertEqual(self.dashboard()["onboarding"]["next_step"], "import_job")
        job = self.create(self.payload(jd_text="", requirements=[]))
        result = self.dashboard()
        self.assertEqual(result["jobs"]["total"], 1)
        self.assertEqual(result["jobs"]["items"][0]["reasons"], ["requirements_missing"])
        self.assertEqual(result["onboarding"]["next_step"], "review_job")
        self.assertEqual(result["onboarding"]["job_id"], job["id"])

    def test_cleared_preferences_do_not_invent_a_ready_profile(self):
        with Session(self.engine) as db:
            db.add(CareerProfile(user_id=self.owner_id, revision=1, preferences={
                "locations": [], "work_types": [], "target_roles": [], "salary": None,
                "weekly_days": None, "internship_months": None}))
            db.commit()
        self.assertEqual(self.dashboard()["onboarding"]["next_step"], "upload_resume")
        self.job()
        self.assertEqual(self.dashboard()["onboarding"]["next_step"], "complete_profile")

    def test_recent_activity_uses_bounded_existing_recorded_events_without_notes(self):
        application = self.application()
        application = self.edit(application, status="submitted", notes="private-note-body", occurred_at="2000-01-01T00:00:00Z")
        application = self.edit(application, next_action=self.action())
        application = self.edit(application, next_action=self.action(status="completed"))
        result = self.dashboard(limit=2)
        self.assertEqual([row["type"] for row in result["recent_activity"]], ["next_action_changed", "status_changed"])
        self.assertEqual(result["recent_activity"][0]["action_status"], "completed")
        self.assertNotIn("private-note-body", str(result))
        self.assertNotIn("changes", str(result))

    def test_recent_status_activity_preserves_event_status_after_current_status_changes(self):
        application = self.application()
        application = self.edit(application, status="submitted")
        self.edit(application, status="closed")
        result = self.dashboard(limit=20)
        statuses = [row["application_status"] for row in result["recent_activity"]]
        self.assertEqual(statuses, ["closed", "submitted", "preparing"])

    def test_large_history_and_application_pages_have_constant_bounded_query_count(self):
        self.seed(action_status="pending", due_date=date(2000, 1, 1), count=120, events=30)
        statements = []
        def record(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            with Session(self.engine) as db:
                result = get_dashboard(db, user_id=self.owner_id, limit=3)
            self.assertLessEqual(len(statements), 12)
            self.assertEqual(result.actions.overdue.total, 120)
            self.assertEqual(len(result.actions.overdue.items), 3)
            self.assertEqual(len(result.recent_activity), 3)
            self.assertEqual(result.summary.total, 120)
            for statement in statements:
                self.assertNotIn("job_matches.evaluation", statement)
                self.assertNotIn("job_jd_snapshots.jd_text", statement)
                self.assertNotIn("resume_documents.raw_text", statement)
                self.assertNotRegex(statement, r"(?:SELECT |, )application_events\.changes(?:,|\n)")
                self.assertNotIn("applications.notes", statement)
            statements.clear()
            with Session(self.engine) as db:
                listed = services.list_applications(db, user_id=self.owner_id, page_size=100)
            self.assertEqual((listed.total, len(listed.items)), (120, 100))
            self.assertLessEqual(len(statements), 5)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)


if __name__ == "__main__":
    unittest.main()
