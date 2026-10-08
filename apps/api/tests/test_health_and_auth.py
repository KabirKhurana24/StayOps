"""Health, token verification and request plumbing (no database required)."""

from __future__ import annotations

import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import verify_access_token
from app.config.settings import Settings
from app.core.errors import AuthenticationError
from app.core.logging import REDACTED, redact
from tests.conftest import TEST_JWT_SECRET, make_token


def test_health_check(bare_client: TestClient) -> None:
    r = bare_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert len(r.headers["x-request-id"]) >= 8
    assert r.headers["x-content-type-options"] == "nosniff"


def test_inbound_request_id_is_propagated(bare_client: TestClient) -> None:
    r = bare_client.get("/health", headers={"X-Request-ID": "lb-req-12345678"})
    assert r.headers["x-request-id"] == "lb-req-12345678"
    # Malformed inbound IDs are replaced, not reflected.
    r = bare_client.get("/health", headers={"X-Request-ID": "<script>"})
    assert r.headers["x-request-id"] != "<script>"


def test_missing_token_is_401_with_error_envelope(bare_client: TestClient) -> None:
    r = bare_client.get("/api/v1/properties")
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "unauthenticated"
    assert body["request_id"] == r.headers["x-request-id"]


def test_cors_allows_only_configured_origin(bare_client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "POST"}
    ok = bare_client.options(
        "/api/v1/reservations", headers={"Origin": "http://localhost:3000", **preflight}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    evil = bare_client.options(
        "/api/v1/reservations", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in evil.headers


class TestTokenVerification:
    def test_valid_token(self, test_settings: Settings) -> None:
        uid = uuid.uuid4()
        verified = verify_access_token(make_token(uid, "a@b.test"), test_settings)
        assert verified.user_id == uid
        assert verified.email == "a@b.test"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"expires_in": -120},
            {"audience": "anon"},
            {"issuer": "https://attacker.supabase.co/auth/v1"},
            {"secret": "wrong-secret-wrong-secret-wrong-secret"},
            {"role": "service_role"},
        ],
        ids=["expired", "wrong-audience", "wrong-issuer", "bad-signature", "non-user-role"],
    )
    def test_rejected_tokens(self, test_settings: Settings, kwargs: dict[str, object]) -> None:
        token = make_token(uuid.uuid4(), **kwargs)  # type: ignore[arg-type]
        with pytest.raises(AuthenticationError):
            verify_access_token(token, test_settings)

    def test_alg_none_rejected(self, test_settings: Settings) -> None:
        token = jwt.encode(
            {"sub": str(uuid.uuid4()), "aud": "authenticated"}, key=None, algorithm="none"
        )
        with pytest.raises(AuthenticationError):
            verify_access_token(token, test_settings)

    def test_hs256_rejected_when_no_secret_configured(self, test_settings: Settings) -> None:
        settings = test_settings.model_copy(update={"supabase_jwt_secret": None})
        with pytest.raises(AuthenticationError):
            verify_access_token(make_token(uuid.uuid4()), settings)

    def test_garbage(self, test_settings: Settings) -> None:
        with pytest.raises(AuthenticationError):
            verify_access_token("not.a.jwt", test_settings)


def test_log_redaction_scrubs_credentials() -> None:
    event = {
        "authorization": "Bearer abc",
        "nested": {"password": "hunter2", "api_key": "sk_live", "ok": "visible"},
        "items": [{"access_token": "t"}],
        "event": "x",
    }
    cleaned = redact(event)
    assert cleaned["authorization"] == REDACTED
    assert cleaned["nested"] == {"password": REDACTED, "api_key": REDACTED, "ok": "visible"}
    assert cleaned["items"][0]["access_token"] == REDACTED
    assert TEST_JWT_SECRET not in str(cleaned)
