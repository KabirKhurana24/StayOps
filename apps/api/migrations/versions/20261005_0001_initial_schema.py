"""Initial schema: tenancy, properties, units, guests, reservations, events, audit.

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the enum values at the time of this migration. Enums are stored as
# VARCHAR + CHECK (not native PG enums) so adding a value is a simple constraint swap.
ROLES = ("OWNER", "ADMIN", "OPERATOR", "STAFF", "VIEWER")
PROPERTY_STATUS = ("ACTIVE", "INACTIVE", "ARCHIVED")
UNIT_TYPE = ("APARTMENT", "STUDIO", "ROOM", "HOUSE", "VILLA", "OTHER")
UNIT_STATUS = ("ACTIVE", "INACTIVE", "MAINTENANCE")
RESERVATION_STATUS = ("PENDING", "CONFIRMED", "CHECKED_IN", "CHECKED_OUT", "CANCELLED")
RESERVATION_SOURCE = ("DIRECT", "AIRBNB", "BOOKING_COM", "VRBO", "EXPEDIA", "OTHER")
EVENT_STATUS = ("PENDING", "PROCESSED", "FAILED")
ACTOR_TYPE = ("USER", "SYSTEM", "INTEGRATION", "AI_AGENT")


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


def upgrade() -> None:
    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("default_currency", sa.String(3), server_default="USD", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_organizations_slug_format")
        ),
        sa.CheckConstraint(
            "char_length(name) between 1 and 120", name=op.f("ck_organizations_name_length")
        ),
    )

    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
    )

    op.create_table(
        "organization_members",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_members")),
        _org_fk("organization_members"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["profiles.id"],
            name=op.f("fk_organization_members_user_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name=op.f("uq_organization_members_organization_id_user_id"),
        ),
        _enum_check("organization_members", "role", "role", ROLES),
    )
    op.create_index("ix_organization_members_user_id", "organization_members", ["user_id"])

    op.create_table(
        "properties",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(500), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        sa.Column(
            "default_check_in_time", sa.Time(), server_default=sa.text("'15:00'"), nullable=False
        ),
        sa.Column(
            "default_check_out_time", sa.Time(), server_default=sa.text("'11:00'"), nullable=False
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_properties")),
        _org_fk("properties"),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_properties_organization_id_id")),
        sa.CheckConstraint(
            "char_length(name) between 1 and 200", name=op.f("ck_properties_name_length")
        ),
        _enum_check("properties", "status", "property_status", PROPERTY_STATUS),
    )
    op.create_index("ix_properties_organization_id_name", "properties", ["organization_id", "name"])

    op.create_table(
        "units",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(16), server_default="APARTMENT", nullable=False),
        sa.Column("status", sa.String(16), server_default="ACTIVE", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_units")),
        _org_fk("units"),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            name="fk_units_property_same_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "property_id",
            "id",
            name=op.f("uq_units_organization_id_property_id_id"),
        ),
        sa.UniqueConstraint("property_id", "name", name=op.f("uq_units_property_id_name")),
        sa.CheckConstraint(
            "char_length(name) between 1 and 100", name=op.f("ck_units_name_length")
        ),
        _enum_check("units", "type", "unit_type", UNIT_TYPE),
        _enum_check("units", "status", "unit_status", UNIT_STATUS),
    )
    op.create_index(
        "ix_units_organization_id_property_id", "units", ["organization_id", "property_id"]
    )

    op.create_table(
        "guests",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guests")),
        _org_fk("guests"),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_guests_organization_id_id")),
        sa.CheckConstraint(
            "char_length(first_name) between 1 and 100", name=op.f("ck_guests_first_name_length")
        ),
        sa.CheckConstraint(
            "char_length(last_name) between 1 and 100", name=op.f("ck_guests_last_name_length")
        ),
    )
    op.create_index(
        "uq_guests_organization_id_email_ci",
        "guests",
        ["organization_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_index(
        "ix_guests_organization_id_last_name", "guests", ["organization_id", "last_name"]
    )

    op.create_table(
        "reservations",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("confirmation_code", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("check_in", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_out", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adults", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("children", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
        _org_fk("reservations"),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_reservations_unit_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            name="fk_reservations_guest_same_org",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "confirmation_code",
            name=op.f("uq_reservations_organization_id_confirmation_code"),
        ),
        sa.CheckConstraint("check_out > check_in", name=op.f("ck_reservations_dates_ordered")),
        sa.CheckConstraint(
            "adults >= 1 AND adults <= 50", name=op.f("ck_reservations_adults_range")
        ),
        sa.CheckConstraint(
            "children >= 0 AND children <= 50", name=op.f("ck_reservations_children_range")
        ),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_reservations_amount_non_negative")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_reservations_currency_iso")),
        _enum_check("reservations", "status", "reservation_status", RESERVATION_STATUS),
        _enum_check("reservations", "source", "reservation_source", RESERVATION_SOURCE),
    )
    op.create_index(
        "ix_reservations_organization_id_check_in", "reservations", ["organization_id", "check_in"]
    )
    op.create_index(
        "ix_reservations_organization_id_check_out",
        "reservations",
        ["organization_id", "check_out"],
    )
    op.create_index(
        "ix_reservations_organization_id_status", "reservations", ["organization_id", "status"]
    )
    op.create_index("ix_reservations_unit_id_check_in", "reservations", ["unit_id", "check_in"])
    op.create_index("ix_reservations_guest_id", "reservations", ["guest_id"])

    op.create_table(
        "events",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        _org_fk("events"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name=op.f("uq_events_organization_id_idempotency_key"),
        ),
        _enum_check("events", "status", "event_status", EVENT_STATUS),
    )
    op.create_index(
        "ix_events_organization_id_occurred_at", "events", ["organization_id", "occurred_at"]
    )
    op.create_index(
        "ix_events_aggregate", "events", ["organization_id", "aggregate_type", "aggregate_id"]
    )
    op.create_index(
        "ix_events_pending",
        "events",
        ["created_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "audit_logs",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(320), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
        _org_fk("audit_logs"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_audit_logs_event_id_events"),
            ondelete="SET NULL",
        ),
        _enum_check("audit_logs", "actor_type", "actor_type", ACTOR_TYPE),
    )
    op.create_index(
        "ix_audit_logs_organization_id_occurred_at",
        "audit_logs",
        ["organization_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_logs_entity", "audit_logs", ["organization_id", "entity_type", "entity_id"]
    )


def downgrade() -> None:
    for table in (
        "audit_logs",
        "events",
        "reservations",
        "guests",
        "units",
        "properties",
        "organization_members",
        "profiles",
        "organizations",
    ):
        op.drop_table(table)
