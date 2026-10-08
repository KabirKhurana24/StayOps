from __future__ import annotations

import uuid

# Must match the topic regex in stayops_private.can_receive_topic (migration 0002).
ORG_TOPIC_PREFIX = "org:"

# Broadcast event names emitted by the trigger: "<table>.<insert|update|delete>".
BROADCAST_TABLES = (
    "reservations",
    "properties",
    "units",
    "guests",
    "events",
    "audit_logs",
    "tasks",
    "cleanings",
    "maintenance_work_orders",
)


def organization_topic(organization_id: uuid.UUID) -> str:
    return f"{ORG_TOPIC_PREFIX}{organization_id}"
