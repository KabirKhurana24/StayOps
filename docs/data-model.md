# Data model

All timestamps are `timestamptz` and stored in UTC; sessions run with `TIME ZONE 'UTC'`.
Naive datetimes are rejected by the API (422). Each property stores its IANA timezone;
"today", nights and display times are always evaluated in the property's zone.

```
organizations ─┬─< organization_members >── profiles (= auth.users.id)
               ├─< properties ──< units
               ├─< guests
               ├─< reservations  (→ unit+property, → guest; all same org)
               ├─< events        (domain event log / outbox)
               └─< audit_logs    (→ events)
```

## Tables

### organizations
`id`, `name` (1–120), `slug` (unique, kebab-case check), `default_currency`, timestamps.

### profiles
`id` = Supabase `auth.users.id` (no cross-schema FK, for portability), `email`, `full_name`.
Provisioned/refreshed from verified JWT claims on each authenticated request.

### organization_members
`organization_id`, `user_id`, `role` ∈ {OWNER, ADMIN, OPERATOR, STAFF, VIEWER}, `joined_at`.
Unique `(organization_id, user_id)`; index on `user_id`.

### properties
`organization_id`, `name`, `address`, `timezone` (IANA, validated against tzdata),
`status` ∈ {ACTIVE, INACTIVE, ARCHIVED}, `default_check_in_time` (15:00),
`default_check_out_time` (11:00). Unique `(organization_id, id)` — target of tenant FKs.

### units
`organization_id`, `property_id`, `name`, `type` ∈ {APARTMENT, STUDIO, ROOM, HOUSE, VILLA,
OTHER}, `status` ∈ {ACTIVE, INACTIVE, MAINTENANCE}.
FK `(organization_id, property_id) → properties(organization_id, id)`;
unique `(property_id, name)` and `(organization_id, property_id, id)`.

### guests
`organization_id`, `first_name`, `last_name`, `email`, `phone`.
Unique index `(organization_id, lower(email)) WHERE email IS NOT NULL`.

### reservations
`organization_id`, `property_id`, `unit_id`, `guest_id`, `confirmation_code`,
`status` ∈ {PENDING, CONFIRMED, CHECKED_IN, CHECKED_OUT, CANCELLED}, `check_in`, `check_out`,
`adults` (1–50), `children` (0–50), `source` ∈ {DIRECT, AIRBNB, BOOKING_COM, VRBO, EXPEDIA,
OTHER}, `total_amount` numeric(12,2) ≥ 0, `currency` (ISO-4217 check), `notes`, `version`.

Constraints:
- `(organization_id, property_id, unit_id) → units(organization_id, property_id, id)` — the
  unit belongs to the property and both belong to the org.
- `(organization_id, guest_id) → guests(organization_id, id)`.
- `check_out > check_in`; unique `(organization_id, confirmation_code)`.
- **No double booking**: `EXCLUDE USING gist (unit_id WITH =, tstzrange(check_in, check_out, '[)') WITH &&) WHERE status IN ('PENDING','CONFIRMED','CHECKED_IN')`.
  Half-open ranges allow same-day turnovers (11:00 out / 15:00 in).
- `version` is SQLAlchemy's `version_id_col`: updates are `… WHERE version = :old`.

Status machine (API-enforced, mirrored in `packages/shared`):

```
PENDING ⇄ CONFIRMED → CHECKED_IN → CHECKED_OUT
   └──────┴──→ CANCELLED → (reinstate) CONFIRMED | PENDING
```

Indexes: `(organization_id, check_in)`, `(organization_id, check_out)`,
`(organization_id, status)`, `(unit_id, check_in)`, `(guest_id)`.

### events
`organization_id`, `event_type`, `aggregate_type`, `aggregate_id`, `occurred_at`,
`created_at`, `payload` jsonb, `source`, `idempotency_key`, `actor_id`, `schema_version`,
`status` ∈ {PENDING, PROCESSED, FAILED}, `attempts`, `processed_at`, `last_error`.
Unique `(organization_id, idempotency_key)`; indexes on `(organization_id, occurred_at)`,
`(organization_id, aggregate_type, aggregate_id)`, partial `created_at WHERE status='PENDING'`.

Event types: `organization.created`, `property.created`, `property.updated`,
`unit.created`, `guest.created`, `reservation.created`, `reservation.updated`,
`reservation.cancelled`. Guest contact details are deliberately not copied into payloads.

### audit_logs
`organization_id`, `actor_type` ∈ {USER, SYSTEM, INTEGRATION, AI_AGENT}, `actor_id`,
`actor_label`, `action`, `entity_type`, `entity_id`, `occurred_at`, `metadata` jsonb
(sanitized; updates store `{"changes": {field: {from, to}}}`), `request_id`, `event_id`.
Indexes on `(organization_id, occurred_at)` and `(organization_id, entity_type, entity_id)`.

## Enums

Stored as `VARCHAR(16)` + `CHECK` (named `ck_<table>_<enum>`). Adding a value is a
constraint swap in a migration — no `ALTER TYPE` locking. Python `StrEnum`s and the
TypeScript tuples in `packages/shared/src/enums.ts` must be kept in sync.

## Row Level Security

RLS is **enabled on every table with no policies**, and `anon`/`authenticated` have no
table grants: the Supabase Data API exposes nothing. The API connects as the table owner
and enforces tenancy in code + constraints. Because every row has `organization_id`, a
future direct-read path can add policies of the form
`USING (stayops_private.is_org_member(organization_id))` without schema changes.

## Migrations

- `0001_initial_schema` — tables, keys, checks, indexes.
- `0002_integrity_security_realtime` — btree_gist + exclusion constraint, RLS, grants,
  realtime functions/policy/triggers (Supabase only).

`tests/test_schema.py` runs Alembic's autogenerate comparison against the migrated schema
to guarantee the ORM models and migrations never drift.
