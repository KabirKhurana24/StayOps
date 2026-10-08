"""Phase 3 operations: tasks, cleanings, maintenance work orders, outbox worker.

* ``tasks`` — first-class operational work (turnover cleaning, inspection, …) with
  tenant-safe composite FKs to property/unit/reservation and to the assignee's
  *membership*. ``(organization_id, source_key)`` is unique: automation creates
  e.g. ``turnover:<reservation_id>`` at most once, however often an event is
  delivered.
* ``cleanings`` — 1:1 extension of a TURNOVER_CLEANING task (checklist, inspection).
* ``maintenance_work_orders`` — work orders; an active one with ``blocks_unit`` makes
  the unit unbookable (read by the existing availability service).
* ``reservations (organization_id, id)`` unique — target for the composite FKs.
* ``events`` — outbox statuses become PENDING / PROCESSING / COMPLETED / FAILED
  (PROCESSED rows are migrated to COMPLETED), plus retry schedule and worker lease.
* RLS deny-by-default and realtime broadcast triggers on the new tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen enum values at the time of this migration (VARCHAR + CHECK, see 0001).
TASK_STATUS = ("TODO", "IN_PROGRESS", "COMPLETED", "CANCELLED")
TASK_PRIORITY = ("LOW", "NORMAL", "HIGH", "URGENT")
TASK_TYPE = ("TURNOVER_CLEANING", "INSPECTION", "MAINTENANCE", "GUEST_REQUEST", "OTHER")
CLEANING_STATUS = (
    "CREATED",
    "ASSIGNED",
    "IN_PROGRESS",
    "COMPLETED",
    "INSPECTION_REQUIRED",
    "APPROVED",
    "FAILED",
)
MAINTENANCE_CATEGORY = (
    "HVAC",
    "PLUMBING",
    "ELECTRICAL",
    "APPLIANCE",
    "LOCK",
    "FURNITURE",
    "CLEANING",
    "OTHER",
)
MAINTENANCE_STATUS = ("OPEN", "ASSIGNED", "IN_PROGRESS", "BLOCKED", "RESOLVED", "CLOSED")
OLD_EVENT_STATUS = ("PENDING", "PROCESSED", "FAILED")
NEW_EVENT_STATUS = ("PENDING", "PROCESSING", "COMPLETED", "FAILED")

NEW_TABLES = ("tasks", "cleanings", "maintenance_work_orders")


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


def upgrade() -> None:
    # --- reservations: composite FK target ----------------------------------------
    op.create_unique_constraint(
        op.f("uq_reservations_organization_id_id"), "reservations", ["organization_id", "id"]
    )

    # --- tasks ----------------------------------------------------------------------
    op.create_table(
        "tasks",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(4000), nullable=True),
        sa.Column("status", sa.String(16), server_default="TODO", nullable=False),
        sa.Column("priority", sa.String(16), server_default="NORMAL", nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        _ts("due_at"),
        _ts("started_at"),
        _ts("completed_at"),
        _ts("cancelled_at"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_by_label", sa.String(320), nullable=True),
        sa.Column("source_key", sa.String(200), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        _org_fk("tasks"),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            name="fk_tasks_property_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_tasks_unit_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_tasks_reservation_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_tasks_assignee_member",
        ),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_tasks_organization_id_id")),
        sa.UniqueConstraint(
            "organization_id", "source_key", name=op.f("uq_tasks_organization_id_source_key")
        ),
        sa.CheckConstraint(
            "char_length(title) between 1 and 200", name=op.f("ck_tasks_title_length")
        ),
        _enum_check("tasks", "task_type", "task_type", TASK_TYPE),
        _enum_check("tasks", "status", "task_status", TASK_STATUS),
        _enum_check("tasks", "priority", "task_priority", TASK_PRIORITY),
    )
    op.create_index("ix_tasks_org_status_due_at", "tasks", ["organization_id", "status", "due_at"])
    op.create_index(
        "ix_tasks_org_assigned_to_status", "tasks", ["organization_id", "assigned_to", "status"]
    )
    op.create_index("ix_tasks_org_property_id", "tasks", ["organization_id", "property_id"])
    op.create_index("ix_tasks_org_unit_id", "tasks", ["organization_id", "unit_id"])
    op.create_index("ix_tasks_org_reservation_id", "tasks", ["organization_id", "reservation_id"])
    op.create_index("ix_tasks_org_type_status", "tasks", ["organization_id", "task_type", "status"])
    op.create_index("ix_tasks_org_completed_at", "tasks", ["organization_id", "completed_at"])

    # --- cleanings ------------------------------------------------------------------
    op.create_table(
        "cleanings",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), server_default="CREATED", nullable=False),
        sa.Column(
            "checklist",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("cleaner_notes", sa.String(2000), nullable=True),
        sa.Column("inspection_notes", sa.String(2000), nullable=True),
        _ts("started_at"),
        _ts("completed_at"),
        _ts("approved_at"),
        _ts("failed_at"),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("inspected_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cleanings")),
        _org_fk("cleanings"),
        sa.ForeignKeyConstraint(
            ["organization_id", "task_id"],
            ["tasks.organization_id", "tasks.id"],
            name="fk_cleanings_task_same_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("task_id", name=op.f("uq_cleanings_task_id")),
        _enum_check("cleanings", "status", "cleaning_status", CLEANING_STATUS),
    )
    op.create_index("ix_cleanings_org_status", "cleanings", ["organization_id", "status"])

    # --- maintenance work orders -------------------------------------------------------
    op.create_table(
        "maintenance_work_orders",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.Column("source_task_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(4000), nullable=True),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), server_default="NORMAL", nullable=False),
        sa.Column("status", sa.String(16), server_default="OPEN", nullable=False),
        sa.Column("blocks_unit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("reported_by", sa.Uuid(), nullable=True),
        sa.Column("reported_by_label", sa.String(320), nullable=True),
        sa.Column("blocked_reason", sa.String(1000), nullable=True),
        sa.Column("resolution_notes", sa.String(2000), nullable=True),
        _ts("started_at"),
        _ts("resolved_at"),
        _ts("closed_at"),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_work_orders")),
        _org_fk("maintenance_work_orders"),
        sa.ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_maintenance_work_orders_unit_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_maintenance_work_orders_reservation_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_task_id"],
            ["tasks.organization_id", "tasks.id"],
            name="fk_maintenance_work_orders_task_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_maintenance_work_orders_assignee_member",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_maintenance_work_orders_organization_id_id")
        ),
        sa.CheckConstraint(
            "char_length(title) between 1 and 200",
            name=op.f("ck_maintenance_work_orders_title_length"),
        ),
        _enum_check(
            "maintenance_work_orders", "category", "maintenance_category", MAINTENANCE_CATEGORY
        ),
        _enum_check("maintenance_work_orders", "priority", "maintenance_priority", TASK_PRIORITY),
        _enum_check("maintenance_work_orders", "status", "maintenance_status", MAINTENANCE_STATUS),
    )
    op.create_index(
        "ix_maintenance_work_orders_org_status",
        "maintenance_work_orders",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_maintenance_work_orders_org_unit_status",
        "maintenance_work_orders",
        ["organization_id", "unit_id", "status"],
    )
    op.create_index(
        "ix_maintenance_work_orders_org_assigned_to",
        "maintenance_work_orders",
        ["organization_id", "assigned_to"],
    )
    op.create_index(
        "ix_maintenance_work_orders_org_source_task",
        "maintenance_work_orders",
        ["organization_id", "source_task_id"],
    )
    op.create_index(
        "ix_maintenance_work_orders_org_created_at",
        "maintenance_work_orders",
        ["organization_id", "created_at"],
    )

    # --- events: outbox worker states ------------------------------------------------
    op.drop_constraint(op.f("ck_events_event_status"), "events", type_="check")
    op.execute("UPDATE events SET status = 'COMPLETED' WHERE status = 'PROCESSED'")
    allowed = ", ".join(f"'{v}'" for v in NEW_EVENT_STATUS)
    op.create_check_constraint(op.f("ck_events_event_status"), "events", f"status IN ({allowed})")
    op.add_column("events", _ts("next_attempt_at"))
    op.add_column("events", _ts("locked_at"))
    op.add_column("events", sa.Column("locked_by", sa.String(100), nullable=True))
    op.drop_index("ix_events_pending", table_name="events")
    op.create_index(
        "ix_events_unfinished",
        "events",
        ["created_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'PROCESSING', 'FAILED')"),
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
        for table in NEW_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS stayops_realtime_broadcast ON {table}")
            op.execute(
                f"""
                CREATE TRIGGER stayops_realtime_broadcast
                AFTER INSERT OR UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION stayops_private.broadcast_change()
                """
            )


def downgrade() -> None:
    op.drop_index("ix_events_unfinished", table_name="events")
    op.create_index(
        "ix_events_pending",
        "events",
        ["created_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_column("events", "locked_by")
    op.drop_column("events", "locked_at")
    op.drop_column("events", "next_attempt_at")
    op.drop_constraint(op.f("ck_events_event_status"), "events", type_="check")
    op.execute("UPDATE events SET status = 'PROCESSED' WHERE status = 'COMPLETED'")
    op.execute("UPDATE events SET status = 'PENDING' WHERE status = 'PROCESSING'")
    allowed = ", ".join(f"'{v}'" for v in OLD_EVENT_STATUS)
    op.create_check_constraint(op.f("ck_events_event_status"), "events", f"status IN ({allowed})")

    op.drop_table("maintenance_work_orders")
    op.drop_table("cleanings")
    op.drop_table("tasks")
    op.drop_constraint(op.f("uq_reservations_organization_id_id"), "reservations", type_="unique")
