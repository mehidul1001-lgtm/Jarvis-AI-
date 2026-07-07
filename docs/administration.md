# Administrator Guide

## Becoming the administrator

The **first account registered** on a fresh installation is automatically
granted the `admin` role. Register immediately after deploying, before
sharing the URL.

## Managing users (Users page)

Admins see the **Users** entry in the sidebar.

- **Promote / demote**: change a user's role with the inline dropdown.
  - `user` — standard access (chat and memory from Phase 2 onward).
  - `manager` — reserved for business features arriving in Phases 2–3.
  - `admin` — full control including user management and audit logs.
- **Deactivate**: blocks login *and* immediately revokes all of the user's
  active sessions. Reactivate at any time; data is preserved.
- Safety rails: you cannot demote or deactivate your own account — ask
  another admin.

## Audit logs (Audit Logs page)

Every security-relevant event is recorded with timestamp, actor, IP and user
agent. Filter by action name (e.g. `auth.login_failed`).

Watch for:

- Bursts of `auth.login_failed` from one IP — someone is guessing passwords
  (rate limiting slows them to 10 attempts/min per IP automatically).
- Any `auth.refresh_token_reuse` — a refresh token was replayed. JARVIS
  already revoked all of that user's sessions; have the user change their
  password and investigate.
- Unexpected `user.updated_by_admin` entries — role changes are recorded
  with a before/after diff in the detail column.

## Sessions

- Users manage their own devices in **Settings → Active sessions**.
- Each user holds at most 10 concurrent sessions; the oldest is revoked
  automatically on the next login.
- A password change signs the user out everywhere, including the current
  device.

## Operational tasks

| Task                  | How                                                        |
|-----------------------|------------------------------------------------------------|
| Check service health  | `GET /api/v1/health` (dashboard shows it live)             |
| Apply migrations      | `alembic upgrade head` (automatic in the Docker image)     |
| Rotate JWT secret     | Set new `JARVIS_JWT_SECRET_KEY`, restart — all users must sign in again |
| Back up               | `pg_dump jarvis > backup.sql` on a schedule                |
| Logs                  | Backend emits JSON logs to stdout in production — ship them to your aggregator |

⚠️ **Do not rotate `JARVIS_ENCRYPTION_KEY`** once data is encrypted with it
(used from Phase 2 onward for stored credentials) without first decrypting
and re-encrypting — the old key cannot be recovered.
