# Architecture

## Overview

JARVIS AI is a modular client–server platform. Every capability added in later
phases (AI brain, agents, workflows) plugs into the foundations laid here:
versioned REST APIs, an authenticated realtime channel, a service layer over
PostgreSQL, and an auditable security model.

```
┌────────────────────────────────────────────────────────────┐
│                        Browser (SPA)                       │
│  React 18 + TypeScript + Tailwind                          │
│  AuthContext ── ThemeContext ── useRealtime (WebSocket)    │
└───────────────┬────────────────────────────┬───────────────┘
                │ REST /api/v1/*             │ WS /api/v1/ws
┌───────────────▼────────────────────────────▼───────────────┐
│                     FastAPI application                    │
│  Middleware: CORS → RateLimit → RequestContext/Headers     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐   │
│  │ auth API │  │ users API│  │ audit API│  │ system API│   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬─────┘   │
│  ┌────▼─────────────▼─────────────▼───────┐ ┌────▼─────┐   │
│  │            Service layer               │ │Connection│   │
│  │  AuthService · UserService · Audit     │ │ Manager  │   │
│  └────────────────────┬───────────────────┘ └──────────┘   │
│  SQLAlchemy 2 (async) │ asyncpg                            │
└───────────────────────┼────────────────────────────────────┘
                ┌───────▼────────┐
                │ PostgreSQL 16  │   Alembic migrations
                └────────────────┘
```

## Backend layering

Requests flow through four strict layers; lower layers never import from
higher ones.

1. **Middleware** (`app/middleware`, `app/core/rate_limit.py`)
   - `RequestContextMiddleware`: request IDs, timing, structured request logs,
     security headers (`X-Frame-Options`, `X-Content-Type-Options`, …).
   - `RateLimitMiddleware`: per-IP sliding-window budgets, with a stricter
     budget for `login`/`register`/`refresh`. Single-process today; the
     limiter interface is designed to be swapped for Redis when scaling out.
2. **API layer** (`app/api`) — request parsing, dependency-based auth
   (`get_current_user`, `require_roles`), response shaping. No business logic.
3. **Service layer** (`app/services`) — all business rules and transactional
   logic. Services receive an `AsyncSession` and never talk HTTP.
4. **Models** (`app/models`) — SQLAlchemy 2 typed models. UUID primary keys,
   timezone-aware timestamps.

### Error handling

Domain errors derive from `JarvisError` and map to a consistent envelope:

```json
{ "error": { "code": "authentication_failed", "message": "…" } }
```

Unhandled exceptions are logged with tracebacks and returned as opaque 500s —
internals never leak to clients.

### Configuration

`pydantic-settings` with the `JARVIS_` prefix (see `backend/.env.example`).
Secrets (`JWT_SECRET_KEY`, `ENCRYPTION_KEY`) auto-generate per boot in
development but **hard-fail in production** if unset.

## Security model

- **Passwords**: bcrypt (cost 12), strength policy enforced at the schema layer.
- **Access tokens**: 15-minute JWTs carrying `sub` (user), `role`, and `sid`
  (session). Every authenticated request verifies the *session* is still
  active, so logout / password change / deactivation revoke access within one
  request, not after token expiry.
- **Refresh tokens**: opaque 384-bit random strings; only SHA-256 digests are
  stored. Tokens rotate on every refresh. Reuse of a rotated token is treated
  as theft: **all** of that user's sessions are revoked and the event is
  audited.
- **RBAC**: `admin` / `manager` / `user` roles enforced by `require_roles`
  dependencies. The first registered account bootstraps as admin.
- **Session management**: per-device sessions with user-agent/IP metadata, a
  per-user cap (oldest revoked first), self-service listing and revocation.
- **Audit trail**: append-only `audit_logs` table (no FK — entries outlive
  users) recording auth events, admin actions and security incidents.
  Security-relevant writes on error paths are committed explicitly so they
  survive the request rollback.
- **Encryption at rest**: `encrypt_value`/`decrypt_value` (Fernet/AES-128-CBC
  + HMAC) for sensitive fields — used by later phases for API keys and
  credentials.
- **Transport hardening**: strict CORS allow-list, security headers on every
  response, OpenAPI docs disabled in production.

## Realtime channel

`GET /api/v1/ws?token=<access_token>` upgrades to a WebSocket after the token
and its backing session are validated. A `ConnectionManager` tracks sockets
per user and exposes `send_to_user` — Phase 2 (chat streaming) and Phase 4
(notifications) publish through it. The frontend `useRealtime` hook keeps the
connection alive with heartbeats and exponential-backoff reconnects.

## Database schema (Phase 1)

| Table           | Purpose                                                      |
|-----------------|--------------------------------------------------------------|
| `users`         | Accounts: email, bcrypt hash, role, active flag, last login  |
| `user_sessions` | Refresh-token sessions: token digest, expiry, revocation, device metadata |
| `audit_logs`    | Append-only security/audit events with JSONB detail          |

Migrations live in `backend/alembic/versions` and run automatically on
container boot (`alembic upgrade head`).

## Frontend architecture

- **API client** (`src/api/client.ts`): typed `fetch` wrapper; on 401 it
  performs a single-flight refresh (concurrent requests share one refresh
  call), retries once, and forces re-login if recovery fails.
- **State**: React context for auth and theme — no heavyweight state library
  needed at this stage.
- **Theming**: Tailwind `dark` class strategy; preference persisted to
  `localStorage`, defaulting to the OS color scheme.
- **Routing**: public (`/login`, `/register`), authenticated (dashboard,
  chat, agents, analytics, memory, settings) and admin-only (`/users`,
  `/logs`) route guards.
- **Layout**: responsive shell — fixed sidebar on desktop, slide-over drawer
  on mobile.

## Design decisions for later phases

- The v1 API is versioned (`/api/v1`) so the AI brain can evolve behind v2
  endpoints without breaking clients.
- Services take an `AsyncSession` argument rather than owning transactions,
  so future agent/tool code can compose several services in one transaction.
- The rate limiter and connection manager are in-process but interface-shaped
  for a Redis implementation when the platform scales horizontally.
