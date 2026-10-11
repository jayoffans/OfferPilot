"""Explicit local operator recovery; bounded batches, no queue or hidden work."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from offerpilot_api.core.config import get_settings
from offerpilot_api.db.session import SessionLocal, engine
from offerpilot_api.resume_ingestion.lifecycle import recover_pending_files
from offerpilot_api.resume_ingestion.storage import LocalResumeStorage, StorageError


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover interrupted local resume uploads/deletions")
    parser.add_argument("--stale-upload-seconds", type=int, default=3600)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if get_settings().environment != "development":
        parser.error("This local recovery command is available only in development")
    if args.stale_upload_seconds < 60 or not 1 <= args.limit <= 1000:
        parser.error("Use a stale threshold of at least 60 seconds and a batch limit of 1..1000")
    try:
        storage = LocalResumeStorage(get_settings().upload_dir)
        with SessionLocal() as db:
            result = recover_pending_files(
                db, storage, stale_before=datetime.now(timezone.utc) - timedelta(seconds=args.stale_upload_seconds),
                limit=args.limit,
            )
        print(json.dumps(asdict(result)))
        return 1 if result.failed else 0
    except (SQLAlchemyError, StorageError):
        print("Recovery unavailable; check the isolated database and storage configuration.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
