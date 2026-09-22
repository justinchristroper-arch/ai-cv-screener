# Development Guide

**Status:** current as of the completion milestone — every command below was run.
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
| Ollama | 0.33.3 | **Only for Local AI Mode.** Demo mode and the whole test suite need nothing from it. See §17. |

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
anthropic 1.4.0, pypdf 6.17.0, python-multipart 0.0.32, pytest 9.1.1,
pytest-cov 7.1.0, pip-audit 2.10.1, httpx2 2.12.0, reportlab 5.0.1
(test-only), ruff 0.16.6.

The `anthropic` SDK is imported **only** inside `backend/app/llm/` — every
service above that layer depends on the `LlmClient` protocol instead. In demo
mode it is never called at all; see §23.

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

`DEMO_MODE=true` is the default and means no API key is needed. With
`DEMO_MODE=false`, a hosted provider selected without its key —
`LLM_PROVIDER=deepseek` without `DEEPSEEK_API_KEY`, or `anthropic` without
`ANTHROPIC_API_KEY` — is also a startup failure, by design: live mode never
silently falls back to fixtures. Ollama, the default, needs no key.

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
> CI (§19) now exercises `upgrade → downgrade → upgrade` on every push against a
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

or `.\tasks.ps1 test-backend`. Expected: **694 passed**.

The suite makes no network call and needs no API key: every LLM-backed test
runs against recorded fixtures (§23).

Tests are split by what they need:

- Most run **offline**. Configuration validation, health endpoints and the
  schema assertions need no database.
- Tests marked `requires_db` are **skipped with a stated reason** when
  PostgreSQL is unreachable, naming the connection string it tried. They are
  never silently passed. CI always has a database available (§19), so all
  test — the full suite, `requires_db` included — runs there on every push.

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

or `.\tasks.ps1 test-frontend`. Expected: **98 passed** across 12 files.
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

## 15. Coverage, contrast, and dependency audits

```powershell
.\tasks.ps1 coverage        # backend tests plus a per-module coverage report
.\tasks.ps1 check-contrast  # every colour pair against WCAG AA, in both themes
.\tasks.ps1 audit           # pip-audit over Python, npm audit over Node
```

`check-contrast` reads the custom properties straight out of
`frontend/src/index.css`, so it cannot describe a palette the stylesheet no
longer has. It checks 34 pairs across the light and dark themes at the two
thresholds WCAG defines: 4.5:1 for text and 3:1 for the visual boundary of
something you can click or type into. The second one found a real failure —
the input border was at 1.63:1. It runs in CI alongside the link check, needs
no browser, and is pure standard library.

Coverage is **97% of `backend/app` by statement**. The weakest module is named
rather than averaged away: `app/llm/client.py` at 78%, and every uncovered line
is inside `LiveLlmClient` — code that cannot run offline by construction.
Mocking it deeply enough to cover would raise the number without raising the
confidence; `scripts/check_llm.py` exercises it for real instead.

The audit is not wired into CI on purpose. A new upstream advisory would turn CI
red on a commit that changed nothing, which trains people to ignore it. It is a
verification step with a written triage in
[`docs/security.md` §13](security.md#13-dependency-vulnerabilities) instead —
a scan whose output nobody reads is not a control.

---

## 16. Run the evaluation harness

```powershell
cd C:\path\to\ai-cv-screener
backend\.venv\Scripts\python.exe -m evaluation.runner
```

Run it from the **repository root**, not from `backend\` — the package is
`evaluation`, and the runner puts `backend\` on `sys.path` itself. It rewrites
`evaluation/results.json` and `evaluation/RESULTS.md` in place, so a change to a
matcher shows up as a diff in the results file.

Skill aliases live in a database table, so the alias matcher needs PostgreSQL
running (section 6) to be measured. Without one, pass `--no-db` and alias
matching is evaluated as if the table were empty:

```powershell
backend\.venv\Scripts\python.exe -m evaluation.runner --no-db
```

The numbers differ between the two modes, which is why the results file records
which one produced it. Neither mode calls a language model or needs an API key.

[`evaluation/README.md`](../evaluation/README.md) explains the dataset and why
none of it is model-generated; [`evaluation/RESULTS.md`](../evaluation/RESULTS.md)
holds the measurements.

---

## 17. Local AI Mode (Ollama)

Everything above runs in **demo mode**, which replays recordings and needs no
model. To run a real model over your own criteria and CVs:

```powershell
# 1. Install Ollama — https://ollama.com/download
# 2. Start it. The desktop app does this; otherwise:
ollama serve

# 3. Pull the model, once. About 4.7 GB.
ollama pull qwen2.5:7b-instruct
```

Then in `.env`:

```ini
DEMO_MODE=false
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
```

Check it before opening the UI. This costs nothing and catches the two things
that go wrong first:

```powershell
.\tasks.ps1 check-llm --preflight
```

It reports whether Ollama is answering and whether the model is installed,
naming the command that fixes whichever is missing. Drop `--preflight` to run
the four sample briefs through the model and see what actually comes back.

**Hardware.** About 8 GB of free RAM for the 7B model on CPU, or a GPU with 6 GB+
of VRAM for a large speed-up. Expect seconds to tens of seconds per call on CPU.
On a smaller machine, `OLLAMA_MODEL=qwen2.5:3b-instruct` (~1.9 GB) runs on much
less and is measurably worse at returning valid structured output.

**Nothing else changes.** The confirmation gate, evidence verification,
deterministic matching, scoring and ranking are identical in every mode — the
model reads text, and ordinary code makes every decision
([ADR-0011](decisions/0011-local-model-by-default.md)).

---

## 18. Stop the development environment

```powershell
# Ctrl+C in each dev-server terminal, then:
docker compose down          # stop PostgreSQL, keep the data volume
docker compose down -v       # stop PostgreSQL and DELETE all data
```

`.\tasks.ps1 db-down` and `.\tasks.ps1 db-reset` respectively.

If CvScreener was started with `Start CvScreener.cmd`, double-click
`Stop CvScreener.cmd` instead. It stops only what the launcher started and never
removes the data volume ([section 25](#25-one-click-start-on-windows)).

---

## 19. Continuous integration

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

## 20. Task script reference

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
| `.\tasks.ps1 check-contrast` | check the UI palette against WCAG AA, both themes |
| `.\tasks.ps1 check-llm` | check the configured AI provider (`--preflight` to check setup only) |
| `.\tasks.ps1 evaluate` | run the evaluation harness (`--no-db` passes through) |
| `.\tasks.ps1` | print this list |

If PowerShell refuses to run it, either allow local scripts for the session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

or just use the underlying commands — nothing depends on the script.

---

## 21. Troubleshooting

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

**`could not reach the local model server at http://localhost:11434`**
Ollama is not running. Start it (`ollama serve`, or launch the desktop app) and
re-run. Only affects Local AI Mode; demo mode never contacts it.

**`the local model 'qwen2.5:7b-instruct' is not installed`**
Pull it once: `ollama pull qwen2.5:7b-instruct` (~4.7 GB). The application never
downloads a model itself. `python scripts/check_llm.py --preflight` tells you
which of these two you have before you wait for a timeout.

**`the local model did not answer within 300s`**
A large model on a slow machine. Either raise `OLLAMA_TIMEOUT_SECONDS`, or move
to `OLLAMA_MODEL=qwen2.5:3b-instruct`, which is smaller and faster and less
reliable at returning valid structured output.

---

## 22. What is not set up

Deliberately absent, with the reason:

- **Authentication and authorisation.** None, anywhere. Anyone who can reach the
  API can do anything it does. A scope decision for a local tool, and the single
  largest reason not to deploy this with real applicant data
  ([security.md §1](security.md#1-what-this-system-is-for-threat-modelling-purposes)).
- **OCR.** Scanned or image-only PDFs have no text layer and are recorded as
  failures (`NO_TEXT_LAYER`); nothing recovers text from them. See §24.
- **Language detection.** `parsed_document.language_detected` is always NULL and
  the `UNSUPPORTED_LANGUAGE` failure reason is reserved but never raised.
  Multilingual *criteria* are supported ([ADR-0009](decisions/0009-natural-language-screening-criteria.md));
  detecting a CV's own language is a different feature and is not one of them.
- **A shared rate limit.** There is a per-client one, but its counters live in
  this process's memory — a brake, not a wall. A real limit belongs in a proxy
  ([security.md §8](security.md#8-rate-limiting-and-request-size)).
- **A deployment.** The backend has a Dockerfile and the frontend builds to
  static files; nothing is hosted. CI (§19) validates the code, it does not
  deploy it. See [deployment.md](deployment.md).
- **A measurement of model quality.** Local AI Mode runs a real model and can be
  exercised with `scripts/check_llm.py`, but the LLM-dependent metrics in
  `evaluation/RESULTS.md` are still reported as not measured. A number produced
  by one model on one machine describes that model, not this application, and
  the harness keeps those two things apart on purpose.
- **The Anthropic provider, in practice.** It is implemented and selectable with
  `LLM_PROVIDER=anthropic`, and has never been exercised against a real key in
  this repository.
- **The DeepSeek provider, in practice.** It is implemented, selectable with
  `LLM_PROVIDER=deepseek`, and covered by offline tests of every request field,
  reply shape and failure path — but no test calls DeepSeek, and it has never
  been run against a real key in this repository.

---

## 23. The LLM layer, demo mode, and fixtures

Everything that talks to a language model sits behind one protocol in
`backend/app/llm/client.py`:

```
LlmClient (Protocol)
├── OllamaLlmClient     — a model on this machine;   LLM_PROVIDER=ollama (default)
├── DeepSeekLlmClient   — the DeepSeek API;           LLM_PROVIDER=deepseek
├── AnthropicLlmClient  — the Anthropic API;          LLM_PROVIDER=anthropic
└── ReplayLlmClient     — serves recorded fixtures;   DEMO_MODE=true
```

`DEMO_MODE=true` is the default, so a fresh clone runs the whole job-description
pipeline — and the whole test suite — with **no API key, no network call, and no
cost**. Two rules are enforced in code and covered by tests:

- **Demo mode never falls back to a live call.** A missing fixture raises a 503
  naming the key it looked for. Silent fallback would let the "free" demo
  quietly start spending money.
- **Live mode never falls back to a fixture.** That would present a recording as
  a fresh result.

### Trying it locally

```powershell
# with the backend running (.\tasks.ps1 dev-backend)
$job = (Invoke-RestMethod -Method Post http://localhost:8000/api/jobs `
        -ContentType application/json -Body '{"title":"Senior Backend Engineer"}')
# attach a job description, then:
Invoke-RestMethod -Method Post "http://localhost:8000/api/jobs/$($job.id)/requirements/extract"
```

Or explore the whole surface at <http://localhost:8000/docs>.

### Adding a fixture

Fixtures live in `backend/app/llm/fixtures/*.json` and are keyed on load by
`(purpose, model, prompt_version, sha256(rendered input), attempt)`. **The hash
is computed from the fixture's own input text**, using the same renderer the
runtime uses — nothing is hand-copied, so a fixture cannot silently drift out of
sync with the prompt that produced it.

```json
{
  "description": "what this fixture is for",
  "purpose": "JD_EXTRACTION",
  "model": "claude-opus-5",
  "prompt_version": "jd-extraction-v1",
  "attempt": 1,
  "jd_text": ["line one", "line two"],
  "response_json": { "requirements": [] }
}
```

Use `response_json` for a well-formed reply and `response_text` for a
deliberately malformed one. Both `jd_text` and `response_text` accept a list of
lines, joined with newlines, so multi-line content stays readable in a diff.

> **Changing a prompt invalidates its fixtures.** `PROMPT_VERSION` in
> `app/llm/prompts/jd_extraction.py` is part of the key, so bump it whenever the
> prompt text changes. Replay will then fail loudly for the old fixtures rather
> than quietly replaying output that answered different instructions.

### Running against a real model

Set `DEMO_MODE=false` in `.env` and choose the provider with `LLM_PROVIDER`:
`ollama` needs Ollama running with the model pulled
([section 17](#17-local-ai-mode-ollama)); `deepseek`
needs `DEEPSEEK_API_KEY`; `anthropic` needs `ANTHROPIC_API_KEY`. Startup fails
immediately if the selected provider's key is missing — live mode has no
fallback, so there is no point discovering that at the first request. A key is
read server-side only and is never logged or returned in a response.

`python scripts/check_llm.py --preflight` checks the selected provider without
generating anything. For DeepSeek it asks for the model list, which spends no
tokens; without `--preflight` the script runs real extractions, which do.

---

## 24. CV upload and PDF parsing

Uploading a CV runs a fixed, deterministic pipeline. No language model is
involved: turning a PDF into text is mechanical, and keeping it mechanical is
what gives every later evidence citation a stable substrate to be checked
against.

```
PDF  ->  validate  ->  store  ->  extract text  ->  normalize  ->  persist
```

### Trying it

```powershell
# with the backend running (.\tasks.ps1 dev-backend)
curl.exe -X POST "http://localhost:8000/api/jobs/<job-id>/candidates" `
  -F "files=@cv_one.pdf" -F "files=@cv_two.pdf"
```

Then `GET /api/candidates/{id}` for status and metadata, or
`GET /api/candidates/{id}/text` for the extracted text and its page map. The
full surface is at <http://localhost:8000/docs>.

### What a file has to satisfy

| Check | Limit | Setting |
|---|---|---|
| Filename ends `.pdf` | — | — |
| Not empty | > 0 bytes | — |
| Size | 10 MB | `MAX_UPLOAD_SIZE_MB` |
| Starts with the `%PDF-` signature | — | — |
| Opens as a readable PDF | — | — |
| Page count | 20 | `MAX_PDF_PAGES` |
| Files per request | 25 | `MAX_FILES_PER_BATCH` |

**The `Content-Type` header is never consulted.** It is trivially forged and
says nothing about the bytes actually received, so the file's own signature is
what decides. A PNG renamed `cv.pdf` is rejected.

### Rejected versus failed

A batch is never all-or-nothing — each file gets its own outcome, and one bad
file among twenty-five costs you only that one.

- **Rejected** — a structural problem knowable before storing anything. No
  candidate row is created. Reported as `rejection_code`:
  `unsupported_file_type`, `empty_file`, `file_too_large`, `not_a_pdf`,
  `malformed_pdf`, `too_many_pages`, `missing_filename`.
- **Failed** — a readable PDF whose *content* is unusable. A candidate row
  exists with `status = FAILED` and a `failure_reason`, because the recruiter
  who uploaded it needs to see that it did not make it: `NO_TEXT_LAYER`,
  `CORRUPT_FILE`, `PARSE_TIMEOUT`.

### There is no OCR

**Scanned or image-only PDFs are not supported, because OCR is not implemented
in this project.** Such a file has no text layer; extraction produces nothing,
and the candidate is recorded as `FAILED` with `NO_TEXT_LAYER`. Nothing is
invented to fill the gap — a fabricated CV would be far worse than an honest
failure. OCR is listed as a future improvement in
[product-spec.md §19](product-spec.md#18-future-improvements).

### Storage

Uploaded files are written under `UPLOAD_STORAGE_DIR` (default
`<repo>/var/uploads`, git-ignored). **The path is derived from a
server-generated UUID and from nothing else** — a client filename never
influences where a byte lands. The original name is kept only as sanitized
display metadata, and the API never returns a filesystem path.

The database stores a path *relative* to the storage root, so the root can move
between environments without a data migration.

### Page provenance

`parsed_document.page_offsets` records the character range of every page:

```json
[{"page": 1, "start": 0, "end": 318}, {"page": 2, "start": 320, "end": 582}]
```

`full_text[start:end]` returns exactly that page. This is what lets a later
evidence quote be traced back to a page **without asking a model where it came
from** — the model quotes, our code locates.

### Column layout

Extraction flattens a page into one stream of lines. For a two-column CV that
stream interleaves two unrelated narratives, and the damage is not obvious in
the output. On one real CV an education line from the left column landed after
the experience heading from the right, and the screener counted a school stream
as a job — 58 months of "professional experience" out of about fourteen.

`detect_columns` looks at **where the text sits**, never at what it says. Each
text run's origin is collected through pypdf's `extract_text(visitor_text=...)`
callback — `cm[0]*tm[4] + cm[2]*tm[5] + cm[4]` for x and `cm[1]*tm[4] +
cm[3]*tm[5] + cm[5]` for y, the text matrix composed with the graphics matrix of
the text object it sits inside. The distinct x positions are sorted and the
widest gap between neighbours becomes a candidate gutter. Then four things must
hold at once:

| Condition | Constant | Rules out |
|---|---|---|
| The gutter is wider than a fifth of the page | `COLUMN_GAP_SHARE` | paragraph indents |
| Each side carries 15% of the runs | `COLUMN_MIN_SHARE` | one stray run in the margin |
| The two sides share half of the shorter one's vertical extent | `COLUMN_MIN_VERTICAL_OVERLAP` | a **centred name block** stacked above the body |
| A quarter of the right side's lines sit on baselines of their own | `COLUMN_MIN_OWN_BASELINES` | a **right-aligned date column** |

Fewer than eight runs on the page means no opinion at all.

The last two conditions were added because the first two, on their own, flagged
two entirely ordinary CV shapes. A CV with right-aligned dates has most of the
page between its bullet text and its dates, and a terse one puts a fifth of its
runs in that date column — but no date ever has a line to itself. A CV with a
centred header clears all of the first three-and-a-half tests and its header
lines do have their own baselines — but the header is stacked above the body
rather than running beside it. Both shapes are common; flagging them would make
the warning worthless, which is the real failure mode for a caution a recruiter
is asked to act on. Both are now fixtures in `tests/pdf_fixtures.py`.

The detector errs towards silence: a thin sidebar — four contact lines beside a
full page of prose — fails the share test and is missed. That is the right
direction to be wrong in. It is checked against both synthetic fixtures and two
real two-column CVs.

`parsed_document.multi_column_pages` is nullable JSONB, so a parse recorded
before this existed has no opinion about its layout, which is true.

**It is not repaired.** The flag becomes a `MULTI_COLUMN_LAYOUT` warning on the
ranked list and a callout on the candidate page, telling the recruiter to read
the original file for that candidate. Reordering the lines would mean guessing
at the intended reading order — a second way to get it wrong, applied silently
to a document a human can simply open.

### Known limitations

- **No OCR** (above).
- **Multi-column and table-heavy layouts** can extract in the wrong reading
  order. pypdf reads the text layer as the PDF stores it; it does not
  reconstruct visual columns. `detect_columns` notices the geometry while the
  page is open and records the page number in `parsed_document.multi_column_pages`
  (see "Column layout" above). The layout is **reported, not corrected**.
- **Character-level offsets within a page are not recorded** — only page
  ranges. Evidence verification searches the text directly, so sentence-level
  spans are located at verification time rather than pre-computed here.
- **The parse time budget is checked between pages**, so it bounds a long
  document but cannot interrupt a single pathological page. A hard limit needs
  process isolation, deferred to the Phase 15 security review.
- **Duplicate uploads are not deduplicated.** `file_sha256` is stored and
  indexed, so identical files are *detectable*, but uploading the same CV twice
  creates two candidates. The data model has no concept of a merged candidate,
  and inventing one here would be a product decision rather than an ingestion one.

---

## 25. One-click start on Windows

`Start CvScreener.cmd` and `Stop CvScreener.cmd` in the repository root run
CvScreener without opening terminals. Both call
[`scripts/start-cvscreener.ps1`](../scripts/start-cvscreener.ps1), and every step
that changes anything is an existing `tasks.ps1` task, so there is still exactly
one way to do each thing. `-ExecutionPolicy Bypass` applies to that one
PowerShell process; no system setting changes.

**Start** runs these steps in order, in one window, and stops at the first
problem with a message saying what to do:

| Step | What happens |
|---|---|
| First-time setup | If `backend\.venv` or `frontend\node_modules` is missing, asks before running `.\tasks.ps1 install`. If `.env` is missing, asks before copying `.env.example`. Both default to **No**, and an existing `.env` is never overwritten. |
| Docker | Starts Docker Desktop if the engine does not answer, and waits up to three minutes. Each `docker info` check gives up after 20 seconds, so an engine that is stuck, not just stopped, ends in a message instead of a window that waits forever. |
| PostgreSQL | `.\tasks.ps1 db-up`, given up to five minutes (time for a first download of the PostgreSQL image). |
| Migrations | Prints the current and latest revision, then `.\tasks.ps1 migrate`. |
| AI provider | Read through the application's own settings loader, so environment variables and `.env` apply exactly as they do for the app; the launcher never opens `.env`. Demo mode: nothing to check. DeepSeek or Anthropic: `.\tasks.ps1 check-llm --preflight`. Ollama: starts the Ollama app if it does not answer; if the configured model is missing, shows the exact `ollama pull` command and asks, defaulting to **No**; then the same preflight. |
| Backend | `.\tasks.ps1 dev-backend` in a minimized window, then waits for `/health`. |
| Frontend | `.\tasks.ps1 dev-frontend` in a minimized window, then waits for the page. Each window's first line says which part it runs; the frontend's title cannot be relied on, because `npm run dev` runs through `cmd.exe`, which retitles the window. |
| Browser | Opens http://localhost:5173. |

**In Local AI mode Ollama is required**, and the launcher will not start the app
without it. Screening a CV currently extracts a profile with the model before
matching, even when every criterion is structured; that is recorded as an open
follow-up in [roadmap.md](roadmap.md#after-the-roadmap-structured-screening-adr-0012).

Running Start again while CvScreener is up is safe. A backend or frontend that
already answers is reused rather than started twice, and a second launcher
window refuses to run while the first is still working instead of racing it. A
port held by some other program is reported by name and process id, and left
alone.

**Stop** ends only what Start launched. Start records the process id *and* the
start time of each window it opens, and of the process listening on each port,
in `var/launcher/state.json` (gitignored). Stop ends a process tree only when
both still match, so a process id Windows has since given to another program is
never touched. The database container is stopped with `docker compose stop db`
only if Start was the one that started it, and the container and its data
volume are kept. If Docker does not answer in time, Stop says so, exits with an
error, and keeps its record, so the next Stop tries again. Docker Desktop and
Ollama are left running.

Neither ever runs `db-reset`, `docker compose down`, `down -v` or `docker prune`,
deletes a volume, or downloads a model without an explicit **y**.

Four things to know before changing the script:

- **`tasks.ps1` is called in-process, except `db-up`.** In Windows PowerShell 5.1
  a failed native command does not stop a script, and a script run as a child
  `powershell.exe` exits with code 0 anyway, so a failed `docker compose up`
  would look like success. In-process, `$LASTEXITCODE` holds the real result.
  `db-up` runs Docker, which can hang, and only a separate process can be given
  a time limit, so it runs in a child `powershell.exe` that passes the exit code
  on itself (`exit $LASTEXITCODE`).
- **Every Docker command has a time limit.** PowerShell's `&` waits for a native
  command however long it takes, and with Docker Desktop's engine stuck a single
  `docker info` has waited more than ten minutes. Docker commands therefore run
  through `Invoke-WithTimeout`, which on timeout ends that command and the
  processes started under it (matched by parent process id *and* a later start
  time) and nothing else.
- **The script is ASCII.** Windows PowerShell 5.1 reads a file with no byte-order
  mark in the ANSI code page, which is how `tasks.ps1` came to print its em dash
  as `â€”`.
- **The `.cmd` files are CRLF**, pinned in `.gitattributes`, because `cmd.exe`
  can mis-parse a batch file with LF line endings.
