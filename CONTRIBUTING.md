# Contributing to CvScreener

This project is developed in strict, sequential phases (see [`docs/roadmap.md`](docs/roadmap.md)); each phase is planned, implemented, verified by actually running the checks, and committed before the next begins. This guide describes the actual workflow used to build it, not an aspirational process.

## Prerequisites

| Tool | Verified version | Enforced by |
|---|---|---|
| Python | 3.10.9 (3.10 is the floor) | [`backend/.python-version`](backend/.python-version), `requires-python` in [`backend/pyproject.toml`](backend/pyproject.toml) |
| Node.js | 24.15.0 | [`frontend/.nvmrc`](frontend/.nvmrc), `engines` in [`frontend/package.json`](frontend/package.json) (enforced at `npm install` via `engine-strict=true`) |
| Docker Desktop | 29.4.3 (must be **running**) | — |
| Git | 2.54.0 | — |

See [`docs/development.md`](docs/development.md#3-language-version-pinning) for exactly how these are enforced, and what "enforced" means for each one (CI reads the version files directly; local enforcement depends on whether you use a version manager).

## Local setup

```powershell
git clone <repository-url> ai-cv-screener
cd ai-cv-screener
.\tasks.ps1 install
Copy-Item .env.example .env
.\tasks.ps1 db-up
.\tasks.ps1 migrate
.\tasks.ps1 test
```

Every command above, its bash equivalent, and a troubleshooting section live in [`docs/development.md`](docs/development.md). If you get stuck, that document — not this one — has the detail.

## Branch naming

Branch off `main`. Name branches `<type>/<short-description>`, matching the commit type prefixes below:

```
feat/pdf-page-offset-extraction
fix/cors-origin-trailing-slash
docs/phase-4-jd-extraction-adr
chore/bump-fastapi
```

## Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <short summary>

<body — the "why," not a restatement of the diff>
```

Types used so far: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`. The summary line is imperative mood ("add", not "added"), under ~72 characters, no trailing period.

Every phase of this project lands as **one commit** covering that phase's full, verified scope (see the phase commits in `git log` for the pattern). Within a phase's development, commit as often as you like locally — squash before landing so history reads as one reviewable unit per phase. Outside the phase workflow (a standalone bug fix, a dependency bump), a normal-sized, single-purpose commit is fine.

## Pull request expectations

A pull request should:

- Describe **what changed and why**, not just what — link the relevant roadmap phase or ADR if one applies.
- Pass CI (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)) before it is merged: backend lint + tests, frontend lint + tests + build.
- Include tests for new behavior. A change with no test coverage for the behavior it adds or fixes will be asked for one.
- Touch documentation when it invalidates something documented — a changed API shape, a new required environment variable, a superseded architectural decision (see [Proposing architectural changes](#proposing-architectural-changes) below).
- Stay scoped to one concern. A PR that mixes a bug fix with an unrelated refactor is harder to review and harder to revert.

## Testing requirements

- **Backend:** `pytest` must pass. Tests that need a live database are marked `@pytest.mark.requires_db` and are *skipped with a stated reason* (not silently passed) when PostgreSQL is unreachable — see [`backend/tests/conftest.py`](backend/tests/conftest.py). A PR that adds a `requires_db` test must have actually run it against real PostgreSQL at least once, not merely written it.
- **Frontend:** `npm test` (Vitest + Testing Library) must pass.
- Do not delete, skip, or weaken a test to make a change pass. If a test is genuinely wrong, fix the test in its own reviewable change and explain why.
- Never fabricate or assume a test result — run it, read the actual output, and report what it says.

## Linting requirements

- **Backend:** `ruff check .` and `ruff format --check .` must both pass. Ruff is this project's only linter and formatter — do not introduce black, isort, or flake8 alongside it.
- **Frontend:** `eslint . --max-warnings 0` and `prettier --check .` must both pass.
- Run `.\tasks.ps1 lint` to check both halves at once, or `.\tasks.ps1 format` to auto-fix what can be auto-fixed.

## Secret handling

- **Never commit a `.env` file, an API key, a database password, or any other credential.** `.env` is git-ignored; only `.env.example` (placeholders only) is tracked. If you need a new environment variable, add it to `.env.example` with an obviously-fake placeholder value and document it in [`docs/development.md`](docs/development.md).
- The development database credentials in [`docker-compose.yml`](docker-compose.yml) are intentionally weak and intentionally committed — they are dev-only, bound to localhost, and hold only synthetic data. Do not "harden" them into something that looks like a real credential; that would be more misleading, not less.
- Before opening a PR, check your diff for anything secret-shaped: `git diff --cached | grep -iE "api[_-]?key|password|secret|token"` is a fast, imperfect first pass — read the actual lines it flags, since most hits in this codebase are intentional placeholders or dev-only values, not real leaks.
- If you ever discover a real secret was committed (even in a past commit, even if later removed), do not just delete it in a new commit — history still contains it. Rotate the credential immediately and say so when reporting the issue; a removed line in a later commit does not undo an exposure.

## Prohibited files and data

Never commit:

- Real candidate CVs or any real personal data. The [`data/`](docs/roadmap.md) directory (Phase 12) is synthetic/fictional data only — see [`docs/product-spec.md` §15](docs/product-spec.md#15-demo-requirements).
- Generated artifacts: `node_modules/`, `.venv/`, `__pycache__/`, `dist/`, `.pytest_cache/`, `.ruff_cache/`, database volumes. These are all git-ignored; if `git status` shows one as untracked-but-should-be-ignored, that is a `.gitignore` bug worth its own small PR.
- Anything a `.gitignore` rule already excludes, added back with `git add -f`. If you find yourself doing that, the file probably shouldn't be committed at all — ask first.

## How to report a bug

Open an issue using the bug report template. At minimum, include:

- What you ran (the exact command) and what you expected.
- What actually happened — the real output or error, not a paraphrase.
- Your environment: OS, Python version, Node version, whether PostgreSQL was reachable.
- Whether it reproduces from a clean clone following [`docs/development.md`](docs/development.md), or only in your local setup.

## Proposing architectural changes

This project records significant, non-obvious architectural decisions as ADRs in [`docs/decisions/`](docs/decisions/README.md) — the AI/deterministic boundary, the evidence-first evaluation model, the sensitive-attribute exclusion, and others. To propose changing one of these:

1. Open an issue or PR describing the new context that motivates a change and the decision you're proposing.
2. Do not silently edit an `Accepted` ADR to reflect a different decision. Write a new ADR that explicitly supersedes it, update the superseded ADR's status line, and update the index in `docs/decisions/README.md`.
3. For a change that affects the data model or the pipeline stages, also update [`docs/architecture.md`](docs/architecture.md) and/or [`docs/data-model.md`](docs/data-model.md) so they keep describing what is actually built.

A change to product scope, the scoring philosophy, or the fairness/security principles in [`docs/product-spec.md`](docs/product-spec.md) should be discussed in an issue before code is written — those documents are the project's contract with itself about what it will and will not do, and are not meant to drift silently.

## Documentation integrity

Relative Markdown links and heading anchors across `README.md` and `docs/*.md` are checked by [`scripts/check_docs.py`](scripts/check_docs.py):

```powershell
python scripts/check_docs.py
```

Run it after adding or renaming a doc, or moving a heading. See the script's own `--help` and [`docs/development.md`](docs/development.md#14-documentation-integrity-check) for details.
