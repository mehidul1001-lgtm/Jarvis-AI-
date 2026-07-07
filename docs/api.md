# API Reference (v1)

Base URL: `/api/v1`. Interactive OpenAPI docs are served at `/docs` outside
production.

## Conventions

- **Auth**: `Authorization: Bearer <access_token>` header.
- **Errors**: consistent envelope
  `{"error": {"code": "<machine_code>", "message": "<human message>"}}` with
  appropriate HTTP status (401, 403, 404, 409, 422, 429, 500). Request
  validation errors use FastAPI's standard 422 `detail` array.
- **Rate limits**: 120 requests/min per IP generally; 10/min per IP on
  `login`, `register` and `refresh`. Exceeding returns 429 with `Retry-After`.
- **Pagination**: list endpoints return
  `{"items": [...], "total": n, "page": p, "page_size": s}`.

## System

### `GET /health`
No auth. Returns service and database status.

```json
{ "status": "ok", "version": "0.1.0", "environment": "production", "database": "ok" }
```

### `WS /ws?token=<access_token>`
Authenticated WebSocket. Sends `{"type": "connected", "user_id": "…"}` on
connect; responds to `{"type": "ping"}` with `{"type": "pong"}`. Later phases
stream chat tokens and notifications over this channel. Invalid or revoked
tokens close the socket with code `1008`.

## Authentication

### `POST /auth/register` → 201
```json
{ "email": "a@b.com", "full_name": "Ada", "password": "Str0ngPass!x" }
```
Password policy: ≥10 chars, uppercase, lowercase, digit. The first account
ever registered receives the `admin` role. Returns the user object.

### `POST /auth/login` → 200
```json
{ "email": "a@b.com", "password": "…" }
```
Returns a token pair:
```json
{ "access_token": "…", "refresh_token": "…", "token_type": "bearer", "expires_in": 900 }
```

### `POST /auth/refresh` → 200
`{"refresh_token": "…"}` → new token pair. Refresh tokens are single-use
(rotation). Reusing an old one revokes **all** the user's sessions (theft
response) and returns 401.

### `POST /auth/logout` → 200
`{"refresh_token": "…"}` — revokes that session. Its access token stops
working immediately.

### `GET /auth/me` → 200
Current user profile.

### `PATCH /auth/me` → 200
`{"full_name": "New Name"}` — update own profile.

### `POST /auth/me/password` → 200
`{"current_password": "…", "new_password": "…"}` — changes the password and
revokes **every** session.

### `GET /auth/me/sessions` → 200
Active sessions (device management): id, IP, user agent, created/expires.

### `DELETE /auth/me/sessions/{session_id}` → 200
Revoke one of your own sessions.

## Users (admin only)

### `GET /users?page=&page_size=` → 200
Paginated user list.

### `GET /users/{user_id}` → 200
Single user.

### `PATCH /users/{user_id}` → 200
Any of `{"full_name": "…", "role": "admin|manager|user", "is_active": bool}`.
Deactivation revokes the target's sessions instantly. Admins cannot demote or
deactivate themselves (422).

## Audit (admin only)

### `GET /audit?page=&page_size=&action=&user_id=` → 200
Paginated, filterable audit trail. Recorded actions include:

| Action                    | Trigger                                    |
|---------------------------|--------------------------------------------|
| `auth.register`           | Account created                            |
| `auth.login`              | Successful login                           |
| `auth.login_failed`       | Wrong credentials (email kept in detail)   |
| `auth.login_blocked`      | Deactivated account attempted login        |
| `auth.refresh`            | Token pair rotated                         |
| `auth.refresh_token_reuse`| Rotated token replayed — all sessions revoked |
| `auth.logout`             | Session ended                              |
| `auth.session_revoked`    | Device session revoked from settings       |
| `user.password_changed`   | Password change                            |
| `user.updated_by_admin`   | Admin changed role/status/name (diff in detail) |

## Roles

| Role      | Capabilities                                              |
|-----------|-----------------------------------------------------------|
| `admin`   | Everything: user management, audit logs                   |
| `manager` | Reserved for business features in Phases 2–3              |
| `user`    | Own profile, sessions, and (from Phase 2) chat and memory |
