# Developer Guide

## Workflow

1. Create a feature branch.
2. Backend changes: keep the layering (api → services → models); services
   never import from `app.api`.
3. Schema changes: edit models, then
   `alembic revision --autogenerate -m "…"` and review the generated script —
   never edit applied migrations.
4. Add or update tests next to the feature (`backend/tests`).
5. Before pushing:
   ```bash
   cd backend && .venv/bin/python -m pytest && .venv/bin/ruff check app tests
   cd ../frontend && npm run build
   ```

## Backend conventions

- **Type hints everywhere**; SQLAlchemy models use `Mapped[]` typing.
- **Domain errors**, not `HTTPException`, inside services — raise
  `JarvisError` subclasses from `app/core/exceptions.py`; handlers translate
  them to the response envelope.
- **Transactions**: `get_db_session` commits on success and rolls back on any
  exception. If a write must survive an error response (audit trails,
  security revocations), call `await self.db.commit()` explicitly before
  raising — see `AuthService.refresh` for the pattern and the reasoning.
- **Never store plaintext secrets**: passwords → bcrypt; refresh tokens →
  SHA-256 digest; third-party credentials (future) → `encrypt_value()`.
- **Audit everything security-relevant** through `AuditService.record`.
- **Settings**: add new configuration to `Settings` with a sensible dev
  default, document it in `.env.example`, and hard-fail in production if it
  is a secret.

## Frontend conventions

- Strict TypeScript (`npm run lint` runs `tsc --noEmit`); no `any`.
- All server communication goes through `src/api/client.ts` — it owns token
  storage, automatic refresh (single-flight), and error normalization into
  `ApiError`. Add typed endpoint wrappers in `src/api/endpoints.ts`.
- Pages live in `src/pages`, shared chrome in `src/components/layout`,
  primitives in `src/components/ui`.
- Style with Tailwind utilities; shared patterns (`.card`, `.input`,
  `.btn-*`) are defined in `src/index.css`. Always provide `dark:` variants.
- Route guards: wrap new pages in `RequireAuth` (and `RequireAdmin` where
  appropriate) in `App.tsx`, and add navigation in
  `components/layout/Sidebar.tsx` (set `adminOnly` when relevant).

## Testing strategy

| Level        | Location                             | Runs against                    |
|--------------|--------------------------------------|---------------------------------|
| Unit         | `tests/test_security_units.py`       | Pure functions (no DB)          |
| API/integration | `tests/test_auth.py`, `test_users.py`, `test_health.py` | Real PostgreSQL (`jarvis_test`) |
| WebSocket    | `tests/test_websocket.py`            | Full app incl. lifespan         |

The suite truncates tables between tests, so tests are order-independent.
Fixtures `admin_tokens` / `user_tokens` give you authenticated clients.

## Phase log

### Phase 1 — Foundation (complete)

- Modular FastAPI backend (config, async SQLAlchemy, middleware stack,
  exception envelope, JSON logging).
- PostgreSQL schema + Alembic: `users`, `user_sessions`, `audit_logs`.
- Authentication: JWT (15 min) bound to server-side sessions, rotating
  refresh tokens with reuse detection, RBAC (admin/manager/user), session
  cap, device management, password policy + change flow.
- Security: bcrypt, Fernet field encryption helper, per-IP rate limiting
  (stricter on auth), security headers, strict CORS, audit trail.
- Realtime: authenticated `/ws` channel with per-user connection manager.
- Frontend: React 18 + strict TS + Tailwind dashboard shell — auth flow with
  auto-refresh, dark/light themes, responsive layout, dashboard with live
  system health, functional admin Users + Audit Logs pages, settings with
  profile/password/session management, placeholders for Phases 2–5.
- Tests: 28 backend tests (unit, API, WebSocket) against real PostgreSQL;
  ruff clean; strict tsc build; Playwright smoke of the real stack.

Fixed during the phase: request-scoped rollback was silently discarding
security writes (session mass-revocation and audit entries) raised alongside
401 responses — now committed explicitly before raising.

### Next: Phase 2 — AI brain

Claude-powered reasoning engine, long-term memory schema
(conversations, preferences, products, suppliers, workflows, business rules,
documents), chat API with WebSocket streaming, planning engine with
self-check before execution, memory explorer UI.
