"""Actual PostgreSQL migration history, native types and database constraints."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from offerpilot_api.db.base import Base
from offerpilot_api.db.models import User, ResumeDocument, Profile, ProfileFact
from tests.postgres.support import PostgresTestCase, isolated_database, migrate


class PostgreSQLMigrationTests(PostgresTestCase):
    def test_empty_database_model_constraints_indexes_and_native_types(self):
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(text("SELECT version_num FROM alembic_version")), "0010_account_lifecycle")
            self.assertEqual(compare_metadata(MigrationContext.configure(conn), Base.metadata), [])
            self.assertEqual(conn.scalar(text("SHOW timezone")), "UTC")
            inspector = inspect(conn)
            self.assertEqual(set(inspector.get_table_names()), set(Base.metadata.tables) | {"alembic_version"})
            for table in Base.metadata.sorted_tables:
                actual = {c["name"]: c for c in inspector.get_columns(table.name)}
                for column in table.columns:
                    self.assertEqual(actual[column.name]["nullable"], column.nullable, f"{table.name}.{column.name}")
                    if str(column.type) == "CHAR(32)":
                        self.assertEqual(str(actual[column.name]["type"]), "UUID")
                expected = {i.name for i in table.indexes}
                self.assertTrue(expected <= {i["name"] for i in inspector.get_indexes(table.name)})

    def test_uuid_utc_json_null_unique_fk_and_delete_cascade(self):
        uid, rid, pid = uuid4(), uuid4(), uuid4()
        with self.session_factory() as db:
            db.add(User(id=uid, email_normalized="native@example.invalid", password_hash="synthetic"))
            db.commit()
            db.add(ResumeDocument(id=rid, user_id=uid, file_name="合成.pdf", file_path="", file_type="application/pdf"))
            db.commit()
            db.add(Profile(id=pid, resume_id=rid, education=[{"school": "合成大学"}], skills=["Python"], projects=[], internships=[]))
            db.commit()
            db.add(ProfileFact(profile_id=pid, field_name="skill", value={"nested": [True, None, "中文"]}, confidence=.9, source_text="合成"))
            db.commit()
            self.assertEqual(db.get(Profile, pid).education, [{"school": "合成大学"}])
            self.assertEqual(db.get(ResumeDocument, rid).created_at.utcoffset(), timedelta(0))
            for row in (User(email_normalized="native@example.invalid", password_hash="other"),
                        ResumeDocument(file_name=None, file_path="", file_type="pdf"),
                        ResumeDocument(user_id=uuid4(), file_name="x", file_path="", file_type="pdf")):
                db.add(row)
                with self.assertRaises(IntegrityError):
                    db.commit()
                db.rollback()
            db.execute(text("DELETE FROM resume_documents WHERE id=:id"), {"id": rid})
            db.commit()
            self.assertEqual(db.scalar(select(text("count(*)")).select_from(Profile)), 0)
            self.assertEqual(db.scalar(select(text("count(*)")).select_from(ProfileFact)), 0)

    def test_application_role_cannot_ddl_or_create_database(self):
        with self.engine.connect() as conn:
            for sql in ("CREATE TABLE public.forbidden(id int)", "CREATE DATABASE forbidden"):
                with self.assertRaises(DBAPIError):
                    conn.execute(text(sql))
                conn.rollback()
            self.assertEqual(conn.execute(text("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user")).one(), (False, False, False))

    def test_synthetic_history_upgrades_each_revision_without_rewriting_facts(self):
        with isolated_database("history", migrate_head=False) as history:
            from offerpilot_api.db.session import build_engine
            migration_engine = build_engine(history.migration_url)
            self.addCleanup(migration_engine.dispose)
            revisions = ["0001_resume_intelligence", "0002_identity_ownership", "0003_resume_file_lifecycle",
                         "0004_trusted_career_profile", "0005_resume_extraction_runs", "0006_deepseek_byok",
                         "0007_private_jobs", "0008_trusted_job_matches", "0009_application_tracker", "0010_account_lifecycle"]
            uid, rid, pid, fid, cid, candidate, runid = [uuid4() for _ in range(7)]
            jobid, snapshotid, matchid = [uuid4() for _ in range(3)]
            now = datetime.now(timezone.utc)
            preserved = {}
            for index, revision in enumerate(revisions):
                migrate(history.migration_url, revision)
                metadata = MetaData()
                metadata.reflect(migration_engine)
                def insert(conn, table, **values):
                    conn.execute(metadata.tables[table].insert().values(**values))
                with migration_engine.begin() as conn:
                    if index == 0:
                        insert(conn, "resume_documents", id=rid, file_name="历史合成.pdf", file_path="legacy", file_type="pdf", raw_text="历史合成原文")
                        insert(conn, "profiles", id=pid, resume_id=rid, education=[{"school": "历史大学"}], skills=["Python"], projects=[], internships=[])
                        insert(conn, "profile_facts", id=fid, profile_id=pid, field_name="skills", value=["Python"], confidence=.8, source_text="历史合成原文")
                    if index == 1:
                        insert(conn, "users", id=uid, email_normalized="history@example.invalid", password_hash="synthetic")
                        conn.execute(metadata.tables["resume_documents"].update().where(metadata.tables["resume_documents"].c.id == rid).values(user_id=uid))
                    if index == 3:
                        insert(conn, "career_profiles", id=cid, user_id=uid, revision=1)
                        insert(conn, "profile_candidates", id=candidate, profile_id=cid, resume_id=rid, batch_key="history", item_index=0,
                               batch_digest="a"*64, category="skill", value={"name": "Python", "level": None}, evidence={},
                               evidence_status="supported", status="pending", schema_version="1.0", extraction_version="history")
                    if index == 4:
                        insert(conn, "resume_extraction_runs", id=runid, user_id=uid, resume_id=rid, idempotency_key="history",
                               source_version="a"*64, max_attempts=2, max_calls=4, timeout_seconds=30,
                               schema_version="2.0", extraction_version="history", prompt_version="history", provider_version="mock")
                        conn.execute(metadata.tables["profile_candidates"].update().where(metadata.tables["profile_candidates"].c.id == candidate).values(extraction_run_id=runid))
                    if index == 6:
                        insert(conn, "saved_jobs", id=jobid, user_id=uid, title="历史合成岗位", work_type="internship",
                               source_kind="pasted", content_digest="b"*64, revision=1, jd_version=1)
                        insert(conn, "job_jd_snapshots", id=snapshotid, job_id=jobid, version=1,
                               jd_text="必须掌握 Python", text_sha256="c"*64)
                        insert(conn, "job_requirements", id=uuid4(), job_id=jobid, snapshot_id=snapshotid,
                               type="technical_skill", content="必须掌握 Python", priority="must", review_status="pending",
                               source_kind="rule", position=0, revision=1, schema_version="1.0")
                    if index == 7:
                        insert(conn, "job_matches", id=matchid, user_id=uid, job_id=jobid, profile_revision=1,
                               job_revision=1, jd_version=1, rule_version="history", schema_version="1.0",
                               evaluation={"synthetic_history": [True, None, "中文"]})
                    for table_name in ("resume_documents", "profiles", "profile_facts", "users", "career_profiles", "profile_candidates", "resume_extraction_runs", "saved_jobs", "job_jd_snapshots", "job_requirements", "job_matches"):
                        if table_name not in metadata.tables:
                            continue
                        rows = [dict(r._mapping) for r in conn.execute(select(metadata.tables[table_name]))]
                        for old in preserved.get(table_name, []):
                            actual = next(r for r in rows if r["id"] == old["id"])
                            for key, value in old.items():
                                if (table_name, key, index) in {("resume_documents", "user_id", 1), ("profile_candidates", "extraction_run_id", 4)}:
                                    continue
                                self.assertEqual(actual[key], value, f"{revision}:{table_name}.{key}")
                        preserved[table_name] = rows
            with migration_engine.connect() as conn:
                self.assertEqual(conn.scalar(text("SELECT extraction_run_id FROM profile_candidates")), runid)
                self.assertEqual(conn.scalar(text("SELECT status FROM profile_candidates")), "pending")
                self.assertEqual(conn.scalar(text("SELECT count(*) FROM career_profile_entries")), 0)
