#!/usr/bin/env python3
"""Validate the Phase 3a Amazon integration against a REAL Seller Central
account. Run this locally (this sandbox's network policy blocks Amazon's
domains, so it cannot run inside a hosted Claude session) after you have a
Login-with-Amazon client id/secret and refresh token - see
docs/amazon-validation.md for how to get those.

What it does, in order, matching the Phase 3a validation checklist:
  1. LWA authentication          - exchanges the refresh token for an access
                                    token; failure here means the app
                                    registration or refresh token is wrong.
  2. Marketplace verification    - calls the Sellers API to confirm these
                                    credentials are actually authorized for
                                    the marketplace you expect (US by
                                    default), instead of assuming.
  3. Orders + sales              - runs the real AmazonSyncService against
                                    your real account and prints order
                                    count/revenue for you to compare against
                                    Seller Central > Reports > Business
                                    Reports.
  4. FBA inventory               - same, compare against Seller Central >
                                    Inventory > Manage FBA Inventory.
  5. FBA inbound shipments       - compare against Seller Central >
                                    Inventory > Manage FBA Shipments.
  6. Financial events            - compare net proceeds against Seller
                                    Central > Reports > Payments.

Everything this script does goes through the SAME code paths the running
app uses (SPAPIClient, AmazonSyncService, AmazonDataService) - this is not
a separate hand-rolled diagnostic, it is Phase 3a itself, pointed at your
real account. Data is written to whichever database JARVIS_DATABASE_URL
points at (the credential and synced rows are real and stay there for the
app to use afterward - this is how you actually connect your store).

Required environment variables:
  JARVIS_DATABASE_URL       Postgres URL with migrations already applied
                             (alembic upgrade head)
  JARVIS_VALIDATE_USER_EMAIL  Email of an existing user in that database
                             (the credential is attached to this user)
  AMAZON_LWA_CLIENT_ID
  AMAZON_LWA_CLIENT_SECRET
  AMAZON_LWA_REFRESH_TOKEN
  AMAZON_SELLER_ID

Optional:
  AMAZON_REGION              NA | EU | FE            (default: NA)
  AMAZON_MARKETPLACE_ID      default: ATVPDKIKX0DER  (Amazon.com / US)
  AMAZON_CREDENTIAL_LABEL    default: "Live validation"
  JARVIS_LOG_LEVEL           DEBUG for full request/response tracing

Usage:
  cd backend
  source .venv/bin/activate  (or .venv\\Scripts\\activate on Windows)
  export JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:...@localhost:5432/jarvis
  export JARVIS_VALIDATE_USER_EMAIL=you@example.com
  export AMAZON_LWA_CLIENT_ID=amzn1.application-oa2-client...
  export AMAZON_LWA_CLIENT_SECRET=...
  export AMAZON_LWA_REFRESH_TOKEN=Atzr|...
  export AMAZON_SELLER_ID=A1B2C3D4E5
  python scripts/validate_amazon_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JARVIS_ENVIRONMENT", "development")
os.environ.setdefault("JARVIS_LOG_LEVEL", "INFO")

REQUIRED_ENV = [
    "JARVIS_DATABASE_URL",
    "JARVIS_VALIDATE_USER_EMAIL",
    "AMAZON_LWA_CLIENT_ID",
    "AMAZON_LWA_CLIENT_SECRET",
    "AMAZON_LWA_REFRESH_TOKEN",
    "AMAZON_SELLER_ID",
]


@dataclass
class CheckResult:
    name: str
    verified: bool
    detail: str
    sample: list[str] = field(default_factory=list)


async def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print("Missing required environment variables:")
        for name in missing:
            print(f"  - {name}")
        print(
            "\nSee the module docstring (python -m pydoc scripts.validate_amazon_live) "
            "or docs/amazon-validation.md for how to obtain each one."
        )
        return 2

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.database import db
    from app.core.logging import configure_logging
    from app.core.security import encrypt_value
    from app.integrations.amazon.client import SPAPIClient
    from app.integrations.amazon.exceptions import SPAPIError
    from app.models.amazon import AmazonCredential, AmazonRegion
    from app.models.user import User
    from app.services.amazon_data_service import AmazonDataService
    from app.services.amazon_sync_service import AmazonSyncService

    configure_logging()
    get_settings()
    db.init()

    region = os.environ.get("AMAZON_REGION", "NA")
    marketplace_id = os.environ.get("AMAZON_MARKETPLACE_ID", "ATVPDKIKX0DER")
    label = os.environ.get("AMAZON_CREDENTIAL_LABEL", "Live validation")
    results: list[CheckResult] = []

    # --- 0. Resolve the user this credential attaches to -----------------------------
    async with db.sessionmaker() as session:
        user = (
            await session.execute(
                select(User).where(User.email == os.environ["JARVIS_VALIDATE_USER_EMAIL"])
            )
        ).scalar_one_or_none()
        if user is None:
            print(f"No user found with email {os.environ['JARVIS_VALIDATE_USER_EMAIL']!r}.")
            print(
                "Register that account through the running app first (POST /api/v1/auth/register)."
            )
            return 2

    # --- 1. LWA authentication ---------------------------------------------------------
    print("=" * 78)
    print("1. LWA authentication")
    print("=" * 78)
    client = SPAPIClient(
        region=region,
        marketplace_id=marketplace_id,
        lwa_client_id=os.environ["AMAZON_LWA_CLIENT_ID"],
        lwa_client_secret=os.environ["AMAZON_LWA_CLIENT_SECRET"],
        lwa_refresh_token=os.environ["AMAZON_LWA_REFRESH_TOKEN"],
    )
    try:
        token = await client.verify_authentication()
        print(f"  OK - access token acquired ({token[:12]}...)")
        results.append(CheckResult("LWA authentication", True, "Access token acquired"))
    except Exception as exc:
        print(f"  FAILED: {exc}")
        results.append(CheckResult("LWA authentication", False, str(exc)))
        _print_report(results)
        await client.aclose()
        return 1

    # --- 2. Marketplace verification -----------------------------------------------------
    print("\n" + "=" * 78)
    print("2. Marketplace verification")
    print("=" * 78)
    try:
        participations = await client.list_marketplace_participations()
        ids = [p.get("marketplace", {}).get("id") for p in participations]
        print(f"  Authorized marketplaces: {ids}")
        if marketplace_id in ids:
            print(f"  OK - {marketplace_id} is in the authorized list")
            results.append(
                CheckResult(
                    "Marketplace verification", True, f"{marketplace_id} confirmed", [str(ids)]
                )
            )
        else:
            print(f"  WARNING - {marketplace_id} was NOT found in the authorized list above.")
            print(
                "  Check AMAZON_MARKETPLACE_ID / AMAZON_REGION, or re-authorize "
                "the app for this marketplace."
            )
            results.append(
                CheckResult(
                    "Marketplace verification",
                    False,
                    f"{marketplace_id} not in authorized list {ids}",
                )
            )
    except SPAPIError as exc:
        print(f"  FAILED: {exc}")
        results.append(CheckResult("Marketplace verification", False, str(exc)))
    await client.aclose()

    # --- 3-6. Full sync through the real AmazonSyncService --------------------------------
    async with db.sessionmaker() as session:
        existing = (
            await session.execute(
                select(AmazonCredential).where(
                    AmazonCredential.user_id == user.id, AmazonCredential.label == label
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            credential = existing
            print(f"\nReusing existing credential '{label}' ({credential.id})")
        else:
            credential = AmazonCredential(
                user_id=user.id,
                label=label,
                region=AmazonRegion(region),
                marketplace_id=marketplace_id,
                seller_id=os.environ["AMAZON_SELLER_ID"],
                lwa_client_id=os.environ["AMAZON_LWA_CLIENT_ID"],
                lwa_client_secret_encrypted=encrypt_value(os.environ["AMAZON_LWA_CLIENT_SECRET"]),
                lwa_refresh_token_encrypted=encrypt_value(os.environ["AMAZON_LWA_REFRESH_TOKEN"]),
            )
            session.add(credential)
            await session.commit()
            await session.refresh(credential)
            print(f"\nCreated credential '{label}' ({credential.id}) for {user.email}")

        print("\n" + "=" * 78)
        print(
            "3-6. Running the real sync pipeline "
            "(orders, listings, inventory, FBA shipments, financials)"
        )
        print("=" * 78)
        try:
            async with AmazonSyncService(session, credential) as service:
                sync_results = await service.sync_all()
            await session.commit()
            for r in sync_results:
                print(f"  {r.resource:18s} created={r.created:4d} updated={r.updated:4d}")
                results.append(
                    CheckResult(r.resource, True, f"created={r.created} updated={r.updated}")
                )
        except Exception as exc:
            print(f"  FAILED: {exc}")
            results.append(CheckResult("sync_all", False, str(exc)))
            await session.rollback()

        # --- Reconciliation printout -------------------------------------------------------
        print("\n" + "=" * 78)
        print("Reconciliation data - compare these against Seller Central")
        print("=" * 78)
        data = AmazonDataService(session)
        try:
            end = datetime.now(UTC)
            start = end - timedelta(days=30)
            sales = await data.sales_summary(user, credential.id, start=start, end=end)
            print("\nSales (last 30 days) - compare to Business Reports:")
            print(f"  Orders: {sales['order_count']}")
            print(f"  Revenue: {sales['total_revenue']} {sales['currency']}")
            print(f"  AOV: {sales['average_order_value']}")

            orders, total = await data.list_orders(user, credential.id, page=1, page_size=5)
            print(f"\nMost recent orders (showing up to 5 of {total}):")
            for o in orders:
                print(
                    f"  {o.amazon_order_id}  {o.purchase_date}  {o.order_status}  "
                    f"{o.order_total_amount} {o.order_total_currency}"
                )

            listings, listing_total = await data.list_listings(
                user, credential.id, page=1, page_size=10
            )
            print(
                f"\nListings - compare to Manage All Inventory "
                f"(showing up to 10 of {listing_total}):"
            )
            for li in listings:
                statuses = ",".join(li.status or [])
                print(f"  {li.seller_sku:20s} {li.asin or '-':12s} [{statuses}] {li.item_name}")

            inventory = await data.latest_inventory(user, credential.id)
            print(f"\nFBA inventory - compare to Manage FBA Inventory ({len(inventory)} SKU(s)):")
            for s in inventory[:10]:
                print(
                    f"  {s.seller_sku:20s} fulfillable={s.fulfillable_quantity:5d} "
                    f"inbound={s.inbound_shipped_quantity:5d}"
                )

            shipments, ship_total = await data.list_fba_shipments(
                user, credential.id, page=1, page_size=10
            )
            print(
                f"\nFBA inbound shipments - compare to Manage FBA Shipments "
                f"(showing up to 10 of {ship_total}):"
            )
            for s in shipments:
                print(f"  {s.shipment_id}  {s.shipment_name}  {s.shipment_status}")

            financials = await data.financial_summary(user, credential.id, start=start, end=end)
            print("\nFinancial events (last 30 days) - compare to Reports > Payments:")
            print(f"  Net amount: {financials['net_amount']} {financials['currency']}")
            for event_type, amount in financials["by_type"].items():
                print(f"    {event_type:25s} {amount}")
        except Exception as exc:
            print(f"  Reconciliation query failed: {exc}")

    print("\n" + "=" * 78)
    print("Verification report")
    print("=" * 78)
    _print_report(results)
    await db.dispose()
    return 0 if all(r.verified for r in results) else 1


def _print_report(results: list[CheckResult]) -> None:
    for r in results:
        status = "VERIFIED" if r.verified else "FAILED  "
        print(f"  [{status}] {r.name}: {r.detail}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
