"""Run existing real PostgreSQL tests with local random credentials on any OS."""
from __future__ import annotations

import argparse
import os
import shutil
import sys

from sqlalchemy.engine import URL

from manage import ROOT, postgres_credentials, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("specific", "regression", "all"), default="all")
    parser.add_argument("--e2e", action="store_true", help="Also run existing Chromium suite against PostgreSQL")
    args = parser.parse_args()
    values = postgres_credentials()
    run(["docker", "compose", "--env-file", str(ROOT / ".env.s5-postgres"), "-f",
         str(ROOT / "compose.postgres.yml"), "up", "-d", "--wait"])
    common = dict(drivername="postgresql+psycopg", host="127.0.0.1",
                  port=int(values.get("S5_POSTGRES_PORT", "55432")), database="offerpilot_s5_test")
    env = dict(os.environ)
    env.update({
        "OFFERPILOT_POSTGRES_ADMIN_URL": URL.create(username="s5_migrator", password=values["S5_MIGRATOR_PASSWORD"], **common).render_as_string(hide_password=False),
        "OFFERPILOT_POSTGRES_APP_URL": URL.create(username="s5_app", password=values["S5_APP_PASSWORD"], **common).render_as_string(hide_password=False),
        "OFFERPILOT_ENVIRONMENT": "development", "OFFERPILOT_DATABASE_URL": "sqlite:///:memory:",
        "OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED": "false", "PYTHONIOENCODING": "utf-8",
        "NEXT_TELEMETRY_DISABLED": "1",
    })
    if args.suite in {"specific", "all"}:
        run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests/postgres", "-t", ".", "-v"], env=env)
    if args.suite in {"regression", "all"}:
        run([sys.executable, "-B", "scripts/postgres/regression.py", "--output",
             "frontend/artifacts/portfolio/pg-regression.json"], env=env)
    if args.e2e:
        env["OFFERPILOT_E2E_POSTGRES"] = "true"
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            parser.error("Install Node.js, run npm ci and install Playwright Chromium first")
        run([npm, "--prefix", "frontend", "run", "test:e2e"], env=env)
    print("PostgreSQL checks completed; only fresh per-test databases were used. Docker volume preserved.")


if __name__ == "__main__":
    main()
