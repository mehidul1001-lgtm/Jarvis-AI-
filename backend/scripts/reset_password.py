#!/usr/bin/env python3
"""Reset a user's password from the server side (forgotten-password rescue).

There is no email-based self-service reset yet (that's a later module), so
this is the operator path: run it where the app runs, next to the database.

Usage (interactive - prompts for the new password without echoing):

    python scripts/reset_password.py --email you@example.com

Under Docker Compose:

    docker compose exec -it backend python scripts/reset_password.py --email you@example.com

Forgot which email you registered with? List the accounts first:

    python scripts/reset_password.py --list

For non-interactive use (automation/tests) the new password may be
supplied via the JARVIS_RESET_PASSWORD environment variable instead of the
prompt; prefer the prompt for manual use so the password stays out of
shell history.

Also revokes all of the user's active sessions (access tokens are
session-bound, so this cuts off anyone holding the old credentials) and
writes an audit-trail entry.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JARVIS_ENVIRONMENT", "development")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a JARVIS user's password.")
    parser.add_argument("--email", help="Email of the account to reset")
    parser.add_argument("--list", action="store_true", help="List accounts and exit")
    args = parser.parse_args()

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.models.audit import AuditLog
    from app.models.session import UserSession
    from app.models.user import User

    settings = get_settings()
    engine = create_engine(settings.database_url.replace("postgresql+asyncpg://", "postgresql://"))

    with Session(engine) as db:
        if args.list:
            users = db.execute(select(User).order_by(User.created_at)).scalars().all()
            if not users:
                print("No accounts exist yet - register one through the app first.")
                return 1
            print(f"{'email':40s} {'role':8s} active")
            for user in users:
                print(f"{user.email:40s} {user.role.value:8s} {user.is_active}")
            return 0

        if not args.email:
            parser.error("--email is required (or use --list to see accounts)")

        user = db.execute(select(User).where(User.email == args.email)).scalar_one_or_none()
        if user is None:
            print(f"No account found for {args.email!r}. Run with --list to see accounts.")
            return 1

        new_password = os.environ.get("JARVIS_RESET_PASSWORD")
        if not new_password:
            new_password = getpass.getpass(f"New password for {user.email}: ")
            confirm = getpass.getpass("Confirm new password: ")
            if new_password != confirm:
                print("Passwords do not match; nothing changed.")
                return 1
        if len(new_password) < settings.password_min_length:
            print(
                f"Password must be at least {settings.password_min_length} characters; "
                "nothing changed."
            )
            return 1

        email = user.email
        user.password_hash = hash_password(new_password)

        revoked = 0
        sessions = (
            db.execute(select(UserSession).where(UserSession.user_id == user.id)).scalars().all()
        )
        for session in sessions:
            if session.revoked_at is None:
                session.revoked_at = datetime.now(UTC)
                revoked += 1

        db.add(
            AuditLog(
                user_id=user.id,
                action="auth.password_reset_cli",
                resource=f"user:{user.id}",
                detail={"sessions_revoked": revoked},
            )
        )
        db.commit()

    print(f"Password reset for {email}; {revoked} active session(s) revoked.")
    print("Log in through the app with the new password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
