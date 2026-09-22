# Deployment

**This project is not deployed.** There is no public URL, no hosting account,
and no cold-start or uptime figures to report. What follows is the configuration
a deployment needs and the decisions to make before one exists — written after
building and running the image, not from memory.

Everything here is deliberately platform-neutral. The application is a container
that speaks HTTP and a static bundle; anything that can run those will do, and
committing to one vendor's YAML would be a dependency bought for nothing.

---

## 1. What has to run

Three pieces:

| Piece | What it is | Notes |
|---|---|---|
| **Backend** | One container, `backend/Dockerfile` | Uvicorn on port 8000. Stateless except for the upload directory. |
| **Database** | PostgreSQL 16 | Any managed instance. No extensions required — pgvector was evaluated and deferred ([ADR-0005](decisions/0005-pgvector-deferred.md)). |
| **Frontend** | A static bundle, `npm run build` → `frontend/dist/` | Plain files. Any static host or CDN. |

The frontend calls the backend directly from the browser, so the backend's CORS
allowlist and the frontend's `VITE_API_BASE_URL` must agree. There is no reverse
proxy in the design; if you add one, that is where a real rate limit belongs
(see [security.md §8](security.md#8-rate-limiting-and-request-size)).

---

## 2. Decide these first

### Demo mode, or a real model

**A public demo should run with `DEMO_MODE=true`.** Every AI call is served from
a recording, so it costs nothing, needs no model server, behaves identically on
every visit, and there is no key to leak. The whole workflow — four sample
briefs, three synthetic CVs, ranking and evidence — works in that mode; that is
what it is for.

`DEMO_MODE=false` runs a real model over whatever a visitor types and uploads,
and on a public URL with **no authentication** that is a problem either way:

- with `LLM_PROVIDER=deepseek` or `anthropic`, it is an open invitation to spend
  your money — and every visitor's upload is sent to that provider;
- with `LLM_PROVIDER=ollama`, it is an open invitation to occupy your CPU or GPU
  for seconds at a time, per request, which is a denial-of-service surface.

If you do it anyway, put authentication in front of it first.

### A hosted model: DeepSeek

A platform that runs containers rarely runs a 4.7 GB local model as well, so a
deployment with `DEMO_MODE=false` will usually use `LLM_PROVIDER=deepseek`.
What that changes:

- **The key comes from the platform's secret store**, never from a file in the
  image and never from the repository. The application reads
  `DEEPSEEK_API_KEY` from the environment and refuses to start without it.
- **Every screened CV leaves your infrastructure.** DeepSeek's privacy policy
  (last updated February 2026) says the personal data it collects is stored and
  processed in the People's Republic of China; API use is governed by its
  open-platform terms. A CV is personal data, and sending it to a processor
  abroad is regulated — in Indonesia by Law No. 27 of 2022 on personal data
  protection. Read both before any real CV goes through it.
- **Cost is per call and uncapped by this application.** The in-process rate
  limit is a brake, not a budget. DeepSeek bills against a balance you top up,
  and answers HTTP 402 when it runs out, so the balance is the only hard cap:
  keep it no larger than you are prepared to lose.
- **The preflight is free.** `python scripts/check_llm.py --preflight` lists the
  models the key can use without spending a token, so it can run on every
  deploy.

### Where uploaded files go

`UPLOAD_STORAGE_DIR` defaults to `/var/lib/ai-cv-screener/uploads` inside the
image. That path is inside the container's writable layer, so it disappears when
the container does. Mount a volume over it if the files need to survive a
restart — and note that uploaded CVs are personal data, so a volume that
survives is a thing you are now responsible for.

A deployment that only ever runs the demo needs no volume at all: the sample
PDFs are in the image, and everything they produce is in the database.

### Whether anyone should be able to reach it

There is **no authentication**. Anyone who can reach the API can create jobs,
upload documents, and read every candidate in the database. That is a deliberate
scope decision for a local tool, and it is the single largest reason not to put
this in front of the public with real data in it.

---

## 3. Configuration

Every variable is listed in [`.env.example`](../.env.example) and in the
[README](../README.md#environment-variables). The ones a deployment must set
explicitly:

| Variable | Value in a deployment |
|---|---|
| `DATABASE_URL` | The managed instance's URL, **with the `+psycopg` suffix**. A bare `postgresql://` makes SQLAlchemy look for psycopg2, which is not installed. |
| `APP_ENV` | `production` |
| `DEMO_MODE` | `true` for a public demo. See above. |
| `LLM_PROVIDER` | `ollama` (default), `deepseek` or `anthropic`. Only read when demo mode is off. |
| `OLLAMA_BASE_URL` | Where Ollama listens. **Not `localhost` from inside a container** — see below. |
| `OLLAMA_MODEL` | Must already be pulled on whatever machine runs Ollama. |
| `DEEPSEEK_API_KEY` | Only when `LLM_PROVIDER=deepseek`. **From the platform's secret store, never from a file in the image.** |
| `DEEPSEEK_MODEL` | `deepseek-flash` unless you have a reason; the preflight checks the key can use it. |
| `DEEPSEEK_BASE_URL` | Leave at `https://api.deepseek.com` unless a gateway sits in front of it. |
| `ANTHROPIC_API_KEY` | Only when `LLM_PROVIDER=anthropic`. **From the platform's secret store, never from a file in the image.** |
| `CORS_ALLOWED_ORIGINS` | Exactly the deployed frontend's origin. Never `*`. |
| `UPLOAD_STORAGE_DIR` | The mount point of the volume, if there is one. |
| `RATE_LIMIT_ENABLED` | `true`. It is a brake, not a wall — put a real limit in the proxy too. |

### Reaching Ollama from a container

`localhost` inside a container is the container, not the host, so a backend in
Docker cannot reach a host Ollama at `http://localhost:11434`. Three shapes,
in the order most deployments want them:

| Setup | `OLLAMA_BASE_URL` |
|---|---|
| Backend on the host, Ollama on the host | `http://localhost:11434` |
| Backend in Docker Desktop, Ollama on the host | `http://host.docker.internal:11434` |
| Both in Compose on one network | `http://ollama:11434` (you supply the service) |

Ollama binds to `127.0.0.1` by default, which is the right default and also
means a host install is not reachable from a container until you set
`OLLAMA_HOST=0.0.0.0` on **Ollama's** side. Do that only on a machine where
that port is not exposed to a network you do not control: Ollama has no
authentication of its own.

**A public deployment should run `DEMO_MODE=true` and not use Ollama at all.**
Local inference on a shared host means every visitor's upload occupies the CPU
or GPU for seconds at a time, with no authentication in front of it — which is
a denial-of-service surface, not a feature.

The backend **fails fast**: a missing or invalid variable stops the process at
startup with a message naming it, rather than surfacing as a confusing error at
the first request. That is a feature in a deployment — a container that will not
start is easier to diagnose than one that starts and misbehaves.

The frontend has exactly one variable, `VITE_API_BASE_URL`, and it is **baked in
at build time**, not read at runtime. Building for one origin and serving on
another does not work; rebuild instead.

```bash
cd frontend
VITE_API_BASE_URL=https://api.example.invalid npm run build
# → dist/  (about 230 kB of JS, 71 kB gzipped, plus 13 kB of CSS)
```

---

## 4. Build and run

```bash
# Backend image, from the repository root
docker build -t ai-cv-screener-backend:latest backend/

# Run it against an existing database
docker run --rm -p 8000:8000 \
  -e DATABASE_URL="postgresql+psycopg://user:pass@host:5432/ai_cv_screener" \
  -e DEMO_MODE=true \
  -e APP_ENV=production \
  -e CORS_ALLOWED_ORIGINS="https://app.example.invalid" \
  ai-cv-screener-backend:latest
```

Two properties of the image worth knowing:

- **It runs as a non-root user** (`screener`, uid 10001). Nothing in the
  application needs root.
- **It does not run migrations.** Applying schema changes from every starting
  replica is a race. Run them as a separate step, before the rollout:

  ```bash
  docker run --rm \
    -e DATABASE_URL="postgresql+psycopg://user:pass@host:5432/ai_cv_screener" \
    ai-cv-screener-backend:latest \
    alembic upgrade head
  ```

The build was verified locally: the image builds from `requirements.lock.txt`
(the verified versions, not the compatible ranges), starts, answers `/health`
and `/health/db`, serves `/api/demo/samples`, and its own `HEALTHCHECK` reports
healthy. Final size, 447 MB.

---

## 5. Health checks

Two endpoints, separate on purpose:

| Endpoint | Answers | Use it for |
|---|---|---|
| `GET /health` | Is this process up and configured? Touches nothing external. | **Liveness.** A database outage must not make the process look dead and get it killed during the incident where its logs matter most. |
| `GET /health/db` | Can this process reach PostgreSQL? Returns 503 when it cannot. | **Readiness**, and monitoring. |

`/health/db` reports the exception *class name* only when it fails. SQLAlchemy
error strings routinely embed the connection URL, password included.

The image's built-in `HEALTHCHECK` hits `/health`, so `docker ps` shows
`(healthy)` without any orchestrator configuration.

---

## 6. Rolling back

The application is stateless apart from the database and the upload volume, so
rolling back the container is rolling back to the previous image tag. Two
caveats that are properties of this codebase rather than of any platform:

- **Migrations are forward-only in practice.** Alembic can `downgrade`, but a
  rollback across a migration that dropped a column loses that column's data.
  Roll the image back first, and only reverse a migration deliberately.
- **A stored score names the config it was computed under**
  (`scoring_config_version`), so a rollback that changes scoring constants does
  not silently reinterpret old rows — they still say which rules produced them
  ([ADR-0008](decisions/0008-deterministic-scoring.md)).

---

## 7. What is not here, and why

- **No platform YAML.** No `fly.toml`, no `render.yaml`, no Kubernetes
  manifests, no Terraform. Each would bind this repository to one vendor and
  would be unverifiable without an account there. The container and the
  variables above are what a platform needs; the mapping is ten lines wherever
  you deploy.
- **No CDN, queue, cache or object store.** Nothing in the pipeline needs one.
  Screening runs synchronously and finishes in seconds per candidate.
- **No monitoring or error-tracking integration.** The application logs to
  stdout in a structured-enough form for a platform's log viewer, and
  deliberately never logs CV content or a key
  ([security.md §9](security.md#9-logging-and-privacy)). Wiring a specific
  vendor's SDK is a decision for whoever deploys it.
- **No measured cold start.** Reporting one would mean inventing it.

---

**See also:** [development.md](development.md) for local setup,
[security.md](security.md) for what the controls do and do not cover,
[README](../README.md#publication-and-maintenance-checklist) for the checks
worth repeating before a fork or a deployment.
