# Guest experience (Phase 4)

Unified inbox, guest requests, property knowledge and the guest portal. Everything
here is deterministic — no AI is involved; Phase 5 builds on these domains.

```
RESERVATION → GUEST → CONVERSATION → GUEST REQUEST → TASK / WORK ORDER → RESOLUTION → GUEST
```

## Domains

| Module | Tables | What it owns |
|---|---|---|
| `inbox` | `conversations`, `messages` | Threads per stay and channel, ordered messages, per-side read state, internal notes, system notes |
| `guest_requests` | `guest_requests` | Request lifecycle, conversion to a task or work order, automatic resolution |
| `knowledge` | `property_knowledge` | Structured house guide per property/unit with visibility rules |
| `guest_portal` | `guest_portal_links` | Hashed per-stay access links and the guest-facing read models |
| `channels` | — | `ChannelAdapter` interface; `InAppAdapter` + not-configured provider adapters |
| `guests` | `guests` (+ `preferences`, `notes`, `version`) | Profile, preferences, private notes, stay/property history |

### Conversations and messages

* One conversation per **(reservation, channel)** (unique constraint); a guest writing
  into a resolved thread reopens it, so a stay's history stays in one place.
* `messages.seq` is a database identity: strict thread order even when two people
  write in the same millisecond.
* `messages.read_at` means *read by the other side*: the team for guest messages, the
  guest for team/assistant messages. Unread counts are shared by the team (a shared
  inbox) and the portal shows its own badge from the same column.
* `internal = true` marks team-only notes (operator notes, automation's operational
  notes). They never reach the guest API.
* A message bumps `conversations.last_message_at` with a single `UPDATE` (not an ORM
  flush on the versioned row), so concurrent replies never 409.
* Message **content is never copied into events or audit metadata** — events carry
  ids, sender type, message type and length.

### Guest requests → operations

```
OPEN ─► ACKNOWLEDGED ─► IN_PROGRESS ─► RESOLVED ─► CLOSED
  └──────────┴──────────────┴─► CANCELLED      └─► IN_PROGRESS (reopen)
```

* **Convert to task** creates a `GUEST_REQUEST` task with `source_key =
  guest_request:<id>` (at most one per request) and links it.
* **Convert to maintenance** creates a work order (client key `guest_request-<id>`,
  at least `HIGH` priority) and links it.
* `guest_requests.handlers` subscribe to `task.completed` and `maintenance.resolved`:
  the linked request is resolved and the guest gets a status message in the thread.
  A cancelled task leaves the request open and tells the team (not the guest).

### System notes in the thread

`inbox.handlers` post internal notes into *existing* conversations: check-in,
check-out, cancellation, date/unit changes, maintenance created/resolved on the
stay, and "unit cleaned and inspected — ready for arrival" for the **arriving**
guest's thread. Keys derive from the source event id, so retries never duplicate.

## Channel adapters

```python
class ChannelAdapter(ABC):
    status: CONNECTED | NOT_CONFIGURED
    assert_can_send()          # called *before* a message is persisted
    send_message(OutboundMessage) -> DeliveryResult
    receive_message(InboundMessage) -> InboundMessage
    normalize_event(payload) -> InboundMessage | None
    verify_webhook(headers, body) -> bool
```

`InAppAdapter` is connected: delivery *is* persistence (the portal reads the row).
`TwilioAdapter` (SMS), `WhatsAppAdapter`, `HostawayAdapter` (email via PMS),
`AirbnbAdapter`, `BookingAdapter` report `NOT_CONFIGURED`: replying on those
channels returns `422 channel_not_configured` and **nothing is stored**. Internal
notes work on any channel. `GET /api/v1/inbox/channels` shows the status.

## Property knowledge

`property → category (WIFI, CHECK_IN, CHECK_OUT, HOUSE_RULES, PARKING, DIRECTIONS,
AMENITIES, APPLIANCES, EMERGENCY, LOCAL_INFO, OTHER) → entry`, optionally per unit.

| Visibility | Who sees it |
|---|---|
| `PUBLIC` | any guest with a valid portal link |
| `STAY` | the guest, from the check-in day to the check-out day (property timezone) — door codes, Wi-Fi |
| `INTERNAL` | team only |

`knowledge.service.guest_entries()` is the only guest-facing read path; Phase 5's
guest agent will ground on it. Entries are archived, never deleted; audit records
which fields changed without copying content (which may contain codes).

## Guest portal

* Operators issue a link from the inbox context panel or reservation:
  `POST /api/v1/reservations/{id}/portal-link` → `{token, path: "/guest#token=…"}`.
  The raw token (256-bit, `secrets.token_urlsafe(32)`) is shown once; only its
  SHA-256 is stored. Issuing a new link revokes the previous one; links expire 7 days
  after check-out; cancelled stays and moved bookings invalidate the link.
* The token travels in the URL **fragment** (never sent to servers or in `Referer`);
  the portal moves it to `localStorage` and strips it from the address bar.
* The portal calls `/api/v1/guest/*` with `X-Guest-Token`. The API resolves the token
  to exactly one (organization, guest, reservation) — browser-supplied ids or
  `X-Organization-Id` are ignored. Guest contexts carry the `VIEWER` role plus a
  `GuestScope`; guest-facing services filter every query by that stay.
* Guest DTOs are separate from staff DTOs: no internal notes, amounts, staff names
  or emails (team replies are signed by the host organization), assignees, linked
  work, or other guests.
* Guests aren't Supabase users and must not join `org:<id>` realtime channels, so the
  portal polls its stay-scoped endpoints (messages every 4 s while visible).
* Guest actions are audited with `actor_type = GUEST`.

Routes: `/guest` (stay + quick actions), `/guest/stay` (house guide),
`/guest/messages`, `/guest/requests`. `proxy.ts` exempts exactly `/guest` and
`/guest/*` from the Supabase session gate (`/guests` stays protected).

## Permissions

| | OWNER/ADMIN | OPERATOR | STAFF | VIEWER | Guest (link) |
|---|---|---|---|---|---|
| Read inbox / requests | ✓ | ✓ | — | ✓ | own stay |
| Reply, notes, status, assign, mark read | ✓ | ✓ | — | — | reply in own thread |
| Manage requests, convert to work | ✓ | ✓ | — | — | raise / cancel own (while unhandled) |
| Edit property knowledge | ✓ | ✓ | — | — | read (visibility rules) |
| Issue / revoke portal links | ✓ | ✓ | — | — | — |

Conversations can only be assigned to members who can work the inbox.

## Realtime

`conversations`, `messages`, `guest_requests` and `property_knowledge` broadcast on
the private `org:<id>` topic (see [realtime.md](realtime.md)). Notices now include
`conversation_id` (an id, no PII) so an open thread refreshes precisely.

## Command Center

`guest_waiting` (unread guest message older than 15 min; critical for high-priority
threads) and `open_guest_request` (unacknowledged request older than 30 min, or any
high/urgent one) signals, plus `unread_conversations` / `open_guest_requests`
metrics — only for roles with `inbox:read`.
