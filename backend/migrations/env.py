"""Alembic environment using the OfferPilot database configuration."""

from alembic import context

from offerpilot_api.core.config import get_settings
from offerpilot_api.db.base import Base
from offerpilot_api.db import models as _models  # Register ORM tables with Base.metadata.
from offerpilot_api.db.session import build_engine, prepare_database_url

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = prepare_database_url(get_settings().database_url)
    context.configure(
        url=url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    migration_engine = build_engine(get_settings().database_url)
    try:
        with migration_engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=connection.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        migration_engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
