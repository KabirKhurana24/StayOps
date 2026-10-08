"""Phase 2 read paths: property-scoped reservation lookups and activity categories.

* ``reservations (organization_id, property_id, check_in)`` serves the property
  calendar, property detail pages and property-filtered reservation lists.
* ``audit_logs (organization_id, entity_type, occurred_at)`` serves the activity
  timeline filtered by category, newest first.

Overlap protection is unchanged: the ``ex_reservations_no_overlap`` EXCLUDE
constraint (migration 0002) remains the database-level guarantee.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_reservations_org_property_check_in",
        "reservations",
        ["organization_id", "property_id", "check_in"],
    )
    op.create_index(
        "ix_audit_logs_org_entity_type_occurred_at",
        "audit_logs",
        ["organization_id", "entity_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_org_entity_type_occurred_at", table_name="audit_logs")
    op.drop_index("ix_reservations_org_property_check_in", table_name="reservations")
