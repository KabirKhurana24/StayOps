"""Test harness.

DB-backed tests run real Alembic migrations into a *disposable schema* (never
`public`) on TEST_DATABASE_URL (default: DATABASE_URL), and each test runs inside
a transaction that is rolled back afterwards. Auth uses HS256 tokens signed with
a test-only secret, exercising the same verification code path as production.
If no database is reachable, DB tests are skipped with an explicit reason.

Tiers (markers, applied automatically — see docs/testing.md):

* ``unit``     — no database at all.
* ``db``       — real PostgreSQL (constraints, EXCLUDE, SKIP LOCKED, RLS flags).
                 Runs against the local test cluster in the fast cycle.
* ``supabase`` — needs Supabase itself (Realtime ``realtime.send`` /
                 ``realtime.messages``). Marked explicitly; runs against the real
                 project. With ``STAYOPS_REQUIRE_SUPABASE=1`` a missing Supabase
                 feature fails the test instead of skipping it, so the Supabase
                 tier can never pass vacuously.
"""

from __future__ import annotations

import json
import os

# Tests never use a configured LLM provider: AI tests inject the MockProvider
# explicitly (tests/ai_helpers.py). Set before app settings are first read.
os.environ["AI_PROVIDER"] = "disabled"
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config.settings import API_ROOT, Settings, get_settings
from app.db.session import build_engine, get_db
from app.main import create_app
from app.organizations.models import Organization, OrganizationMember, Profile, Role

# Phase 4 fixtures (guest_stay, …) live with their builders.
pytest_plugins = ["tests.inbox_helpers", "tests.ai_helpers"]

TEST_JWT_SECRET = "test-only-secret-not-used-anywhere-else-0123456789"
TEST_SUPABASE_URL = "https://stayops-test.supabase.co"
REQUIRE_SUPABASE = os.environ.get("STAYOPS_REQUIRE_SUPABASE", "").lower() in ("1", "true", "yes")


LIVE_LLM = os.environ.get("STAYOPS_LIVE_LLM", "").lower() in ("1", "true", "yes")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag every test with its tier so ``-m unit|db|supabase`` selects precisely."""
    for item in items:
        if item.get_closest_marker("llm_live") and not LIVE_LLM:
            # Paid provider calls are opt-in only, never part of a normal run.
            item.add_marker(pytest.mark.skip(reason="real LLM smoke test; set STAYOPS_LIVE_LLM=1"))
        if item.get_closest_marker("supabase"):
            continue
        uses_db = "db_engine" in getattr(item, "fixturenames", ())
        item.add_marker(pytest.mark.db if uses_db else pytest.mark.unit)


def require_supabase_feature(available: bool, reason: str) -> None:
    """Skip (fast tier) or fail (Supabase tier) when a Supabase feature is absent."""
    if available:
        return
    if REQUIRE_SUPABASE:
        pytest.fail(f"{reason} (STAYOPS_REQUIRE_SUPABASE=1: this tier must run on Supabase)")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    base = get_settings()
    return base.model_copy(
        update={
            "app_env": "test",
            "log_json": False,
            "supabase_url": TEST_SUPABASE_URL,
            "supabase_jwt_secret": SecretStr(TEST_JWT_SECRET),
            "api_cors_origins": ["http://localhost:3000"],
        }
    )


def make_token(
    user_id: uuid.UUID,
    email: str | None = None,
    *,
    secret: str = TEST_JWT_SECRET,
    expires_in: int = 3600,
    audience: str = "authenticated",
    issuer: str = f"{TEST_SUPABASE_URL}/auth/v1",
    role: str = "authenticated",
    **extra: Any,
) -> str:
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "email": email,
        "aud": audience,
        "iss": issuer,
        "role": role,
        "iat": now,
        "exp": now + expires_in,
        "user_metadata": {"full_name": extra.pop("full_name", None)},
        **extra,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


# --------------------------------------------------------------------------- DB


@pytest.fixture(scope="session")
def db_engine(test_settings: Settings) -> Iterator[Engine]:
    url = test_settings.test_database_url or test_settings.database_url
    schema = f"stayops_test_{uuid.uuid4().hex[:10]}"
    engine = build_engine(url, pool_size=2, max_overflow=2)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(
            f"database not reachable for DB tests ({type(exc.orig).__name__}); "
            "set DATABASE_URL or TEST_DATABASE_URL"
        )

    @event.listens_for(engine, "connect")
    def _search_path(dbapi_connection: Any, _: Any) -> None:
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}", extensions')
        dbapi_connection.commit()

    engine.dispose()  # drop pre-listener connections so every connection gets the search_path

    with engine.connect() as conn:
        cfg = Config(str(API_ROOT / "alembic.ini"))
        cfg.attributes["connection"] = conn
        cfg.attributes["schema"] = schema
        cfg.attributes["install_realtime"] = False
        cfg.attributes["skip_logging_config"] = True
        command.upgrade(cfg, "head")
        conn.commit()

    try:
        yield engine
    finally:
        with engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.commit()
        engine.dispose()


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    conn = db_engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
def db(db_connection: Connection) -> Iterator[Session]:
    # Service-level commits become SAVEPOINT releases; the outer transaction is
    # rolled back after the test, so tests never see each other's data.
    session = Session(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session, test_settings: Settings) -> Iterator[TestClient]:
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def bare_client(test_settings: Settings) -> Iterator[TestClient]:
    """Client for endpoints that must not touch the database."""
    app = create_app(test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# ------------------------------------------------------------------- tenants


@dataclass
class Member:
    user_id: uuid.UUID
    email: str
    organization_id: uuid.UUID
    role: Role

    @property
    def token(self) -> str:
        return make_token(self.user_id, self.email)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def add_member(db: Session, org: Organization, role: Role, email: str) -> Member:
    user_id = uuid.uuid4()
    db.add(Profile(id=user_id, email=email))
    db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user_id, role=role))
    db.flush()
    return Member(user_id=user_id, email=email, organization_id=org.id, role=role)


def make_org(db: Session, name: str) -> Organization:
    org = Organization(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
        default_currency="EUR",
    )
    db.add(org)
    db.flush()
    return org


@pytest.fixture
def org_a(db: Session) -> Organization:
    return make_org(db, "Alpha Stays")


@pytest.fixture
def org_b(db: Session) -> Organization:
    return make_org(db, "Bravo Rentals")


@pytest.fixture
def owner_a(db: Session, org_a: Organization) -> Member:
    return add_member(db, org_a, Role.OWNER, "owner@alpha.test")


@pytest.fixture
def operator_a(db: Session, org_a: Organization) -> Member:
    return add_member(db, org_a, Role.OPERATOR, "operator@alpha.test")


@pytest.fixture
def viewer_a(db: Session, org_a: Organization) -> Member:
    return add_member(db, org_a, Role.VIEWER, "viewer@alpha.test")


@pytest.fixture
def staff_a(db: Session, org_a: Organization) -> Member:
    return add_member(db, org_a, Role.STAFF, "staff@alpha.test")


@pytest.fixture
def owner_b(db: Session, org_b: Organization) -> Member:
    return add_member(db, org_b, Role.OWNER, "owner@bravo.test")


# ------------------------------------------------------------- API builders


def create_property(
    client: TestClient,
    member: Member,
    *,
    name: str = "Canal View Apartments",
    timezone: str = "Europe/Amsterdam",
) -> dict[str, Any]:
    r = client.post(
        "/api/v1/properties",
        headers=member.headers,
        json={"name": name, "address": "Prinsengracht 263, Amsterdam", "timezone": timezone},
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def create_unit(
    client: TestClient, member: Member, property_id: str, name: str = "101"
) -> dict[str, Any]:
    r = client.post(
        "/api/v1/units", headers=member.headers, json={"property_id": property_id, "name": name}
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def create_guest(
    client: TestClient,
    member: Member,
    first: str = "Sarah",
    last: str = "Johnson",
    email: str | None = None,
) -> dict[str, Any]:
    r = client.post(
        "/api/v1/guests",
        headers=member.headers,
        json={
            "first_name": first,
            "last_name": last,
            "email": email or f"{first}.{last}.{uuid.uuid4().hex[:6]}@example.com".lower(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@dataclass
class Stay:
    property: dict[str, Any]
    unit: dict[str, Any]
    guest: dict[str, Any]


@pytest.fixture
def stay_a(client: TestClient, owner_a: Member) -> Stay:
    prop = create_property(client, owner_a)
    unit = create_unit(client, owner_a, prop["id"])
    guest = create_guest(client, owner_a)
    return Stay(property=prop, unit=unit, guest=guest)


def reservation_body(stay: Stay, **overrides: Any) -> dict[str, Any]:
    body = {
        "unit_id": stay.unit["id"],
        "guest_id": stay.guest["id"],
        "check_in": "2026-11-02T15:00:00+01:00",
        "check_out": "2026-11-05T11:00:00+01:00",
        "adults": 2,
        "children": 0,
        "source": "AIRBNB",
        "total_amount": "540.00",
        "currency": "EUR",
    }
    body.update(overrides)
    return body


# ------------------------------------------------------------------ realtime


def install_realtime_triggers(db: Session, tables: tuple[str, ...]) -> None:
    """Attach the production broadcast trigger (migration 0002) to ``tables`` in this
    test's disposable schema. Runs inside the test transaction, which is rolled back,
    so nothing is ever actually broadcast. Requires Supabase Realtime."""
    available = db.execute(
        text(
            "SELECT to_regprocedure('stayops_private.broadcast_change()') IS NOT NULL "
            "AND to_regclass('realtime.messages') IS NOT NULL"
        )
    ).scalar()
    require_supabase_feature(bool(available), "Supabase Realtime is not installed on this database")
    for table in tables:
        db.execute(
            text(
                f"CREATE TRIGGER stayops_realtime_broadcast AFTER INSERT OR UPDATE OR DELETE "
                f"ON {table} FOR EACH ROW EXECUTE FUNCTION stayops_private.broadcast_change()"
            )
        )


def realtime_notices(db: Session, topic: str) -> list[tuple[str, dict[str, Any]]]:
    """Broadcast notices queued on ``topic`` in this transaction: (event, payload)."""
    rows = db.execute(
        text("SELECT event, payload FROM realtime.messages WHERE topic = :topic AND private"),
        {"topic": topic},
    ).all()
    return [(e, p if isinstance(p, dict) else json.loads(p)) for e, p in rows]
