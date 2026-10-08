"""Phase 4 guest experience: inbox, guest requests, property knowledge, portal links.

* ``guests`` — structured ``preferences`` (JSONB), private team ``notes``, ``version``.
* ``conversations`` — one per (reservation, channel); tenant-safe composite FKs to
  guest / reservation / property / unit and to the assignee's membership.
* ``messages`` — ``seq`` identity for strict ordering, per-side ``read_at``,
  ``internal`` (team-only) flag.
* ``guest_requests`` — the bridge to operations: links to the task / work order
  created from them.
* ``property_knowledge`` — structured guest information with PUBLIC / STAY /
  INTERNAL visibility (the grounding source for the Phase 5 guest agent).
* ``guest_portal_links`` — hashed per-stay access tokens for the guest portal.
* ``audit_logs.actor_type`` accepts ``GUEST``.
* RLS deny-by-default on every new table; realtime broadcast triggers on the inbox
  tables, and the broadcast notice gains ``conversation_id`` (an id, no PII) so
  open threads refresh precisely.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen enum values at the time of this migration (VARCHAR + CHECK, see 0001).
OLD_ACTOR_TYPE = ("USER", "SYSTEM", "INTEGRATION", "AI_AGENT")
NEW_ACTOR_TYPE = (*OLD_ACTOR_TYPE, "GUEST")
CHANNEL = ("IN_APP", "EMAIL", "SMS", "WHATSAPP", "AIRBNB", "BOOKING", "OTHER")
CONVERSATION_STATUS = ("OPEN", "PENDING", "RESOLVED", "CLOSED")
PRIORITY = ("LOW", "NORMAL", "HIGH", "URGENT")
SENDER_TYPE = ("GUEST", "STAFF", "SYSTEM", "AI")
MESSAGE_TYPE = ("TEXT", "SYSTEM", "REQUEST", "STATUS")
GUEST_REQUEST_TYPE = (
    "EARLY_CHECK_IN",
    "LATE_CHECKOUT",
    "EXTRA_TOWELS",
    "HOUSEKEEPING",
    "MAINTENANCE",
    "QUESTION",
    "OTHER",
)
GUEST_REQUEST_STATUS = ("OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "CLOSED", "CANCELLED")
GUEST_REQUEST_SOURCE = ("GUEST", "STAFF", "AI", "SYSTEM")
KNOWLEDGE_CATEGORY = (
    "WIFI",
    "CHECK_IN",
    "CHECK_OUT",
    "HOUSE_RULES",
    "PARKING",
    "DIRECTIONS",
    "AMENITIES",
    "APPLIANCES",
    "EMERGENCY",
    "LOCAL_INFO",
    "OTHER",
)
KNOWLEDGE_VISIBILITY = ("PUBLIC", "STAY", "INTERNAL")

NEW_TABLES = (
    "conversations",
    "messages",
    "guest_requests",
    "property_knowledge",
    "guest_portal_links",
)
# Portal links carry token hashes: not broadcast.
REALTIME_TABLES = ("conversations", "messages", "guest_requests", "property_knowledge")


def _enum_check(
    table: str, column: str, enum_name: str, values: tuple[str, ...]
) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(f"ck_{table}_{enum_name}"))


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organizations.id"],
        name=op.f(f"fk_{table}_organization_id_organizations"),
        ondelete="CASCADE",
    )


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _version() -> sa.Column:
    return sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False)


def _realtime_requested() -> bool:
    attr = context.config.attributes.get("install_realtime")
    if attr is not None:
        return bool(attr)
    flag = context.get_x_argument(as_dictionary=True).get("realtime")
    return flag is None or flag.lower() not in ("0", "false", "no", "off")


def _broadcast_function_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text("SELECT to_regprocedure('stayops_private.broadcast_change()') IS NOT NULL")
        )
        .scalar()
    )


def _role_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :n)"), {"n": name})
        .scalar()
    )


def _broadcast_function(extra_fields: str) -> str:
    """The migration-0002 notice function; ``extra_fields`` adds routing ids."""
    return f"""
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
            'aggregate_id', rec->>'aggregate_id',{extra_fields}
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


def upgrade() -> None:
    # --- guests: profile enhancements -------------------------------------------------
    op.add_column(
        "guests",
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("guests", sa.Column("notes", sa.String(4000), nullable=True))
    op.add_column("guests", _version())

    # --- audit: guests act through the portal -----------------------------------------
    op.drop_constraint(op.f("ck_audit_logs_actor_type"), "audit_logs", type_="check")
    allowed = ", ".join(f"'{v}'" for v in NEW_ACTOR_TYPE)
    op.create_check_constraint(
        op.f("ck_audit_logs_actor_type"), "audit_logs", f"actor_type IN ({allowed})"
    )

    # --- conversations ---------------------------------------------------------------
    op.create_table(
        "conversations",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(16), server_default="IN_APP", nullable=False),
        sa.Column("status", sa.String(16), server_default="OPEN", nullable=False),
        sa.Column("priority", sa.String(16), server_default="NORMAL", nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("subject", sa.String(200), nullable=True),
        _ts("last_message_at"),
        _version(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        _org_fk("conversations"),
        sa.ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            name="fk_conversations_guest_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_conversations_reservation_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            name="fk_conversations_property_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_conversations_unit_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_conversations_assignee_member",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_conversations_organization_id_id")
        ),
        sa.UniqueConstraint(
            "organization_id",
            "reservation_id",
            "channel",
            name=op.f("uq_conversations_organization_id_reservation_id_channel"),
        ),
        sa.CheckConstraint(
            "char_length(subject) <= 200", name=op.f("ck_conversations_subject_length")
        ),
        _enum_check("conversations", "channel", "channel", CHANNEL),
        _enum_check("conversations", "status", "conversation_status", CONVERSATION_STATUS),
        _enum_check("conversations", "priority", "conversation_priority", PRIORITY),
    )
    op.create_index(
        "ix_conversations_org_last_message_at",
        "conversations",
        ["organization_id", "last_message_at", "id"],
    )
    op.create_index("ix_conversations_org_status", "conversations", ["organization_id", "status"])
    op.create_index(
        "ix_conversations_org_guest_id", "conversations", ["organization_id", "guest_id"]
    )
    op.create_index(
        "ix_conversations_org_assigned_to", "conversations", ["organization_id", "assigned_to"]
    )

    # --- messages --------------------------------------------------------------------
    op.create_table(
        "messages",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("sender_type", sa.String(16), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=True),
        sa.Column("sender_label", sa.String(320), nullable=True),
        sa.Column("message_type", sa.String(16), server_default="TEXT", nullable=False),
        sa.Column("content", sa.String(4000), nullable=False),
        sa.Column("internal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _ts("read_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        _org_fk("messages"),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            name="fk_messages_conversation_same_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_messages_organization_id_id")),
        sa.CheckConstraint(
            "char_length(content) between 1 and 4000", name=op.f("ck_messages_content_length")
        ),
        _enum_check("messages", "sender_type", "sender_type", SENDER_TYPE),
        _enum_check("messages", "message_type", "message_type", MESSAGE_TYPE),
    )
    op.create_index("ix_messages_conversation_seq", "messages", ["conversation_id", "seq"])
    op.create_index(
        "ix_messages_unread",
        "messages",
        ["conversation_id", "sender_type"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index("ix_messages_org_created_at", "messages", ["organization_id", "created_at"])

    # --- guest requests --------------------------------------------------------------
    op.create_table(
        "guest_requests",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("priority", sa.String(16), server_default="NORMAL", nullable=False),
        sa.Column("status", sa.String(16), server_default="OPEN", nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        _ts("requested_for"),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("work_order_id", sa.Uuid(), nullable=True),
        sa.Column("resolution_note", sa.String(1000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_by_label", sa.String(320), nullable=True),
        _ts("resolved_at"),
        _ts("closed_at"),
        _version(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guest_requests")),
        _org_fk("guest_requests"),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            name="fk_guest_requests_conversation_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_guest_requests_reservation_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_guest_requests_unit_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_guest_requests_assignee_member",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "task_id"],
            ["tasks.organization_id", "tasks.id"],
            name="fk_guest_requests_task_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "work_order_id"],
            ["maintenance_work_orders.organization_id", "maintenance_work_orders.id"],
            name="fk_guest_requests_work_order_same_org",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_guest_requests_organization_id_id")
        ),
        sa.CheckConstraint(
            "char_length(description) between 1 and 2000",
            name=op.f("ck_guest_requests_description_length"),
        ),
        _enum_check("guest_requests", "type", "guest_request_type", GUEST_REQUEST_TYPE),
        _enum_check("guest_requests", "priority", "guest_request_priority", PRIORITY),
        _enum_check("guest_requests", "status", "guest_request_status", GUEST_REQUEST_STATUS),
        _enum_check("guest_requests", "source", "guest_request_source", GUEST_REQUEST_SOURCE),
    )
    for name, cols in (
        ("ix_guest_requests_org_status", ["organization_id", "status"]),
        ("ix_guest_requests_org_conversation", ["organization_id", "conversation_id"]),
        ("ix_guest_requests_org_reservation", ["organization_id", "reservation_id"]),
        ("ix_guest_requests_org_task", ["organization_id", "task_id"]),
        ("ix_guest_requests_org_work_order", ["organization_id", "work_order_id"]),
    ):
        op.create_index(name, "guest_requests", cols)

    # --- property knowledge ----------------------------------------------------------
    op.create_table(
        "property_knowledge",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.String(4000), nullable=False),
        sa.Column("visibility", sa.String(16), server_default="PUBLIC", nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("updated_by_label", sa.String(320), nullable=True),
        _ts("archived_at"),
        _version(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_property_knowledge")),
        _org_fk("property_knowledge"),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            name="fk_property_knowledge_property_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_property_knowledge_unit_same_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_property_knowledge_organization_id_id")
        ),
        sa.CheckConstraint(
            "char_length(title) between 1 and 200", name=op.f("ck_property_knowledge_title_length")
        ),
        sa.CheckConstraint(
            "char_length(content) between 1 and 4000",
            name=op.f("ck_property_knowledge_content_length"),
        ),
        _enum_check("property_knowledge", "category", "knowledge_category", KNOWLEDGE_CATEGORY),
        _enum_check(
            "property_knowledge", "visibility", "knowledge_visibility", KNOWLEDGE_VISIBILITY
        ),
    )
    op.create_index(
        "ix_property_knowledge_org_property_category",
        "property_knowledge",
        ["organization_id", "property_id", "category"],
    )

    # --- guest portal links ------------------------------------------------------------
    op.create_table(
        "guest_portal_links",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_by_label", sa.String(320), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("revoked_at"),
        _ts("last_used_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guest_portal_links")),
        _org_fk("guest_portal_links"),
        sa.ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_guest_portal_links_reservation_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            name="fk_guest_portal_links_guest_same_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_guest_portal_links_token_hash")),
    )
    op.create_index(
        "ix_guest_portal_links_active",
        "guest_portal_links",
        ["organization_id", "reservation_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # --- deny-by-default RLS (all access goes through the API) -----------------------
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for role in ("anon", "authenticated"):
        if _role_exists(role):
            for table in NEW_TABLES:
                op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")

    # --- realtime: same private per-organization broadcast as migration 0002 ----------
    if _realtime_requested() and _broadcast_function_exists():
        op.execute(_broadcast_function("\n            'conversation_id', rec->>'conversation_id',"))
        op.execute("REVOKE ALL ON FUNCTION stayops_private.broadcast_change() FROM PUBLIC")
        for table in REALTIME_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS stayops_realtime_broadcast ON {table}")
            op.execute(
                f"""
                CREATE TRIGGER stayops_realtime_broadcast
                AFTER INSERT OR UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION stayops_private.broadcast_change()
                """
            )


def downgrade() -> None:
    if _broadcast_function_exists():
        op.execute(_broadcast_function(""))
        op.execute("REVOKE ALL ON FUNCTION stayops_private.broadcast_change() FROM PUBLIC")
    for table in ("guest_portal_links", "property_knowledge", "guest_requests", "messages"):
        op.drop_table(table)
    op.drop_table("conversations")

    op.execute("DELETE FROM audit_logs WHERE actor_type = 'GUEST'")
    op.drop_constraint(op.f("ck_audit_logs_actor_type"), "audit_logs", type_="check")
    allowed = ", ".join(f"'{v}'" for v in OLD_ACTOR_TYPE)
    op.create_check_constraint(
        op.f("ck_audit_logs_actor_type"), "audit_logs", f"actor_type IN ({allowed})"
    )
    op.drop_column("guests", "version")
    op.drop_column("guests", "notes")
    op.drop_column("guests", "preferences")
