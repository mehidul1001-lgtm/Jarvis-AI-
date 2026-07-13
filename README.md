# JARVIS AI — Enterprise Business Assistant

JARVIS AI is a personal operating system for your business: an AI assistant that
understands natural language, plans tasks, uses tools, automates workflows,
analyzes data and maintains long-term memory.

The platform is built in phases. **Phase 1 (this state) delivers the
foundation**: project architecture, PostgreSQL database with migrations, a
hardened authentication system, the core FastAPI backend with WebSocket
support, and the responsive React dashboard shell with dark/light mode.

## Stack

| Layer     | Technology                                              |
|-----------|---------------------------------------------------------|
| Frontend  | React 18, TypeScript (strict), Tailwind CSS, Vite       |
| Backend   | Python 3.11, FastAPI, SQLAlchemy 2 (async), Alembic     |
| Database  | PostgreSQL 16                                           |
| Auth      | JWT access tokens + rotating refresh tokens, RBAC       |
| Realtime  | WebSocket channel (`/api/v1/ws`)                        |

## Repository layout

```
backend/            FastAPI application
  app/
    api/v1/         Versioned REST + WebSocket endpoints
    core/           Config, database, security, rate limiting, logging
    middleware/     Request context + security headers
    models/         SQLAlchemy models (users, sessions, audit logs)
    schemas/        Pydantic request/response models
    services/       Business logic (auth, users, audit)
  alembic/          Database migrations
  tests/            Pytest suite (unit + API integration + WebSocket)
frontend/           React dashboard
  src/
    api/            Typed API client with automatic token refresh
    components/     Layout + UI building blocks
    context/        Auth and theme providers
    hooks/          Realtime WebSocket hook
    pages/          Dashboard, auth, admin and placeholder pages
docs/               Architecture, installation, API and developer guides
docker-compose.yml  Full-stack deployment (db + backend + frontend)
```

## Quick start (development)

Prerequisites: Python 3.11+, Node 20+, PostgreSQL 16.

```bash
# 1. Database
createuser jarvis --pwprompt              # password: choose your own
createdb jarvis --owner jarvis
createdb jarvis_test --owner jarvis      # only needed to run the tests

# 2. Backend (http://localhost:8000, docs at /docs)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                      # adjust JARVIS_DATABASE_URL if needed
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload

# 3. Frontend (http://localhost:5173, proxies /api to the backend)
cd ../frontend
npm install
npm run dev
```

Open http://localhost:5173, register an account — **the first account becomes
the administrator**.

## Quick start (Docker)

```bash
export JARVIS_JWT_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export JARVIS_ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
docker compose up --build
```

Then open http://localhost:8080.

## Windows

Docker Desktop (WSL2 backend) runs the stack unmodified — `docker compose up
--build` works as-is in PowerShell or cmd, since `docker-compose.yml` uses a
named volume rather than a host bind mount. For a native (non-Docker)
backend setup, every dependency in `requirements.txt` ships a prebuilt
Windows wheel (`uvicorn[standard]`'s `uvloop` extra is correctly skipped on
`win32` via its own platform marker — Windows falls back to asyncio's
default event loop), and no part of the stack depends on a bash-only
script. Swap the venv paths in the steps above for their Windows
equivalents: `.venv\Scripts\pip.exe`, `.venv\Scripts\alembic.exe`,
`.venv\Scripts\uvicorn.exe` (or run `.venv\Scripts\activate` first).

## Testing

```bash
cd backend
.venv/bin/python -m pytest        # 28 tests: auth, RBAC, audit, websocket, security units
.venv/bin/ruff check app tests    # lint
cd ../frontend
npm run build                     # strict TypeScript compile + production build
```

## Documentation

- [Architecture](docs/architecture.md)
- [Installation & deployment](docs/installation.md)
- [API reference](docs/api.md)
- [Developer guide](docs/development.md)
- [Administrator guide](docs/administration.md)
- [Validating the Amazon integration against a real account](docs/amazon-validation.md)

## Amazon integration

Phase 3a connects the Amazon agent to a real Seller Central account via the
Selling Partner API (SP-API): orders, sales, FBA inventory, FBA inbound
shipments and financial events sync on a recurring schedule and become
queryable by the six agents and the REST API under `/api/v1/amazon/...`.

Amazon's 2023 SP-API migration removed the AWS SigV4/IAM-role signing
requirement — connecting a store only needs Login-with-Amazon (LWA)
credentials, obtained from Seller Central → **Apps and Services → Develop
apps**: an LWA client ID/secret and a refresh token authorized for your
seller account. `POST /api/v1/amazon/credentials` (admin/manager only)
stores them encrypted at rest with `JARVIS_ENCRYPTION_KEY`. Trigger a sync
with `POST /api/v1/amazon/credentials/{id}/sync` (`recurring: true` keeps
it running every `JARVIS_AMAZON_SYNC_INTERVAL_MINUTES`, default 30).

For a single-store deployment, setting `AMAZON_LWA_CLIENT_ID`,
`AMAZON_LWA_CLIENT_SECRET`, `AMAZON_LWA_REFRESH_TOKEN`, `AMAZON_SELLER_ID`
and `AMAZON_BOOTSTRAP_USER_EMAIL` connects (or updates) that account
automatically on every startup — no API call needed.

Everything above is covered by 91 automated tests against a scripted fake
API — before relying on it, validate it against your real seller account:
see [docs/amazon-validation.md](docs/amazon-validation.md).

## Roadmap

- **Phase 1 — Foundation** ✅ architecture, database, authentication, dashboard shell, core backend
- **Phase 2 — AI brain** ✅ Claude reasoning engine, long-term memory, chat, planning engine, workflow engine, all six agents
- **Phase 3a — Amazon SP-API integration** ✅ built, tested against a fake API; ⏳ pending validation against a real seller account (see above)
- **Phase 3b — Amazon Ads API**: campaigns, keywords, search terms, ACOS/ROAS/CTR/CPC/spend
- **Phase 3c — Enterprise dashboard**: live KPI cards, revenue charts, profit tracking, inventory heat map, PPC analytics
- **Phase 3d — AI automation & voice**: PPC optimization, restock forecasting, listing quality, daily reports, voice assistant
