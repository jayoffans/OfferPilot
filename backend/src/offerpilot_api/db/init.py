"""Apply all database migrations to the configured database."""

from alembic import command
from alembic.config import Config

from offerpilot_api.core.config import BACKEND_ROOT


def initialize_database() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    initialize_database()
