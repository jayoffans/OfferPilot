"""Run existing API/domain integration suites on fresh real PostgreSQL databases.

The existing test methods retain their assertions. Each harness's engine and
Settings construction are redirected to the migrated isolated database. The
authentication outage injection uses migration-role REVOKE instead of SQLite DDL.
This runner never opens a supplied development database or creates SQLite files.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/src"), str(ROOT / "tests")]

from offerpilot_api.core.config import Settings
from tests.postgres.support import isolated_database

MODULES = (
    "test_authentication", "test_resume_upload", "test_resume_ownership",
    "test_resume_file_lifecycle", "test_resume_lifecycle_failures",
    "test_trusted_profile", "test_extraction_runs", "test_byok_security",
    "test_deepseek_preview_consistency", "test_job_catalog", "test_job_matches",
    "test_applications", "test_dashboard",
)

class PostgreSQLResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started, self.successes = [], []

    def startTest(self, test):
        super().startTest(test)
        self.started.append(test.id())
        self._stack = ExitStack()
        db = self._stack.enter_context(isolated_database("regression"))
        test.postgres_migration_url = db.migration_url
        def settings(*args, **kwargs):
            if args:
                raise TypeError("Settings test adapter expects keyword arguments")
            kwargs["database_url"] = db.database_url
            return Settings(**kwargs)
        # Some suites reuse another module's setUp function. Patch its original
        # globals, not merely the module declaring the current TestCase.
        for name in MODULES:
            module = sys.modules.get(name)
            if module is None:
                continue
            if hasattr(module, "build_engine"):
                self._stack.enter_context(patch.object(module, "build_engine", return_value=db.engine))
            if hasattr(module, "Settings"):
                self._stack.enter_context(patch.object(module, "Settings", side_effect=settings))

    def stopTest(self, test):
        try:
            self._stack.close()
        except Exception:
            # Report bounded fixture-cleanup failures as errors and retain a
            # final JSON result instead of losing the entire run's evidence.
            if test.id() in self.successes:
                self.successes.remove(test.id())
            self.addError(test, sys.exc_info())
        finally:
            super().stopTest(test)

    def addSuccess(self, test):
        self.successes.append(test.id())
        super().addSuccess(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modules", nargs="+", choices=MODULES, default=MODULES)
    parser.add_argument("--output", type=Path, default=ROOT / "frontend/artifacts/s5-1/pg-regression.json")
    args = parser.parse_args()
    if not os.environ.get("OFFERPILOT_POSTGRES_ADMIN_URL") or not os.environ.get("OFFERPILOT_POSTGRES_APP_URL"):
        parser.error("Configure isolated local S5 PostgreSQL URLs with scripts/postgres/env.ps1 first")
    modules = [importlib.import_module(name) for name in args.modules]
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromModule(module) for module in modules)
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2, resultclass=PostgreSQLResult).run(suite)
    report = {"modules": list(args.modules), "tests_run": result.testsRun,
        "passed": len(result.successes), "elapsed_seconds": time.perf_counter() - started,
        "failed": [{"test": test.id(), "traceback": trace} for test, trace in result.failures],
        "errors": [{"test": test.id(), "traceback": trace} for test, trace in result.errors],
        "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        "database": "real PostgreSQL; fresh Alembic-head database per test; restricted application role",
        "provider_calls": "only existing injected Mock/Fake providers; no paid provider requests"}
    report["module_results"] = [{"module": name,
        "run": sum(test.startswith(name + ".") for test in result.started),
        "passed": sum(test.startswith(name + ".") for test in result.successes),
        "failed": sum(test.id().startswith(name + ".") for test, _ in result.failures),
        "errors": sum(test.id().startswith(name + ".") for test, _ in result.errors),
        "skipped": sum(test.id().startswith(name + ".") for test, _ in result.skipped)}
        for name in args.modules]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
