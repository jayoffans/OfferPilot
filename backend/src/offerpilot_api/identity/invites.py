"""Local operator CLI; invitation delivery is explicitly manual in S1-1."""

import argparse
import sys

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from offerpilot_api.core.config import get_settings
from offerpilot_api.db.session import SessionLocal
from offerpilot_api.identity.services import create_invitation


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local, email-bound registration invitation")
    parser.add_argument("--email", required=True)
    parser.add_argument("--expires-hours", type=int, default=48, choices=range(1, 169))
    args = parser.parse_args()
    if get_settings().environment != "development":
        parser.error("This local operator command is available only in development")
    try:
        with SessionLocal() as db:
            token = create_invitation(db, email=args.email, ttl_seconds=args.expires_hours * 3600)
    except (ValidationError, ValueError, SQLAlchemyError):
        print("Could not create invitation; check input and isolated database migrations.", file=sys.stderr)
        return 1
    # Intentional one-time operator output, never an application log or API.
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
