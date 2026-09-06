# Development Guide

**Status:** Phase 2 — local development environment.
**Companion documents:** [architecture.md](architecture.md) · [data-model.md](data-model.md) · [roadmap.md](roadmap.md)

Every command below was executed on Windows 11 with PowerShell while writing this
document. Nothing here is aspirational; if a command is listed, it ran.

---

## 1. Prerequisites

| Tool | Version verified | Notes |
|---|---|---|
| Python | 3.10.9 | 3.10 is the floor. Nothing in the project requires 3.11+. |
| Node.js | 24.15.0 | |
| npm | 11.12.1 | |
| Docker Desktop | 29.4.3 (Compose 5.1.4) | Must be **running**, not merely installed. |
| Git | 2.54.0 | |

Check them:

```powershell
python --version; node --version; npm --version; docker --version; docker compose version
```

Docker Desktop needs its daemon running. `docker info` fails with
`failed to connect to the docker API at npipe:...` when the desktop app has not
been started — start Docker Desktop and wait ~30-90 seconds.

---

## 2. Clone and set up

```powershell
git clone <repository-url> ai-cv-screener
cd ai-cv-screener
```

The fastest path from here is the task script:

```powershell
.\tasks.ps1 install
Copy-Item .env.example .env
.\tasks.ps1 db-up
.\tasks.ps1 migrate
.\tasks.ps1 test
```

Everything that script does is spelled out below, so you never have to trust it.

---

## 3. Python environment

```powershell
python -m venv backend\.venv
```

The virtual environment lives at `backend\.venv` and is git-ignored. This guide
calls the interpreter by its full path rather than activating the environment —
that keeps every command copy-pasteable and avoids PowerShell's execution-policy
prompt for `Activate.ps1`. If you prefer to activate it:

```powershell
backend\.venv\Scripts\Activate.ps1     # PowerShell
source backend/.venv/bin/activate      # bash / macOS / Linux
```

---

## 4. Install backend dependencies

```powershell
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -r backend\requirements-dev.txt
```

`requirements.txt` declares version *ranges*; `requirements.lock.txt` records the
exact versions this project was verified against. To reproduce that exact set:

```powershell
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
```

Versions verified: FastAPI 0.141.1, Uvicorn 0.52.4, Pydantic 2.13.5,
pydantic-settings 2.15.0, SQLAlchemy 2.0.52, Alembic 1.19.2, psycopg 3.3.5,
pytest 8.4.2, httpx 0.28.1, ruff 0.16.6.

---

## 5. Start PostgreSQL

```powershell
docker compose up -d --wait
```

`--wait` blocks until the container's health check passes, so the next command
never races a database that is still starting.

Check it:

```powershell
docker compose ps
```

Plain PostgreSQL 16, no pgvector — Phase 1 evaluated vector search and deferred
it (see [data-model.md §10](data-model.md#10-pgvector-the-deferred-path)).

If port 5432 is already taken by a local PostgreSQL install, change the host side
of the port mapping in `docker-compose.yml` and update `DATABASE_URL` to match.

---

## 6. Configure `.env`

```powershell
Copy-Item .env.example .env      # PowerShell
cp .env.example .env             # bash
```

`.env` is git-ignored. The defaults match `docker-compose.yml` and work as-is.

**The one value that must be right is `DATABASE_URL`**, and it needs the
`+psycopg` driver suffix:

```
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ai_cv_screener
```

A bare `postgresql://` URL makes SQLAlchemy look for psycopg **2**, which is not
installed, and fails with `ModuleNotFoundError: No module named 'psycopg2'`.

The backend fails fast when configuration is missing. With no `.env` present:

```
app.core.config.ConfigurationError: Invalid application configuration:
  - DATABASE_URL: required environment variable is not set

Copy .env.example to .env and fill in the values. Expected at: ...\.env
```

`DEMO_MODE=true` is the default and means no API key is needed. Setting
`DEMO_MODE=false` without `ANTHROPIC_API_KEY` is also a startup failure, by
design — live mode never silently falls back to fixtures.

---

## 7. Run migrations

```powershell
cd backend
..\backend\.venv\Scripts\python.exe -m alembic upgrade head
cd ..
```

or simply `.\tasks.ps1 migrate`.

Alembic must run from `backend\`. The database URL comes from the application's
own settings, **not** from `alembic.ini` — no credential is written to a tracked
file, and migrations cannot target a different database than the app reads.

Useful commands:

```powershell
python -m alembic current                       # which revision is applied
python -m alembic check                          # do the models match the migrations?
python -m alembic downgrade base                 # tear the schema back down
python -m alembic revision --autogenerate -m "add x"   # new migration from model changes
```

`alembic check` is the one to run after touching a model: it reports
`No new upgrade operations detected.` when models and migrations agree.

**The ORM models are the single source of truth.** `Base.metadata` is what
autogenerate diffs against the live database. Do not hand-write DDL that the
models do not describe.

> Note on enums: the initial migration was hand-corrected after autogenerate.
> Alembic renders metadata-bound enums as `sa.Enum(..., metadata=MetaData())`,
> which does not import, emits `CREATE TYPE` twice for the three enums used by
> two columns each, and never drops the types on downgrade. The migration
> creates all 14 types explicitly and drops them explicitly; the reasoning is
> written at the top of the file. Expect to make the same correction if a future
> migration introduces a new enum.

---

## 8. Run the backend

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

or `.\tasks.ps1 dev-backend`.

| URL | Purpose |
|---|---|
| <http://localhost:8000/health> | Liveness. Touches nothing external. |
| <http://localhost:8000/health/db> | Database reachability. 503 when PostgreSQL is down. |
| <http://localhost:8000/docs> | Interactive API documentation. |

The two health endpoints are deliberately separate: a database outage must not
make the process look dead to a supervisor.

---

## 9. Run the frontend

```powershell
cd frontend
npm run dev
```

or `.\tasks.ps1 dev-frontend`. Then open <http://localhost:5173>.

The two servers are long-running, so run them in **separate terminals**.

`frontend\.env.example` documents `VITE_API_BASE_URL` (default
`http://localhost:8000`). Copy it to `frontend\.env` only if you need to change
it. Everything in a `VITE_*` variable is compiled into the browser bundle and is
therefore public — never put a secret there.

---

## 10. Run backend tests

```powershell
cd backend
.venv\Scripts\python.exe -m pytest
```

or `.\tasks.ps1 test-backend`. Expected: **32 passed**.

Tests are split by what they need:

- Most run **offline**. Configuration validation, health endpoints and the
  schema assertions need no database.
- Tests marked `requires_db` are **skipped with a stated reason** when
  PostgreSQL is unreachable, naming the connection string it tried. They are
  never silently passed.

Run only the offline set:

```powershell
.venv\Scripts\python.exe -m pytest -m "not requires_db"
```

---

## 11. Run frontend tests

```powershell
cd frontend
npm test
```

or `.\tasks.ps1 test-frontend`. Expected: **15 passed** across 3 files.
`npm run test:watch` for watch mode.

---

## 12. Linting and formatting

```powershell
# backend — ruff is both the linter and the formatter
cd backend
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .

# frontend
cd frontend
npm run lint
npm run format:check
```

`.\tasks.ps1 lint` runs all four. `.\tasks.ps1 format` applies fixes
(`ruff format` + `ruff check --fix` + `prettier --write`).

---

## 13. Stop the development environment

```powershell
# Ctrl+C in each dev-server terminal, then:
docker compose down          # stop PostgreSQL, keep the data volume
docker compose down -v       # stop PostgreSQL and DELETE all data
```

`.\tasks.ps1 db-down` and `.\tasks.ps1 db-reset` respectively.

---

## 14. Task script reference

`tasks.ps1` is a thin PowerShell wrapper, not a build system — every task is one
or two of the commands above.

| Command | Does |
|---|---|
| `.\tasks.ps1 install` | venv + backend deps + `npm install` |
| `.\tasks.ps1 db-up` / `db-down` / `db-reset` | PostgreSQL lifecycle |
| `.\tasks.ps1 migrate` | `alembic upgrade head` |
| `.\tasks.ps1 migration "message"` | autogenerate a revision |
| `.\tasks.ps1 dev-backend` / `dev-frontend` | run a server |
| `.\tasks.ps1 test` / `test-backend` / `test-frontend` | run tests |
| `.\tasks.ps1 lint` / `format` | lint or format both halves |
| `.\tasks.ps1` | print this list |

If PowerShell refuses to run it, either allow local scripts for the session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

or just use the underlying commands — nothing depends on the script.

---

## 15. Troubleshooting

**`ModuleNotFoundError: No module named 'psycopg2'`**
`DATABASE_URL` is missing the driver suffix. Use `postgresql+psycopg://`.

**`ConfigurationError: DATABASE_URL: required environment variable is not set`**
No `.env` at the repository root. `Copy-Item .env.example .env`.

**`failed to connect to the docker API at npipe:...`**
Docker Desktop is not running. Start it and wait for the whale icon to settle.

**`Error: Port 5173 is already in use`**
A Vite server is already running. `vite.config.ts` sets `strictPort: true`, so it
fails loudly rather than silently moving to another port.

**Frontend reachable at `localhost:5173` but not `127.0.0.1:5173`**
Vite binds to `localhost`, which on this machine resolves to IPv6 `::1` only.
Use `localhost` in the browser and in any scripted health probe.

**`/health/db` returns 503**
PostgreSQL is not running or `DATABASE_URL` is wrong. `docker compose ps` to
check. The response deliberately contains only the exception class name — never
the driver message, which embeds the connection URL and its password.

**Backend tests all skip with "PostgreSQL not reachable"**
Expected when the database is down. `docker compose up -d --wait`, then re-run.

---

## 16. What is not set up yet

Deliberately absent in Phase 2, arriving in the phase named:

- CI (Phase 3) — no automated checks run on push yet.
- LLM client and prompts (Phase 4) — `app/llm/` does not exist; `app/services/`
  is an empty package.
- API routes beyond health (Phases 4-10).
- Authentication, rate limiting, structured request logging (Phase 15).
- Frontend routing and the screening UI (Phase 11) — the current page is a shell
  that reports backend connectivity and nothing more.
