"""Finite recovery; explicitly initialize the registry only for a new rollout."""
import argparse
import json

from offerpilot_api.account_lifecycle.deletion import replay_deletion_registry
from offerpilot_api.account_lifecycle.exports import process_export_batch
from offerpilot_api.account_lifecycle.ledger import DeletionLedger, LedgerError
from offerpilot_api.core.config import get_settings
from offerpilot_api.db.session import SessionLocal
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-ledger", action="store_true")
    parser.add_argument("--limit", type=int, default=100, choices=range(1, 1001))
    args = parser.parse_args()
    settings = get_settings()
    try:
        if args.initialize_ledger:
            DeletionLedger(settings.deletion_ledger_dir).initialize()
            print(json.dumps({"registry_initialized": True}))
            return 0
        with SessionLocal() as db:
            result = replay_deletion_registry(db, settings=settings, storage=LocalResumeStorage(settings.upload_dir), limit=args.limit)
        with SessionLocal() as db:
            exports = process_export_batch(db, LocalResumeStorage(settings.upload_dir), settings, limit=min(100, args.limit))
        print(json.dumps({"deletion": result, "exports": exports}))
        return int(bool(result["failed"] or exports["cleanup"]["failed"]))
    except Exception:
        # Deliberately no traceback/connection strings or filesystem paths.
        print(json.dumps({"error_code": "lifecycle_recovery_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
