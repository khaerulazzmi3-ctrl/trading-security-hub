# trading-security-hub

Backend-first foundation for AI Ops Universe Trading Ops and Security Ops. This repository is being built as a real application, not a static dashboard mockup.

## Implemented

- FastAPI service with persistent SQLite storage.
- First-admin bootstrap guarded by a secret environment token and a 12-character minimum password.
- Scrypt password hashing, random opaque bearer tokens stored as hashes, session expiry and revocation.
- Server-side role checks for `blocked`, `ordinary`, `admin`, and `privileged`.
- Access request and approval workflow; only privileged governance can grant privileged access.
- Guardian incident records, high/critical incident session revocation when the subject matches an account, and audit trail.
- Paper-trading order records only. No broker/exchange order is transmitted.
- Health endpoint and API docs at `/docs`.

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Set environment variables before startup. Copy `.env.example` to `.env` and load it with your preferred secret manager or shell. Use a long random `ADMIN_BOOTSTRAP_TOKEN`; never commit real credentials.

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Create the first admin once, using the bootstrap token as the `X-Bootstrap-Token` header:

```bash
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -H "X-Bootstrap-Token: $ADMIN_BOOTSTRAP_TOKEN" \
  -d '{"username":"admin","password":"replace-this-with-a-long-password"}'
```

Then log in at `POST /auth/login`. Use the returned bearer token for authenticated endpoints.

## Production readiness

This is real backend code, but it is **not yet a production-hardened or publicly deployed service**. Before internet exposure, add TLS/reverse proxy, managed secrets, rate limiting and login lockout, database migrations/backups, structured monitoring/alert delivery, automated tests, dependency scanning, and independent security review. CORS should be restricted to the exact deployed frontend origin.

The developer's Admin role must be granted through verified credentials, not inferred from IP address alone. Privileged access must use a separate approval path and stronger authentication (MFA/WebAuthn) before production.

Live market data, IDX official disclosure ingestion, Telegram alerts, MT5, Stockbit, TRIV, and broker integrations are **not connected yet**. Do not insert API keys into source code or enable real-money execution from this foundation. The trading endpoint in this version only records paper orders and explicitly sends nothing to any broker.

## API surface

- `GET /health`
- `POST /auth/register` (one-time bootstrap)
- `POST /auth/login`, `POST /auth/logout`
- `GET /me`
- `POST /access-requests`, `GET /access-requests`, `POST /access-requests/{id}/decision`
- `POST /guardian/incidents`, `GET /guardian/incidents`, `POST /guardian/incidents/{id}/resolve`
- `GET /audit`
- `POST /paper/orders`, `GET /paper/orders`


## Agent Operations — first implementation

The Agent Task Engine is now included as a bounded backend foundation. It stores task records and event history in the same SQLite database and requires an authenticated session.

Endpoints:
- `POST /agent/tasks` — create a task (`plan_only` or `system_check`).
- `GET /agent/tasks` — list the current user's tasks.
- `GET /agent/tasks/{task_id}` — task details and event timeline.
- `GET /agent/tasks/{task_id}/events` — event timeline only.
- `POST /agent/tasks/{task_id}/run` — explicitly run a queued task.
- `POST /agent/tasks/{task_id}/cancel` — cancel a queued task.

Current behavior is intentionally limited: handlers run locally, record status/progress/events, and return a plan or basic system-check result. **This is not yet an autonomous AI agent**: it does not call Grok, fetch market data, run arbitrary tools, schedule background work, or execute trades. Tasks are executed synchronously by an explicit API request. The next implementation steps are automated tests, durable worker/lease handling, dashboard integration, and a separately configured, budget-limited AI provider adapter.
