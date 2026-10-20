"""Inspect actual S5 Docker logs without disclosing captured log contents.

A native PostgreSQL installation has no Docker log source. Such an alternative
reports an explicit skip rather than claiming that server logging was checked.
"""

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.postgres.support import PostgresTestCase


CONTAINER = "offerpilot-s5-postgres"


class ServerLoggingPostgresTests(PostgresTestCase):
    def test_invalid_bound_value_does_not_reach_database_server_logs(self):
        docker = shutil.which("docker")
        if docker is None:
            self.skipTest("Docker CLI unavailable; native PostgreSQL server logs require separate verification")
        options = {"capture_output": True, "timeout": 20,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        try:
            inspection = subprocess.run([docker, "inspect", "--format",
                '{"running":{{json .State.Running}},"ports":{{json .NetworkSettings.Ports}}}',
                CONTAINER], **options)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("Fixed S5 Docker container unavailable; native PostgreSQL logging is not verified")
        if inspection.returncode != 0:
            self.skipTest("Fixed S5 Docker container absent or inaccessible; native PostgreSQL logging is not verified")
        try:
            state = json.loads(inspection.stdout)
            bindings = (state.get("ports") or {}).get("5432/tcp") or []
        except (ValueError, AttributeError):
            self.fail("S5 Docker container metadata could not be inspected")
        if not state.get("running") or not any(
            binding.get("HostIp") == "127.0.0.1"
            and binding.get("HostPort") == str(self.engine.url.port or 5432)
            for binding in bindings
        ):
            self.skipTest("Configured PostgreSQL is not the running fixed S5 Docker container; server logs are not verified")

        since = datetime.now(timezone.utc).isoformat()
        marker = "SYNTHETIC_S5_SQL_PARAMETER_" + uuid4().hex
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SHOW log_min_messages")), "fatal")
            with self.assertRaises(DBAPIError) as caught:
                connection.execute(text("SELECT CAST(:value AS integer)"), {"value": marker})
            self.assertEqual(getattr(caught.exception.orig, "sqlstate", None), "22P02")
            connection.rollback()
            self.assertEqual(connection.scalar(text("SELECT 1")), 1)

        # Allow Docker's stderr collector to consume the completed error event.
        time.sleep(0.25)
        try:
            captured = subprocess.run([docker, "logs", "--since", since, CONTAINER], **options)
        except (OSError, subprocess.TimeoutExpired):
            self.fail("S5 PostgreSQL container logs could not be read after the error probe")
        self.assertEqual(captured.returncode, 0, "S5 PostgreSQL container logs could not be read")
        # assertNotIn would print the full log stream on failure. Keep all
        # captured server messages private even when the regression is found.
        self.assertFalse(marker.encode("ascii") in captured.stdout + captured.stderr,
            "Synthetic SQL bound value appeared in PostgreSQL container logs")
