"""Phase 6 Autopilot: workflows, immutable versions, executions, steps, approvals;
operational alerts.

* ``automation_workflows`` — editable draft + status; ``current_version`` points at
  the published version that runs.
* ``automation_workflow_versions`` — immutable: a trigger rejects UPDATE (and DELETE
  outside a workflow's cascade), so an execution's pinned version can't change.
* ``automation_executions`` / ``automation_step_executions`` — durable run records;
  ``(workflow_id, dedupe_key)`` and ``(execution_id, step_id)`` are unique, which is
  what makes triggers and steps idempotent.
* ``automation_approvals`` — at most one PENDING approval per step (partial unique).
* ``operational_alerts`` — unique ``(organization_id, dedupe_key)``.
* RLS deny-by-default; realtime broadcast triggers on all six tables, and the notice
  gains ``workflow_id`` / ``execution_id`` (ids only — never execution context).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = (
    "automation_workflows",
    "automation_workflow_versions",
    "automation_executions",
    "automation_step_executions",
    "automation_approvals",
    "operational_alerts",
)


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
    """The migration-0002 notice function plus routing ids (see 0005, 0006)."""
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
            'aggregate_id', rec->>'aggregate_id',
            'conversation_id', rec->>'conversation_id',{extra_fields}
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


PHASE5_FIELDS = """
            'run_id', rec->>'run_id',"""
PHASE6_FIELDS = (
    PHASE5_FIELDS
    + """
            'workflow_id', rec->>'workflow_id',
            'execution_id', rec->>'execution_id',"""
)

IMMUTABLE_VERSIONS = """
    CREATE OR REPLACE FUNCTION automation_versions_immutable()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'automation_workflow_versions are immutable (publish a new version)'
          USING ERRCODE = 'restrict_violation';
      END IF;
      RETURN OLD;
    END;
    $$;
"""


def upgrade() -> None:
    op.create_table(
        "automation_workflows",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("trigger_type", sa.String(length=64), nullable=False),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("published_trigger_type", sa.String(length=64), nullable=True),
        sa.Column("has_unpublished_changes", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_by_label", sa.String(length=320), nullable=True),
        sa.Column("updated_by_label", sa.String(length=320), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'ARCHIVED')",
            name=op.f("ck_automation_workflows_workflow_status"),
        ),
        sa.CheckConstraint(
            "char_length(name) between 1 and 120", name=op.f("ck_automation_workflows_name_length")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_automation_workflows_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_workflows")),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_automation_workflows_organization_id_id")
        ),
    )
    op.create_index(
        "ix_automation_workflows_active_trigger",
        "automation_workflows",
        ["organization_id", "published_trigger_type"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_automation_workflows_org_status",
        "automation_workflows",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_table(
        "operational_alerts",
        sa.Column(
            "severity",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("entity_label", sa.String(length=200), nullable=True),
        sa.Column("property_id", sa.UUID(), nullable=True),
        sa.Column("workflow_id", sa.UUID(), nullable=True),
        sa.Column("execution_id", sa.UUID(), nullable=True),
        sa.Column("created_by_label", sa.String(length=320), nullable=True),
        sa.Column("acknowledged_by_label", sa.String(length=320), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'CRITICAL')",
            name=op.f("ck_operational_alerts_alert_severity"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')",
            name=op.f("ck_operational_alerts_alert_status"),
        ),
        sa.CheckConstraint(
            "char_length(title) between 1 and 200", name=op.f("ck_operational_alerts_title_length")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_operational_alerts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_alerts")),
        sa.UniqueConstraint(
            "organization_id",
            "dedupe_key",
            name=op.f("uq_operational_alerts_organization_id_dedupe_key"),
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_operational_alerts_organization_id_id")
        ),
    )
    op.create_index(
        "ix_operational_alerts_org_status",
        "operational_alerts",
        ["organization_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "automation_workflow_versions",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=64), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("published_by", sa.UUID(), nullable=True),
        sa.Column("published_by_label", sa.String(length=320), nullable=True),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_id"],
            ["automation_workflows.organization_id", "automation_workflows.id"],
            name="fk_automation_workflow_versions_workflow_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_automation_workflow_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_workflow_versions")),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_automation_workflow_versions_organization_id_id")
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "version",
            name=op.f("uq_automation_workflow_versions_workflow_id_version"),
        ),
    )
    op.create_table(
        "automation_executions",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("workflow_version_id", sa.UUID(), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=64), nullable=False),
        sa.Column("triggering_event_id", sa.UUID(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("entity_label", sa.String(length=200), nullable=True),
        sa.Column(
            "execution_context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.Column("actions_executed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_APPROVAL', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name=op.f("ck_automation_executions_execution_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_id"],
            ["automation_workflows.organization_id", "automation_workflows.id"],
            name="fk_automation_executions_workflow_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["automation_workflow_versions.organization_id", "automation_workflow_versions.id"],
            name="fk_automation_executions_version_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_automation_executions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_executions")),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_automation_executions_organization_id_id")
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "dedupe_key",
            name=op.f("uq_automation_executions_workflow_id_dedupe_key"),
        ),
    )
    op.create_index(
        "ix_automation_executions_org_started_at",
        "automation_executions",
        ["organization_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_automation_executions_org_status",
        "automation_executions",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_automation_executions_runnable",
        "automation_executions",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.create_index(
        "ix_automation_executions_workflow_started_at",
        "automation_executions",
        ["organization_id", "workflow_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "automation_step_executions",
        sa.Column("execution_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("step_type", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "input_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED', 'WAITING_APPROVAL', 'REJECTED')",
            name=op.f("ck_automation_step_executions_step_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "execution_id"],
            ["automation_executions.organization_id", "automation_executions.id"],
            name="fk_automation_step_executions_execution_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_automation_step_executions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_step_executions")),
        sa.UniqueConstraint(
            "execution_id",
            "step_id",
            name=op.f("uq_automation_step_executions_execution_id_step_id"),
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_automation_step_executions_organization_id_id")
        ),
    )
    op.create_index(
        "ix_automation_step_executions_execution_seq",
        "automation_step_executions",
        ["execution_id", "seq"],
        unique=False,
    )
    op.create_table(
        "automation_approvals",
        sa.Column("execution_id", sa.UUID(), nullable=False),
        sa.Column("step_execution_id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("expected_effect", sa.String(length=500), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("entity_label", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_by_label", sa.String(length=320), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'EXECUTED', 'REJECTED', 'FAILED', 'CANCELLED')",
            name=op.f("ck_automation_approvals_approval_status"),
        ),
        sa.CheckConstraint(
            "char_length(title) between 1 and 200",
            name=op.f("ck_automation_approvals_title_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "execution_id"],
            ["automation_executions.organization_id", "automation_executions.id"],
            name="fk_automation_approvals_execution_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "step_execution_id"],
            ["automation_step_executions.organization_id", "automation_step_executions.id"],
            name="fk_automation_approvals_step_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_automation_approvals_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_approvals")),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_automation_approvals_organization_id_id")
        ),
    )
    op.create_index(
        "ix_automation_approvals_org_status",
        "automation_approvals",
        ["organization_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_automation_approvals_pending_step",
        "automation_approvals",
        ["step_execution_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.execute(IMMUTABLE_VERSIONS)
    op.execute(
        """
        CREATE TRIGGER automation_versions_immutable
        BEFORE UPDATE ON automation_workflow_versions
        FOR EACH ROW EXECUTE FUNCTION automation_versions_immutable()
        """
    )

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for role in ("anon", "authenticated"):
        if _role_exists(role):
            for table in NEW_TABLES:
                op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")

    if _realtime_requested() and _broadcast_function_exists():
        op.execute(_broadcast_function(PHASE6_FIELDS))
        op.execute("REVOKE ALL ON FUNCTION stayops_private.broadcast_change() FROM PUBLIC")
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
    if _broadcast_function_exists():
        op.execute(_broadcast_function(PHASE5_FIELDS))
        op.execute("REVOKE ALL ON FUNCTION stayops_private.broadcast_change() FROM PUBLIC")
    op.drop_table("automation_approvals")
    op.drop_table("automation_step_executions")
    op.drop_table("automation_executions")
    op.drop_table("automation_workflow_versions")
    op.execute("DROP FUNCTION IF EXISTS automation_versions_immutable()")
    op.drop_table("operational_alerts")
    op.drop_table("automation_workflows")
