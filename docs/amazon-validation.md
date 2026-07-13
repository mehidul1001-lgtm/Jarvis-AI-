# Validating Phase 3a against your real Amazon account

Phase 3a (the Selling Partner API integration) is built and covered by 84
automated tests, but every one of those tests runs against a scripted fake -
none of them have touched a real Amazon account. This guide walks through
getting real credentials and running the one live check that actually
proves it: `backend/scripts/validate_amazon_live.py`.

**Run this locally**, not in a hosted Claude session. Two reasons: your
Login-with-Amazon client secret and refresh token are long-lived production
credentials, and putting them in a hosted chat transcript is unnecessary
exposure when running one script on your own machine avoids it entirely;
and separately, sandboxed Claude sessions sit behind a network policy that
blocks arbitrary outbound hosts (including Amazon's), so the script
couldn't reach Amazon from there even if you wanted it to.

## 1. Register a private SP-API application

You only need to do this once.

1. Sign in to [Seller Central](https://sellercentral.amazon.com) with your
   seller account.
2. Go to **Apps and Services → Develop apps**. Accept the API/Developer
   agreement if this is your first time.
3. Click **Add new app client**. Give it a name (e.g. "Jarvis AI") and
   choose **Private** — this app is for your own seller account, not for
   distribution to other sellers, so it doesn't need Amazon's app review.
4. Select the API roles/permissions your app needs. For Phase 3a that's:
   **Orders**, **Inventory and Order Tracking**, **Amazon Fulfillment**,
   and **Finance and Accounts Management**. (Amazon's exact wording for
   these role names shifts slightly release to release — pick whichever
   options cover orders, FBA inventory/shipments, and financial events.)
5. Save. Amazon issues an **LWA Client ID**
   (`amzn1.application-oa2-client...`) and an **LWA Client Secret** —
   copy both somewhere safe (a password manager, not a chat window).

## 2. Get a refresh token

For a private app tied to your own seller account, Seller Central lets you
self-authorize without the full third-party OAuth redirect dance:

1. Back in **Develop apps**, find your app and use its **Authorize** /
   **View** action.
2. Confirm the authorization for your own seller account.
3. Amazon shows (or lets you generate) a **refresh token** — a long string
   starting with `Atzr|`. Copy it now; some flows only show it once.

If your Seller Central UI instead only offers the standard OAuth consent
URL (`https://sellercentral.amazon.com/apps/authorize/consent?application_id=...`),
that's fine too — completing that consent flow and exchanging the resulting
`spapi_oauth_code` at the LWA token endpoint (`grant_type=authorization_code`)
produces the same kind of refresh token. Either path ends with the same
`Atzr|...` value the validation script needs.

## 3. Find your Seller ID and marketplace

- **Seller ID**: Seller Central → **Settings → Account Info** → "Merchant
  Token" / "Seller ID" (starts with a letter, ~14 characters).
- **Marketplace**: for the US marketplace the ID is always
  `ATVPDKIKX0DER` and the region is `NA`. The validation script defaults
  to both, so you only need to override `AMAZON_MARKETPLACE_ID` /
  `AMAZON_REGION` for a different marketplace.

## 4. Run the validation script

Prerequisites: Python 3.11+, PostgreSQL running with migrations applied
(`alembic upgrade head` — see the main README's Quick Start), and at least
one registered user in that database (register one via
`POST /api/v1/auth/register` if you haven't already — the first account
becomes admin).

```bash
cd backend
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt   # if you haven't already

export JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:<password>@localhost:5432/jarvis
export JARVIS_VALIDATE_USER_EMAIL=you@example.com

export AMAZON_LWA_CLIENT_ID=amzn1.application-oa2-client....
export AMAZON_LWA_CLIENT_SECRET=...
export AMAZON_LWA_REFRESH_TOKEN=Atzr|...
export AMAZON_SELLER_ID=A1B2C3D4E5

python scripts/validate_amazon_live.py
```

It runs through the real code path end to end — `SPAPIClient` →
`AmazonSyncService` → `AmazonDataService` — the same objects the running
app uses, not a separate diagnostic shim. It:

1. Exchanges your refresh token for an LWA access token.
2. Calls the Sellers API to confirm these credentials are actually
   authorized for the marketplace you expect.
3. Runs a full sync (orders, FBA inventory, FBA inbound shipments,
   financial events) and writes the results into your real database — this
   *is* connecting the account, not a dry run.
4. Prints reconciliation tables: recent orders, 30-day sales, inventory per
   SKU, open shipments, and a financial-events breakdown by type.
5. Prints a final report marking each stage `VERIFIED` or `FAILED`.

## 5. Reconcile against Seller Central

The script can't know whether the numbers are *right* — only you can, by
eyeballing them against Seller Central:

| Script output | Compare against |
|---|---|
| Sales (order count, revenue, AOV) | Reports → Business Reports |
| Recent orders | Orders → Manage Orders |
| FBA inventory per SKU | Inventory → Manage FBA Inventory |
| FBA inbound shipments | Inventory → Manage FBA Shipments |
| Financial events by type | Reports → Payments |

If a number is off, check the sync logs first (`JARVIS_LOG_LEVEL=DEBUG` for
full request/response tracing) — the sync service now logs every page
fetched and every resource's created/updated counts.

## Troubleshooting

- **`LWA authentication` FAILED, "refused the refresh token"**: the
  refresh token was revoked, expired, or doesn't match this client
  id/secret pair. Re-authorize the app in Seller Central and get a fresh
  one.
- **`Marketplace verification` FAILED, ID not in the authorized list**:
  either `AMAZON_MARKETPLACE_ID`/`AMAZON_REGION` don't match where you
  actually sell, or the app wasn't authorized for that marketplace during
  step 1.
- **`sync_all` FAILED with a 403 on a specific resource**: the app
  registration is missing that role/permission (e.g. Finance). Go back to
  Develop apps, add the missing role, and re-authorize.
- **Connection/timeout errors**: confirm you're running this on a machine
  with normal internet access, not inside a network-restricted sandbox.
