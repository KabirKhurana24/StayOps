"""Minimal Supabase Auth admin client (seed only; uses the server-side secret key)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.config.settings import Settings


class SupabaseAdminError(RuntimeError):
    pass


class SupabaseAdmin:
    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url or settings.supabase_secret_key is None:
            raise SupabaseAdminError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
        key = settings.supabase_secret_key.get_secret_value()
        headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):  # legacy service_role JWT also goes in Authorization
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.Client(
            base_url=f"{settings.supabase_url.rstrip('/')}/auth/v1/admin",
            headers=headers,
            timeout=15,
        )

    def __enter__(self) -> SupabaseAdmin:
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def _check(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            # Body may echo request fields but never contains our key.
            raise SupabaseAdminError(
                f"Supabase admin API {response.request.method} "
                f"{response.request.url.path} failed: {response.status_code} {response.text[:300]}"
            )
        data: dict[str, Any] = response.json()
        return data

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        page = 1
        while True:
            data = self._check(self._client.get("/users", params={"page": page, "per_page": 200}))
            users = data.get("users", [])
            for user in users:
                if (user.get("email") or "").lower() == email.lower():
                    return user  # type: ignore[no-any-return]
            if len(users) < 200:
                return None
            page += 1

    def ensure_user(self, email: str, password: str, full_name: str) -> uuid.UUID:
        """Create the user (email pre-confirmed) or reset its password to the seed value."""
        attrs = {
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name},
        }
        existing = self.find_user_by_email(email)
        if existing:
            self._check(self._client.put(f"/users/{existing['id']}", json=attrs))
            return uuid.UUID(existing["id"])
        created = self._check(self._client.post("/users", json=attrs))
        return uuid.UUID(created["id"])
