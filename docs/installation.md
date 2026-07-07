# Installation & Deployment Guide

## 1. Local development

### Prerequisites

- Python **3.11+**
- Node.js **20+** (22 recommended)
- PostgreSQL **16**

### Database

```bash
sudo -u postgres psql <<'SQL'
CREATE USER jarvis WITH PASSWORD 'change-me';
CREATE DATABASE jarvis OWNER jarvis;
CREATE DATABASE jarvis_test OWNER jarvis;  -- only needed for the test suite
SQL
```

### Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # requirements.txt for runtime only
cp .env.example .env
# edit .env: set JARVIS_DATABASE_URL to match the password you chose
.venv/bin/alembic upgrade head                  # apply migrations
.venv/bin/uvicorn app.main:app --reload         # http://localhost:8000, docs at /docs
```

### Frontend

```bash
cd frontend
npm install
npm run dev                                     # http://localhost:5173
```

The Vite dev server proxies `/api` (including the WebSocket) to
`http://localhost:8000`, so no CORS configuration is needed in development.

### First run

Open http://localhost:5173/register and create an account. The **first
account automatically becomes the administrator**; all later registrations
get the `user` role and can be promoted from the Users page.

## 2. Running the tests

```bash
cd backend
.venv/bin/python -m pytest          # needs the jarvis_test database
.venv/bin/ruff check app tests      # lint

cd ../frontend
npm run build                       # tsc strict check + production build
```

## 3. Docker deployment

`docker-compose.yml` runs the full stack: PostgreSQL, the API (migrations
apply automatically on boot) and the frontend behind nginx (which proxies
`/api` and the WebSocket to the backend).

```bash
# Generate real secrets — required whenever JARVIS_ENVIRONMENT=production
export JARVIS_JWT_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export JARVIS_ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export POSTGRES_PASSWORD='a-strong-database-password'

docker compose up --build -d
```

Open http://localhost:8080.

### Production checklist

- [ ] `JARVIS_ENVIRONMENT=production` (disables OpenAPI docs, enforces secrets)
- [ ] Unique `JARVIS_JWT_SECRET_KEY` and `JARVIS_ENCRYPTION_KEY` stored in a
      secret manager, not in files or shell history
- [ ] Strong `POSTGRES_PASSWORD`; database not exposed publicly
- [ ] TLS termination in front of nginx (e.g. a cloud load balancer or Caddy);
      WebSockets require `Upgrade` header passthrough
- [ ] `JARVIS_CORS_ORIGINS` set to your real origin(s) only
- [ ] Database backups scheduled (`pg_dump` or managed snapshots)
- [ ] Log aggregation for the backend's JSON logs

## 4. Configuration reference

All backend settings use the `JARVIS_` prefix. See `backend/.env.example`
for the complete annotated list. Key variables:

| Variable                          | Default            | Notes                              |
|-----------------------------------|--------------------|------------------------------------|
| `JARVIS_ENVIRONMENT`              | `development`      | `production` enforces secrets      |
| `JARVIS_DATABASE_URL`             | local dev database | asyncpg URL                        |
| `JARVIS_JWT_SECRET_KEY`           | auto (dev only)    | **required in production**         |
| `JARVIS_ENCRYPTION_KEY`           | auto (dev only)    | **required in production**, Fernet |
| `JARVIS_ACCESS_TOKEN_EXPIRE_MINUTES` | `15`            |                                    |
| `JARVIS_REFRESH_TOKEN_EXPIRE_DAYS`   | `7`             |                                    |
| `JARVIS_CORS_ORIGINS`             | localhost dev URLs | comma-separated                    |
| `JARVIS_RATE_LIMIT_*`             | see example file   | request budgets per IP             |

## 5. Troubleshooting

| Symptom                                   | Fix                                                            |
|-------------------------------------------|----------------------------------------------------------------|
| `connection refused` on startup           | PostgreSQL not running or wrong `JARVIS_DATABASE_URL`          |
| `JARVIS_JWT_SECRET_KEY must be set…`      | You are in production mode without secrets — generate them     |
| 401 loops in the browser                  | Clock skew or stale tokens — sign out/in; check server time    |
| WebSocket badge shows "disconnected"      | Proxy must forward `Upgrade` headers (see `frontend/nginx.conf`) |
| Tests fail with database errors           | Create the `jarvis_test` database (see section 1)              |
