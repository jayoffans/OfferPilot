"""Finite local Mock processing/recovery; no production or paid provider path."""

import argparse
import json
import sys

from sqlalchemy.exc import SQLAlchemyError

from offerpilot_api.agent_runtime.extraction_runs import process_extraction_batch, recover_expired_runs
from offerpilot_api.core.config import get_settings
from offerpilot_api.db.session import SessionLocal, engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Process or recover opt-in synthetic Mock extraction runs")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()
    if not get_settings().mock_extraction_available:
        parser.error("This command requires development and OFFERPILOT_MOCK_EXTRACTION_ENABLED=true")
    if not 1 <= args.limit <= 100:
        parser.error("Use a batch limit of 1..100")
    try:
        if args.recover_only:
            with SessionLocal() as db:
                result = {"recovered": recover_expired_runs(db, limit=args.limit)}
        else:
            result = process_extraction_batch(SessionLocal, limit=args.limit, settings=get_settings())
        print(json.dumps(result))
        return 0
    except SQLAlchemyError:
        print("Extraction storage unavailable; check the isolated database and migration.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
