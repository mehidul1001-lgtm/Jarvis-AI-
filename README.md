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

## Roadmap

- **Phase 1 — Foundation** ✅ architecture, database, authentication, dashboard shell, core backend
- **Phase 2 — AI brain**: Claude reasoning engine, long-term memory, chat, planning engine
- **Phase 3 — Agents**: Amazon, Finance, Developer, Operations (+ Product Research, Marketing)
- **Phase 4 — Automation**: browser automation, workflow engine, background jobs, notifications
- **Phase 5 — Analytics & hardening**: reports, optimization, security review, deployment
