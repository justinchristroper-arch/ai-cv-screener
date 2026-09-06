# Development Guide

**Status:** Phase 3 — repository workflow and CI.
**Companion documents:** [architecture.md](architecture.md) · [data-model.md](data-model.md) · [roadmap.md](roadmap.md) · [CONTRIBUTING.md](../CONTRIBUTING.md) · [decisions/](decisions/README.md)

Every command below was executed — on Windows 11 with PowerShell for the local workflow, and additionally against a fresh, isolated PostgreSQL container for anything CI also runs — while writing this document. Nothing here is aspirational; if a command is listed, it ran and its real output is what's described.

---

## 1. Prerequisites

| Tool | Version verified | Notes |
|---|---|---|
| Python | 3.10.9 | 3.10 is the floor. Nothing in the project requires 3.11+. Pinned — see §3. |
| Node.js | 24.15.0 | Pinned — see §3. |
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

## 3. Language version pinning

Phase 2 identified a reproducibility gap: nothing enforced the Python or Node version a contributor actually used, so "works on my machine" could mean a genuinely different runtime. This is now pinned two ways per language — an exact version for local reproducibility, and a floor for the actual compatibility requirement — rather than one single number serving both purposes:

| Language | Exact pin (local dev) | Floor (compatibility) | Where |
|---|---|---|---|
| Python | `3.10.9` | `>=3.10` | [`backend/.python-version`](../backend/.python-version) (exact) · `requires-python` in [`backend/pyproject.toml`](../backend/pyproject.toml) (floor) |
| Node.js | `24.15.0` | `^22.13.0 \|\| ^24.0.0 \|\| >=26.0.0` | [`frontend/.nvmrc`](../frontend/.nvmrc) (exact) · `engines.node` in [`frontend/package.json`](../frontend/package.json) (floor) |

**Why two numbers per language, not one:** the exact pin is what a contributor's version manager switches to; the floor is the actual range this project has been checked against. `requires-python = ">=3.10"` was a deliberate Phase 2 choice — nothing in the code needs 3.11+, and pinning the packaging metadata to an exact patch version would reject a perfectly compatible 3.10.x or 3.12.x. The Node floor is **not** a guess: it's the intersection of what this project's own `devDependencies` actually declare (`vitest` requires `^22.12.0 || ^24.0.0 || >=26.0.0`; `eslint` requires `^20.19.0 || ^22.13.0 || >=24`; combining both — 22.12 alone fails eslint's `^22.13.0` floor — gives the range above), read directly from each package's own `package.json` rather than assumed.

**What "enforced" actually means for each mechanism:**

- **CI enforces both exactly**, reading `backend/.python-version` and `frontend/.nvmrc` directly via `actions/setup-python`'s and `actions/setup-node`'s `*-version-file` inputs (see [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) — the version file is the single source of truth; the number is never duplicated as a literal in the workflow.
- **`npm install` / `npm ci` enforce the Node floor locally**, and do so *strictly*: [`frontend/.npmrc`](../frontend/.npmrc) sets `engine-strict=true`, so an incompatible Node version fails the install outright (`npm error code EBADENGINE`) rather than silently succeeding and failing later in a way that looks like a project bug. Verified: temporarily setting `engines.node` to an impossible range (`>=99.0.0`) and running `npm install` produces exactly that error; reverting restores a clean install.
- **`.python-version` is a real, standard convention** — honored automatically by `pyenv` and `asdf` if you use one, and by `actions/setup-python` in CI — but is **not actively enforced on a machine with no version manager installed**. Plain `python -m venv` does not read it. If you don't use pyenv/asdf, checking `python --version` against the table above is on you locally; CI checks it either way.

Do not bump either pin without a concrete compatibility reason — see [`CONTRIBUTING.md`](../CONTRIBUTING.md#proposing-architectural-changes) if you have one.

---

## 4. Python environment

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

## 5. Install backend dependencies

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
pytest 8.4.2, httpx2 2.12.0, ruff 0.16.6.

> **Why `httpx2`, not `httpx`:** `starlette.testclient` (used via
> `fastapi.testclient.TestClient` in every backend test) tries
> `import httpx2 as httpx` first, and only falls back to the older `httpx`
> package — with a `DeprecationWarning: Using httpx with starlette.testclient
> is deprecated; install httpx2 instead` — when `httpx2` is absent. `httpx2` is
> a real, independently published package on PyPI (confirmed via
> `pip index versions httpx2`, currently 2.12.0), and installing it removes the
> warning outright rather than suppressing it. Plain `httpx` was removed
> entirely from this project (`requirements-dev.txt` lists `httpx2` only) —
> nothing here imports it directly, and its own transitive dependencies
> (`httpcore`, `certifi`) were confirmed orphaned (`pip show <pkg>` →
> `Required-by:` empty) once it was removed, so they're gone too. One warning
> remains after this fix, and it is **not** fixable from this project's side:
> `starlette/testclient.py:53` itself references a deprecated `anyio.abc.
> BlockingPortal` alias internally. `starlette==1.6.0` was, at the time this
> was checked, the latest version on PyPI — this is a currently-unresolved
> upstream issue, not a gap in this project's dependency pinning. Re-check on
> the next starlette upgrade.

---

## 6. Start PostgreSQL

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

## 7. Configure `.env`

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

## 8. Run migrations

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
>
> CI (§16) now exercises `upgrade → downgrade → upgrade` on every push against a
> fresh database specifically to catch a regression of this class before merge —
> a plain `upgrade head` alone would not have caught it the first time.

---

## 9. Run the backend

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

## 10. Run the frontend

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

## 11. Run backend tests

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
  never silently passed. CI always has a database available (§16), so all
  32 tests — the full suite, `requires_db` included — run there on every push.

Run only the offline set:

```powershell
.venv\Scripts\python.exe -m pytest -m "not requires_db"
```

---

## 12. Run frontend tests

```powershell
cd frontend
npm test
```

or `.\tasks.ps1 test-frontend`. Expected: **15 passed** across 3 files.
`npm run test:watch` for watch mode.

---

## 13. Linting and formatting

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

## 14. Documentation integrity check

```powershell
python scripts\check_docs.py          # check
python scripts\check_docs.py -v       # check, listing every link examined
```

or `.\tasks.ps1 check-docs`. Pure standard library — no dependency install
needed beyond Python itself, and it runs identically under PowerShell, bash,
and CI (see [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)'s `docs`
job).

It scans `README.md` and every `docs/**/*.md` file for Markdown links and
checks two things: that a relative link resolves to a file that actually
exists, and that a link fragment (`file.md#some-heading`) resolves to a
heading that actually exists in the target — reproducing GitHub's own
heading-to-anchor slug algorithm, so a link that checks out here is guaranteed
to work when GitHub renders it. It deliberately does not check external
(`http://`/`https://`) links — that needs a network call and is a different,
flakier kind of check.

Run it after adding or renaming a doc, or renaming a heading a link depends on.
Exit code is `0` when everything resolves, `1` otherwise; on failure it prints
every broken link with its file and line number.

---

## 15. Stop the development environment

```powershell
# Ctrl+C in each dev-server terminal, then:
docker compose down          # stop PostgreSQL, keep the data volume
docker compose down -v       # stop PostgreSQL and DELETE all data
```

`.\tasks.ps1 db-down` and `.\tasks.ps1 db-reset` respectively.

---

## 16. Continuous integration

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push
and pull request to `main`, as three independent jobs:

| Job | Runs | Database |
|---|---|---|
| `backend` | ruff check, ruff format --check, `alembic upgrade head`, `alembic downgrade base` + `alembic upgrade head` (round-trip), `alembic check`, `pytest -v` | A `postgres:16-alpine` service container — the full suite runs, `requires_db` tests included, nothing skipped |
| `frontend` | `npm ci`, eslint, prettier --check, vitest, production build | — |
| `docs` | `scripts/check_docs.py -v` | — |

**Why CI runs a real PostgreSQL service, not just the offline test subset:**
roughly a third of the backend suite (`test_database.py`, plus one test in
`test_health.py`) is marked `requires_db` and asserts things only a live,
migrated database can prove — that the migration actually creates every table
the models describe, that all 14 enum types exist, that the
`positive_verdict_requires_evidence` constraint is genuinely enforced by
PostgreSQL (not merely present in ORM metadata), and that the pgvector
extension is genuinely absent (see [ADR-0005](decisions/0005-pgvector-deferred.md)).
Skipping all of that in CI would mean CI never actually validates a migration,
which is precisely the category of bug Phase 2 found and fixed by hand (the
enum note in §8). The service container costs nothing beyond a few seconds of
startup time and needs no secret — the credentials are the same
intentionally-weak, intentionally-public ones already in `docker-compose.yml`
and `.env.example`.

**Local commands were used to construct every CI step**, and each one was
additionally re-verified against a completely fresh, isolated PostgreSQL
container (not the everyday dev one) before being written into the workflow —
but **actual execution on GitHub Actions has not been observed** until this
repository is pushed and a workflow run completes there. Treat "the workflow
file is correct and its steps were verified locally" and "CI passed on GitHub"
as two different claims; only the first is currently true.

---

## 17. Task script reference

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
| `.\tasks.ps1 check-docs` | check docs for broken relative links and anchors |
| `.\tasks.ps1` | print this list |

If PowerShell refuses to run it, either allow local scripts for the session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

or just use the underlying commands — nothing depends on the script.

---

## 18. Troubleshooting

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

**`npm error code EBADENGINE`**
Your Node version is outside the range in `frontend/package.json`'s `engines`
field (`^22.13.0 || ^24.0.0 || >=26.0.0`), and `frontend/.npmrc` sets
`engine-strict=true` so npm refuses to install rather than risk an untested
combination. Switch to the version in `frontend/.nvmrc` (24.15.0) — `nvm use`
if you have nvm installed.

---

## 19. What is not set up yet

Deliberately absent, arriving in the phase named:

- LLM client and prompts (Phase 4) — `app/llm/` does not exist; `app/services/`
  is an empty package.
- API routes beyond health (Phases 4-10).
- Authentication, rate limiting, structured request logging (Phase 15).
- Frontend routing and the screening UI (Phase 11) — the current page is a shell
  that reports backend connectivity and nothing more.
- Deployment (Phase 19) — CI (§16) validates the code; it does not deploy it.
