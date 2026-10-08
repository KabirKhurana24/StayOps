"""The migrations produce exactly the schema the ORM models describe."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text

from app.db.registry import Base

# Objects that intentionally exist only in migrations.
MIGRATION_ONLY = {"ex_reservations_no_overlap"}


def _names(diff: object) -> set[str]:
    found: set[str] = set()
    stack = [diff]
    while stack:
        item = stack.pop()
        if isinstance(item, list | tuple):
            stack.extend(item)
        elif getattr(item, "name", None):
            found.add(item.name)  # type: ignore[attr-defined]
    return found


def test_migrations_match_models(db_connection: Connection) -> None:
    ctx = MigrationContext.configure(
        db_connection, opts={"compare_type": True, "include_schemas": False}
    )
    diffs = [d for d in compare_metadata(ctx, Base.metadata) if not (_names(d) & MIGRATION_ONLY)]
    assert diffs == [], f"models and migrations disagree: {diffs}"


def test_rls_enabled_on_every_table(db_connection: Connection) -> None:
    rows = db_connection.execute(
        text(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = current_schema() AND c.relkind = 'r'"
            " AND c.relname <> 'alembic_version'"
        )
    ).all()
    assert rows, "no tables found"
    assert {name for name, rls in rows if not rls} == set()
