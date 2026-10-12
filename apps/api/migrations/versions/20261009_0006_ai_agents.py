"""Phase 5 AI: agent runs, steps, action proposals; assistant toggle per thread.

* ``ai_runs`` — one agent invocation with provider/model, status, answer,
  evidence and token/cost accounting; a guest message triggers at most one run.
* ``ai_run_steps`` — persisted progress (``seq`` identity), the source of the live
  agent states in the UI.
* ``ai_action_proposals`` — consequential actions awaiting human approval.
* ``conversations.assistant_enabled`` — operators can pause the guest assistant.
* RLS deny-by-default; realtime triggers on the three AI tables, and the broadcast
  notice gains ``run_id`` (an id, no content) so the UI follows one run precisely.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT_TYPE = ("GUEST_AGENT", "OPERATOR_COPILOT")
RUN_STATUS = ("RUNNING", "COMPLETED", "AWAITING_APPROVAL", "ESCALATED", "FAILED")
STEP_KIND = ("CONTEXT", "LLM", "TOOL", "PROPOSAL", "GUARDRAIL", "ANSWER")
STEP_STATUS = ("STARTED", "COMPLETED", "FAILED", "BLOCKED")
PROPOSAL_STATUS = ("PENDING", "APPROVED", "EXECUTED", "REJECTED", "FAILED")

NEW_TABLES = ("ai_runs", "ai_run_steps", "ai_action_proposals")


def _enum_check(
    table: str, column: str, enum_name: str, values: tuple[str, ...]
) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(f"ck_{table}_{enum_name}"))


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organizations.id"],
        name=op.f(f"fk_{table}_organization_id_organizations"),
        ondelete="CASCADE",
    )


def _jsonb(name: str, default: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text(f"'{default}'::jsonb"),
        nullable=False,
    )


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


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
    """The migration-0002 notice function plus routing ids (see 0005)."""
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


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "assistant_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
    )

    op.create_table(
        "ai_runs",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("agent_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), server_default="RUNNING", nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("guest_id", sa.Uuid(), nullable=True),
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("trigger_message_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.String(500), nullable=True),
        sa.Column("answer", sa.String(4000), nullable=True),
        sa.Column("answer_message_id", sa.Uuid(), nullable=True),
        _jsonb("evidence", "[]"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        _ts("started_at", nullable=False),
        _ts("completed_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_runs")),
        _org_fk("ai_runs"),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            name="fk_ai_runs_conversation_same_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_ai_runs_reservation_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            name="fk_ai_runs_guest_same_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_ai_runs_organization_id_id")),
        sa.UniqueConstraint(
            "organization_id",
            "trigger_message_id",
            name=op.f("uq_ai_runs_organization_id_trigger_message_id"),
        ),
        _enum_check("ai_runs", "agent_type", "agent_type", AGENT_TYPE),
        _enum_check("ai_runs", "status", "run_status", RUN_STATUS),
    )
    op.create_index("ix_ai_runs_org_started_at", "ai_runs", ["organization_id", "started_at"])
    op.create_index(
        "ix_ai_runs_org_conversation", "ai_runs", ["organization_id", "conversation_id"]
    )
    op.create_index("ix_ai_runs_org_user", "ai_runs", ["organization_id", "user_id"])

    op.create_table(
        "ai_run_steps",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=True),
        _jsonb("input_summary", "{}"),
        _jsonb("result_summary", "{}"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_run_steps")),
        _org_fk("ai_run_steps"),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["ai_runs.organization_id", "ai_runs.id"],
            name="fk_ai_run_steps_run_same_org",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "char_length(label) between 1 and 200", name=op.f("ck_ai_run_steps_label_length")
        ),
        _enum_check("ai_run_steps", "kind", "step_kind", STEP_KIND),
        _enum_check("ai_run_steps", "status", "step_status", STEP_STATUS),
    )
    op.create_index("ix_ai_run_steps_run_seq", "ai_run_steps", ["run_id", "seq"])

    op.create_table(
        "ai_action_proposals",
        _uuid_pk(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("entity_label", sa.String(200), nullable=True),
        sa.Column("expected_effect", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_by_label", sa.String(320), nullable=True),
        _ts("decided_at"),
        _jsonb("result", "{}"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_action_proposals")),
        _org_fk("ai_action_proposals"),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["ai_runs.organization_id", "ai_runs.id"],
            name="fk_ai_action_proposals_run_same_org",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name=op.f("uq_ai_action_proposals_organization_id_id")
        ),
        sa.CheckConstraint(
            "char_length(title) between 1 and 200",
            name=op.f("ck_ai_action_proposals_title_length"),
        ),
        _enum_check("ai_action_proposals", "status", "proposal_status", PROPOSAL_STATUS),
    )
    op.create_index(
        "ix_ai_action_proposals_org_status", "ai_action_proposals", ["organization_id", "status"]
    )
    op.create_index(
        "ix_ai_action_proposals_run", "ai_action_proposals", ["organization_id", "run_id"]
    )

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for role in ("anon", "authenticated"):
        if _role_exists(role):
            for table in NEW_TABLES:
                op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")

    if _realtime_requested() and _broadcast_function_exists():
        op.execute(_broadcast_function("\n            'run_id', rec->>'run_id',"))
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
        op.execute(_broadcast_function(""))
        op.execute("REVOKE ALL ON FUNCTION stayops_private.broadcast_change() FROM PUBLIC")
    op.drop_table("ai_action_proposals")
    op.drop_table("ai_run_steps")
    op.drop_table("ai_runs")
    op.drop_column("conversations", "assistant_enabled")
