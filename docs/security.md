# Security

## Authentication

- Supabase Auth issues access tokens; the web app keeps the session in **cookies** via
  `@supabase/ssr` (not localStorage). `proxy.ts` refreshes the cookie on navigation and
  uses `getClaims()` (signature-verified) for route gating.
- The API trusts nothing from the browser except a bearer JWT, verified in
  `app/auth/tokens.py`:
  - algorithm pinned per key source — `ES256`/`RS256`/`EdDSA` only via the project JWKS,
    `HS256` only via the configured legacy secret; `none` and anything else rejected;
  - `aud = authenticated`, `iss = <SUPABASE_URL>/auth/v1`, `exp` and `sub` required (30 s leeway);
  - tokens whose `role` isn't `authenticated` (e.g. anon/service keys) are rejected.
- Without auth configuration the API still boots; authenticated endpoints return 401.

## Authorization and tenant isolation

1. **Membership**: `get_tenant_context` resolves the organization from
   `organization_members`. `X-Organization-Id` is only a *selector* — naming an org you
   don't belong to returns 403.
2. **Permissions**: endpoints depend on permissions (`reservations:write`, …), mapped from
   roles in `app/auth/permissions.py`. VIEWER/STAFF are read-only in Phase 1; OPERATOR can
   manage guests, units and reservations; ADMIN/OWNER can also manage properties.
3. **Scoped queries**: every service query filters on `organization_id`. Another tenant's
   resource returns **404** (no existence oracle).
4. **Database constraints**: composite foreign keys make it impossible to attach a
   reservation to another tenant's unit or guest even if application code were wrong
   (covered by `test_database_rejects_cross_tenant_reservation`).
5. **Supabase Data API**: RLS on with no policies and grants revoked from
   `anon`/`authenticated` — the publishable key can't read tables.
6. **Realtime**: private topics + membership RLS, receive-only, no PII. See
   [realtime.md](realtime.md).

## Guest portal access

Guests have no account. A per-stay link token (256-bit, stored only as SHA-256,
revocable, expiring 7 days after check-out) is sent as `X-Guest-Token` and resolved
server-side to one organization + guest + reservation. Guest contexts carry the
`VIEWER` role and a stay scope; guest DTOs exclude internal notes, amounts, staff
identities and other guests. Member JWTs are not accepted on `/guest/*` and guest
tokens are not accepted anywhere else. The token travels in the URL fragment so it
never reaches server logs or `Referer`. See [guest-experience.md](guest-experience.md).

## Input validation and errors

- Pydantic validates every body/query (lengths, ranges, ISO currency, IANA timezone,
  aware datetimes, enum values, `extra="forbid"` on updates, explicit nulls rejected).
- LIKE searches escape `%`/`_`; all SQL is parameterized via SQLAlchemy.
- Errors use one envelope `{error: {code, message, request_id, details?}}`. Constraint
  violations map to 409/422 by SQLSTATE without leaking SQL; unexpected errors return a
  generic 500 and are logged server-side with the stack trace.

## Secrets

- `.env` is gitignored (`.env.*` too, except `.env.example`). No secret has a default.
- The secret/service key is used **only** by the seed script; the API never needs it.
- Only `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` and
  `NEXT_PUBLIC_API_URL` are exposed to the browser (explicitly, in `next.config.ts`).

## Logging hygiene

- Logs never include tokens, passwords, API keys or query strings. A structlog processor
  redacts values under credential-like keys (tested in `test_log_redaction_scrubs_credentials`).
- Audit metadata passes through the same redaction plus size caps.

## HTTP hardening

- CORS: exact origins from `API_CORS_ORIGINS` (wildcards rejected at startup),
  credentials disabled (bearer auth), limited methods/headers.
- Security headers on API and web: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, API `Cache-Control: no-store`;
  `X-Powered-By` disabled on web.
- Inbound `X-Request-ID` accepted only if it matches `^[A-Za-z0-9._:-]{8,128}$`.
- Open-redirect protection on `?next=` (login and auth callback).
- OpenAPI docs disabled when `APP_ENV=production`.

## Known gaps / next steps

- Rate limiting (planned with Redis) and account lockout policies (Supabase settings).
- Member invitation & role management endpoints (Phase 2), with audit.
- CSP header for the web app once third-party origins are finalized.
- Secret scanning in CI (e.g. gitleaks) when the repo gets CI.
