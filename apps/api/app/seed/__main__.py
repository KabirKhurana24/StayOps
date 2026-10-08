"""Seed the demo organization.

    uv run python -m app.seed

Creates/updates the demo Supabase auth user (needs SUPABASE_SECRET_KEY and
SEED_DEMO_PASSWORD). Without the secret key it falls back to an existing profile
with SEED_DEMO_EMAIL (i.e. a user who has signed up and opened the app once).
"""

from __future__ import annotations

import sys
import uuid

from sqlalchemy import select

from app.config.settings import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_session_factory
from app.events import handlers  # noqa: F401  (registers subscribers)
from app.organizations.models import Profile
from app.seed.demo import DEMO_ORG_NAME, seed_demo
from app.seed.supabase_admin import SupabaseAdmin, SupabaseAdminError

logger = get_logger("stayops.seed")


def main() -> int:
    settings = get_settings()
    configure_logging("WARNING", json_logs=False)
    email = settings.seed_demo_email

    with get_session_factory()() as session:
        user_id: uuid.UUID | None
        if settings.supabase_secret_key is not None:
            if settings.seed_demo_password is None:
                print("SEED_DEMO_PASSWORD is required to create the demo login.", file=sys.stderr)
                return 1
            try:
                with SupabaseAdmin(settings) as admin:
                    user_id = admin.ensure_user(
                        email, settings.seed_demo_password.get_secret_value(), "Demo Operator"
                    )
            except SupabaseAdminError as exc:
                print(f"Could not provision the demo auth user: {exc}", file=sys.stderr)
                return 1
        else:
            user_id = session.scalar(select(Profile.id).where(Profile.email == email))
            if user_id is None:
                print(
                    f"No SUPABASE_SECRET_KEY set and no existing profile for {email}.\n"
                    "Either set SUPABASE_SECRET_KEY (+ SEED_DEMO_PASSWORD), or sign up as "
                    f"{email} in the web app first and re-run the seed.",
                    file=sys.stderr,
                )
                return 1

        counts = seed_demo(session, user_id, email)

    print(f"Seeded '{DEMO_ORG_NAME}': " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    print(f"Sign in as {email} (password: SEED_DEMO_PASSWORD from your .env)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
