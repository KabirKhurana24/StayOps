"""Alembic environment.

Configuration knobs (all optional):

* ``config.attributes["connection"]`` - reuse an existing connection (tests).
* ``config.attributes["schema"]`` / ``-x schema=NAME`` - migrate into a non-public
  schema (tests use a disposable schema).
* ``config.attributes["install_realtime"]`` / ``-x realtime=false`` - skip the
  Supabase-specific realtime/RLS-helper objects (they are also skipped
  automatically when the target database is not Supabase).
"""

from __future__ import annotations

import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, text

from app.config.settings import get_settings
from app.db.registry import Base
from app.db.session import build_engine

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
x_args = context.get_x_argument(as_dictionary=True)
schema: str | None = config.attributes.get("schema") or x_args.get("schema")
if schema is not None and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
    raise ValueError(f"invalid schema name: {schema!r}")


def _configure_and_run(connection: Connection) -> None:
    if schema:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        connection.execute(text(f'SET search_path TO "{schema}", extensions'))
        connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
        compare_type=True,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    existing: Connection | None = config.attributes.get("connection")
    if existing is not None:
        _configure_and_run(existing)
        return
    engine = build_engine(get_settings().database_url, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as connection:
            _configure_and_run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
