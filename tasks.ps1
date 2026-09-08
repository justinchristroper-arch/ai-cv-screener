<#
.SYNOPSIS
    Developer commands for the AI CV Screener.

.DESCRIPTION
    A thin wrapper over the real commands, not a build system. Everything here
    is a one-line shortcut; docs/development.md spells out the underlying
    commands so nothing is hidden behind this script.

.EXAMPLE
    .\tasks.ps1 install
    .\tasks.ps1 db-up
    .\tasks.ps1 migrate
    .\tasks.ps1 test
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Task = "help",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$Compose = Join-Path $Root "docker-compose.yml"

function Require-Venv {
    if (-not (Test-Path $Python)) {
        throw "Python virtual environment not found at $Python. Run: .\tasks.ps1 install"
    }
}

function Invoke-InDir([string]$Dir, [scriptblock]$Body) {
    Push-Location $Dir
    try { & $Body } finally { Pop-Location }
}

switch ($Task) {

    "install" {
        Write-Host "==> Creating Python virtual environment" -ForegroundColor Cyan
        python -m venv (Join-Path $Backend ".venv")
        & $Python -m pip install --upgrade pip
        Write-Host "==> Installing backend dependencies" -ForegroundColor Cyan
        & $Python -m pip install -r (Join-Path $Backend "requirements.txt") `
                                 -r (Join-Path $Backend "requirements-dev.txt")
        Write-Host "==> Installing frontend dependencies" -ForegroundColor Cyan
        Invoke-InDir $Frontend { npm install }
        Write-Host "Done. Next: copy .env.example to .env, then .\tasks.ps1 db-up" -ForegroundColor Green
    }

    "db-up" {
        docker compose -f $Compose up -d --wait
    }

    "db-down" {
        docker compose -f $Compose down
    }

    "db-reset" {
        Write-Host "This deletes the development database volume." -ForegroundColor Yellow
        docker compose -f $Compose down -v
        docker compose -f $Compose up -d --wait
    }

    "migrate" {
        Require-Venv
        Invoke-InDir $Backend { & $Python -m alembic upgrade head }
    }

    "migration" {
        Require-Venv
        $message = if ($Rest) { $Rest -join " " } else { throw "Usage: .\tasks.ps1 migration `"describe the change`"" }
        Invoke-InDir $Backend { & $Python -m alembic revision --autogenerate -m $message }
    }

    "dev-backend" {
        Require-Venv
        Invoke-InDir $Backend { & $Python -m uvicorn app.main:app --reload --port 8000 }
    }

    "dev-frontend" {
        Invoke-InDir $Frontend { npm run dev }
    }

    "test" {
        Require-Venv
        Invoke-InDir $Backend { & $Python -m pytest }
        Invoke-InDir $Frontend { npm test }
    }

    "test-backend" {
        Require-Venv
        Invoke-InDir $Backend { & $Python -m pytest }
    }

    "test-frontend" {
        Invoke-InDir $Frontend { npm test }
    }

    "lint" {
        # ruff runs from backend/ so it picks up that pyproject's configuration,
        # but it is pointed at scripts/ and evaluation/ too: they are Python this
        # project owns, and leaving them unlinted is how four errors sat in
        # scripts/check_docs.py unnoticed.
        Require-Venv
        Invoke-InDir $Backend {
            & $Python -m ruff check . ../scripts ../evaluation
            & $Python -m ruff format --check . ../scripts ../evaluation
        }
        Invoke-InDir $Frontend {
            npm run lint
            npm run format:check
        }
    }

    "format" {
        Require-Venv
        Invoke-InDir $Backend {
            & $Python -m ruff format . ../scripts ../evaluation
            & $Python -m ruff check --fix . ../scripts ../evaluation
        }
        Invoke-InDir $Frontend { npm run format }
    }

    "check-docs" {
        python (Join-Path $Root "scripts\check_docs.py")
    }

    "check-contrast" {
        python (Join-Path $Root "scripts\check_contrast.py") @Rest
    }

    "coverage" {
        Require-Venv
        Invoke-InDir $Backend {
            & $Python -m pytest --cov=app --cov-report=term-missing:skip-covered @Rest
        }
    }

    "audit" {
        # Both halves. Findings are triaged in writing in docs/security.md —
        # a scan whose output nobody reads is not a control.
        Require-Venv
        Write-Host "==> Python dependencies" -ForegroundColor Cyan
        Invoke-InDir $Backend { & $Python -m pip_audit --progress-spinner off }
        Write-Host "==> Node dependencies" -ForegroundColor Cyan
        Invoke-InDir $Frontend { npm audit }
    }

    "check-llm" {
        # Whichever provider is configured. With Ollama that is a preflight plus
        # four real generations, so it is a task rather than part of `lint`.
        Require-Venv
        Invoke-InDir $Root { & $Python (Join-Path $Root "scripts\check_llm.py") @Rest }
    }

    "evaluate" {
        # Run from the repository root: the package is `evaluation`, and the
        # runner puts backend\ on sys.path itself. Extra arguments pass through,
        # so `.\tasks.ps1 evaluate --no-db` works with no PostgreSQL running.
        Require-Venv
        Invoke-InDir $Root { & $Python -m evaluation.runner @Rest }
    }

    default {
        Write-Host @"
AI CV Screener — developer commands

  .\tasks.ps1 install        Create the venv and install backend + frontend dependencies
  .\tasks.ps1 db-up          Start PostgreSQL and wait until it is healthy
  .\tasks.ps1 db-down        Stop PostgreSQL (keeps data)
  .\tasks.ps1 db-reset       Stop PostgreSQL, DELETE its volume, start fresh
  .\tasks.ps1 migrate        Apply Alembic migrations to head
  .\tasks.ps1 migration "m"  Autogenerate a new migration from the ORM models

  .\tasks.ps1 dev-backend    Run the API on http://localhost:8000 (reload)
  .\tasks.ps1 dev-frontend   Run the UI on http://localhost:5173

  .\tasks.ps1 test           Run backend and frontend tests
  .\tasks.ps1 test-backend   Backend tests only
  .\tasks.ps1 test-frontend  Frontend tests only
  .\tasks.ps1 lint           Lint and format-check both halves
  .\tasks.ps1 format         Apply formatting to both halves
  .\tasks.ps1 check-docs     Check docs for broken relative links and anchors
  .\tasks.ps1 check-contrast Check the UI palette against WCAG AA, both themes
  .\tasks.ps1 check-llm      Check the configured AI provider (add --preflight)
  .\tasks.ps1 coverage       Backend tests with a coverage report
  .\tasks.ps1 audit          Scan Python and Node dependencies for known CVEs
  .\tasks.ps1 evaluate       Measure the pipeline and rewrite evaluation/RESULTS.md
                             (add --no-db to run without PostgreSQL)

There is no single 'dev' task: the two servers are long-running, so run
dev-backend and dev-frontend in separate terminals.

Full documentation, including the raw commands behind each of these and the
bash equivalents, is in docs/development.md.
"@
    }
}
