"""Realtime projection.

StayOps realtime is a *projection of committed database state*, never a separate
state store:

    API transaction commits
      → AFTER trigger (stayops_private.broadcast_change, migration 0002)
      → realtime.send(..., topic='org:<organization_id>', private=true)
      → Supabase Realtime (authorizes via RLS on realtime.messages)
      → browser subscription → TanStack Query invalidation → refetch via the API

Broadcast payloads carry ids and routing hints only (no guest PII); the client
refetches through the authorized API. See docs/realtime.md.
"""

from app.realtime.topics import organization_topic

__all__ = ["organization_topic"]
