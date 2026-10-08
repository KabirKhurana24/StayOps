"""Engine + session management (sync SQLAlchemy 2.x on psycopg 3).

FastAPI runs sync dependencies/endpoints in a threadpool, so a sync engine keeps
the code simple without blocking the event loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


def build_engine(url: str, *, pool_size: int = 5, max_overflow: int = 5) -> Engine:
    engine = create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10},
    )

    # Every session talks UTC (values are timestamptz regardless). Done with SET rather
    # than the `options` startup parameter, which connection poolers may reject.
    @event.listens_for(engine, "connect")
    def _set_utc(dbapi_connection: Any, _: Any) -> None:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")
        dbapi_connection.commit()

    return engine


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return build_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
