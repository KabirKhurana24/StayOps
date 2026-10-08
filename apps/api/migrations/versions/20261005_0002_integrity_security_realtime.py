"""Integrity, security and realtime layer.

* No-double-booking EXCLUDE constraint (btree_gist).
* Row Level Security enabled on every table with *no* policies: the Supabase Data
  API (PostgREST) with the publishable key sees nothing. All reads/writes go through
  the StayOps API, which connects as the table owner and enforces tenancy itself.
* Supabase Realtime: AFTER triggers publish a minimal change notice on a *private*
  per-organization broadcast topic (``org:<organization_id>``). An RLS policy on
  ``realtime.messages`` only lets authenticated members of that organization
  subscribe. Skipped automatically on non-Supabase Postgres.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL_TABLES = (
    "organizations",
    "profiles",
    "organization_members",
    "properties",
    "units",
    "guests",
    "reservations",
    "events",
    "audit_logs",
)
# table -> trigger operations that are broadcast
REALTIME_TABLES = {
    "reservations": "INSERT OR UPDATE OR DELETE",
    "properties": "INSERT OR UPDATE OR DELETE",
    "units": "INSERT OR UPDATE OR DELETE",
    "guests": "INSERT OR UPDATE OR DELETE",
    "events": "INSERT",
    "audit_logs": "INSERT",
}
BLOCKING_STATUSES = "('PENDING', 'CONFIRMED', 'CHECKED_IN')"
POLICY = "stayops_org_members_receive_broadcasts"


def _realtime_requested() -> bool:
    attr = context.config.attributes.get("install_realtime")
    if attr is not None:
        return bool(attr)
    flag = context.get_x_argument(as_dictionary=True).get("realtime")
    return flag is None or flag.lower() not in ("0", "false", "no", "off")


def _supabase_realtime_available() -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text(
                "SELECT to_regprocedure('realtime.send(jsonb,text,text,boolean)') IS NOT NULL "
                "AND to_regprocedure('auth.uid()') IS NOT NULL"
            )
        ).scalar()
    )


def _role_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :n)"), {"n": name})
        .scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    # --- No double booking -------------------------------------------------------
    has_extensions_schema = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions')")
    ).scalar()
    op.execute(
        "CREATE EXTENSION IF NOT EXISTS btree_gist"
        + (" WITH SCHEMA extensions" if has_extensions_schema else "")
    )
    op.execute(
        f"""
        ALTER TABLE reservations ADD CONSTRAINT ex_reservations_no_overlap
        EXCLUDE USING gist (unit_id WITH =, tstzrange(check_in, check_out, '[)') WITH &&)
        WHERE (status IN {BLOCKING_STATUSES})
        """
    )

    # --- Deny-by-default RLS + no Data API grants ---------------------------------
    for table in ALL_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for role in ("anon", "authenticated"):
        if _role_exists(role):
            for table in ALL_TABLES:
                op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")

    # --- Supabase Realtime (private broadcast per organization) --------------------
    if not (_realtime_requested() and _supabase_realtime_available()):
        return

    op.execute("CREATE SCHEMA IF NOT EXISTS stayops_private")
    op.execute("REVOKE ALL ON SCHEMA stayops_private FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA stayops_private TO authenticated")

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION stayops_private.is_org_member(org uuid)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = ''
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM "{schema}".organization_members m
            WHERE m.organization_id = org AND m.user_id = (SELECT auth.uid())
          );
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION stayops_private.can_receive_topic(topic text)
        RETURNS boolean
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = ''
        AS $$
        BEGIN
          IF topic IS NULL
             OR topic !~ '^org:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
            RETURN false;
          END IF;
          RETURN stayops_private.is_org_member(substring(topic FROM 5)::uuid);
        END;
        $$;
        """
    )
    for fn in ("is_org_member(uuid)", "can_receive_topic(text)"):
        op.execute(f"REVOKE ALL ON FUNCTION stayops_private.{fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION stayops_private.{fn} TO authenticated")

    # Receive-only for clients: there is deliberately no INSERT policy, so browsers
    # cannot publish into an organization's topic. Only the database (below) can.
    op.execute(f'DROP POLICY IF EXISTS "{POLICY}" ON realtime.messages')
    op.execute(
        f"""
        CREATE POLICY "{POLICY}" ON realtime.messages
        FOR SELECT TO authenticated
        USING (
          realtime.messages.extension = 'broadcast'
          AND stayops_private.can_receive_topic((SELECT realtime.topic()))
        )
        """
    )

    # Minimal change notice (ids + a few routing hints, no PII). Clients use it to
    # invalidate queries and refetch through the authorized API.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION stayops_private.broadcast_change()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = ''
        AS $$
        DECLARE
          rec jsonb;
          payload jsonb;
        BEGIN
          IF TG_OP = 'DELETE' THEN rec := to_jsonb(OLD); ELSE rec := to_jsonb(NEW); END IF;
          payload := jsonb_strip_nulls(jsonb_build_object(
            'table', TG_TABLE_NAME,
            'operation', TG_OP,
            'id', rec->>'id',
            'organization_id', rec->>'organization_id',
            'status', rec->>'status',
            'event_type', rec->>'event_type',
            'action', rec->>'action',
            'entity_type', rec->>'entity_type',
            'entity_id', rec->>'entity_id',
            'aggregate_id', rec->>'aggregate_id',
            'at', now()
          ));
          BEGIN
            PERFORM realtime.send(
              payload,
              TG_TABLE_NAME || '.' || lower(TG_OP),
              'org:' || (rec->>'organization_id'),
              true
            );
          EXCEPTION WHEN OTHERS THEN
            -- Realtime is a projection of committed state; never fail the write.
            RAISE WARNING 'stayops realtime broadcast failed: %', SQLERRM;
          END;
          RETURN NULL;
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION stayops_private.broadcast_change() FROM PUBLIC")

    for table, ops in REALTIME_TABLES.items():
        op.execute(f"DROP TRIGGER IF EXISTS stayops_realtime_broadcast ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER stayops_realtime_broadcast
            AFTER {ops} ON {table}
            FOR EACH ROW EXECUTE FUNCTION stayops_private.broadcast_change()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    realtime_installed = bind.execute(
        sa.text("SELECT to_regprocedure('stayops_private.broadcast_change()') IS NOT NULL")
    ).scalar()
    if realtime_installed:
        for table in REALTIME_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS stayops_realtime_broadcast ON {table}")
        op.execute(f'DROP POLICY IF EXISTS "{POLICY}" ON realtime.messages')
        op.execute("DROP FUNCTION IF EXISTS stayops_private.broadcast_change()")
        op.execute("DROP FUNCTION IF EXISTS stayops_private.can_receive_topic(text)")
        op.execute("DROP FUNCTION IF EXISTS stayops_private.is_org_member(uuid)")

    for table in ALL_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE reservations DROP CONSTRAINT IF EXISTS ex_reservations_no_overlap")
