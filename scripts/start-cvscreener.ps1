<#
.SYNOPSIS
    Start or stop CvScreener with a double-click.

.DESCRIPTION
    "Start CvScreener.cmd" and "Stop CvScreener.cmd" in the repository root call
    this script. It adds orchestration only: every step that changes anything is
    an existing .\tasks.ps1 task (install, db-up, migrate, check-llm, dev-backend,
    dev-frontend), so there is still exactly one way to do each thing.

    Start runs, in order: first-time setup, Docker Desktop, PostgreSQL,
    migrations, the AI provider (Ollama in Local AI mode), backend, frontend, and
    then opens http://localhost:5173. Running it again while CvScreener is up
    starts nothing twice.

    Stop ends only what Start launched. A process counts as launched by Start
    only if its process id AND its start time match what Start recorded, because
    Windows reuses process ids.

    Neither ever runs db-reset, docker compose down, down -v or docker prune,
    deletes a volume, reads or prints .env, downloads a model without an explicit
    "y", or stops a process the launcher did not start.

    Every Docker command has its own time limit. When Docker Desktop's engine is
    stuck, a single `docker info` can wait forever; the launcher then gives up
    on that command, ends it, and says what to do, instead of waiting with it.

    This file is deliberately ASCII. Windows PowerShell 5.1 reads a script that
    has no byte-order mark in the ANSI code page, so a single em dash prints as
    mojibake -- which is exactly what tasks.ps1 did with its banner.

.PARAMETER Stop
    Stop what Start launched, and nothing else.
#>
[CmdletBinding()]
param(
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Tasks = Join-Path $Root "tasks.ps1"
$Backend = Join-Path $Root "backend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$NodeModules = Join-Path $Root "frontend\node_modules"
$EnvFile = Join-Path $Root ".env"
$EnvExample = Join-Path $Root ".env.example"
$Compose = Join-Path $Root "docker-compose.yml"
$StateDir = Join-Path $Root "var\launcher"
$StateFile = Join-Path $StateDir "state.json"

$DbContainer = "ai-cv-screener-db"
$BackendPort = 8000
$FrontendPort = 5173
$BackendUrl = "http://localhost:$BackendPort"
$FrontendUrl = "http://localhost:$FrontendPort"

$DockerDesktopExe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
$DockerBinDir = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
$OllamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$OllamaTrayExe = Join-Path $OllamaDir "ollama app.exe"
$OllamaCliExe = Join-Path $OllamaDir "ollama.exe"

$DockerWaitSeconds = 180     # Docker Desktop, until its engine answers
$DockerCheckSeconds = 20     # one `docker info` or `docker inspect`
$DatabaseStartSeconds = 300  # `docker compose up --wait`, with time for a first image download
$DatabaseStopSeconds = 60    # `docker compose stop`
$OllamaWaitSeconds = 60
$BackendWaitSeconds = 120
$FrontendWaitSeconds = 90

# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    OK  $Text" -ForegroundColor Green
}

function Write-Info([string]$Text) {
    Write-Host "        $Text"
}

function Write-Caution([string]$Text) {
    Write-Host "    !!  $Text" -ForegroundColor Yellow
}

function Fail-Launcher([string[]]$Lines) {
    # A problem the person at the keyboard can act on. Marked so the top level
    # prints the message on its own, without a PowerShell stack trace.
    $problem = [System.InvalidOperationException]::new(($Lines -join [Environment]::NewLine))
    $problem.Data["CvScreenerLauncher"] = $true
    throw $problem
}

function Read-YesNo([string]$Question) {
    # The default is No. A window that cannot ask at all also gets No: never a
    # silent Yes.
    try {
        $answer = Read-Host "$Question [y/N]"
    } catch {
        Write-Info "(This window cannot ask a question, so the answer is No.)"
        return $false
    }
    return [bool]("$answer" -match '^\s*(y|yes)\s*$')
}

function Wait-Until([int]$Seconds, [scriptblock]$Condition) {
    # The condition is handed the seconds left, so a check that can block on
    # its own -- a Docker command -- can be held to this same deadline.
    $deadline = (Get-Date).AddSeconds($Seconds)
    $dots = $false
    while ((Get-Date) -lt $deadline) {
        $left = [Math]::Max(1, [int][Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds))
        if (& $Condition $left) {
            if ($dots) { Write-Host "" }
            return $true
        }
        Write-Host "." -NoNewline
        $dots = $true
        Start-Sleep -Seconds 3
    }
    if ($dots) { Write-Host "" }
    return $false
}

# --------------------------------------------------------------------------
# Native commands
# --------------------------------------------------------------------------

function Invoke-Quiet([string]$FilePath, [string[]]$Arguments) {
    # Output discarded, success returned. Windows PowerShell 5.1 turns a
    # redirected stderr line into an error record, which ErrorActionPreference
    # Stop would make fatal, so the preference is relaxed for exactly this call.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Invoke-Capture([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    # Standard output only; stderr discarded. For values the launcher reads,
    # never for anything the person needs to see.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    Push-Location -LiteralPath $WorkingDirectory
    try {
        $lines = & $FilePath @Arguments 2> $null
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Lines = @($lines) }
    } catch {
        return [pscustomobject]@{ ExitCode = -1; Lines = @() }
    } finally {
        Pop-Location
        $ErrorActionPreference = $previous
    }
}

function ConvertTo-CommandLineArgument([string]$Value) {
    # Windows hands a program one command-line string, which the program splits
    # back into arguments -- Docker by the same rules as C programs: an argument
    # with a space or a quote goes in quotes, a quote inside it is escaped, and
    # backslashes are doubled only where they come before a quote.
    if ($Value -and $Value -notmatch '[\s"]') { return $Value }
    return '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Stop-StartedProcessTree([System.Diagnostics.Process]$Process) {
    # Ends a process this launcher started and every process started under it
    # (`docker compose` runs docker-compose.exe; `docker info` runs each CLI
    # plugin), and nothing else. Windows reuses process ids, so a child is
    # recognised by its parent's id AND by having started after that parent,
    # and it is ended through a handle opened before its start time is
    # compared, which stops the id from being reused in between.
    try { $started = $Process.StartTime } catch { $started = $null }
    try { $Process.Kill() } catch { }
    # Without the parent's start time a child cannot be told from a stranger,
    # so then only the process itself is ended.
    if (-not $started) { return }
    try {
        $all = @(Get-CimInstance -ClassName Win32_Process -Property ProcessId, ParentProcessId, CreationDate)
    } catch {
        return
    }
    $parents = @([pscustomobject]@{ Id = $Process.Id; Started = $started })
    while ($parents.Count -gt 0) {
        $children = @()
        foreach ($parent in $parents) {
            foreach ($entry in $all) {
                if ($entry.ParentProcessId -ne $parent.Id -or $entry.CreationDate -lt $parent.Started) { continue }
                $child = Get-Process -Id $entry.ProcessId -ErrorAction SilentlyContinue
                if (-not $child) { continue }
                try {
                    $null = $child.Handle
                    if ([Math]::Abs(($child.StartTime - $entry.CreationDate).TotalMilliseconds) -lt 1) {
                        $child.Kill()
                        $children += [pscustomobject]@{ Id = $child.Id; Started = $entry.CreationDate }
                    }
                } catch { }
            }
        }
        $parents = $children
    }
}

function Invoke-WithTimeout([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds, [switch]$Capture) {
    # A native command, given at most $TimeoutSeconds. PowerShell's `&` waits
    # for as long as a native command takes, and a Docker command can take
    # forever: with Docker Desktop's engine stuck, `docker info` once waited for
    # more than ten minutes. On timeout this ends that command and what it
    # started, and nothing else -- never Docker Desktop, its engine, or a
    # docker.exe the launcher did not start.
    #
    # -Capture reads the output instead of showing it. Without it the command
    # writes to this window, as it would under `&`.
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = (@($Arguments) | ForEach-Object { ConvertTo-CommandLineArgument $_ }) -join " "
    $info.WorkingDirectory = $Root
    $info.UseShellExecute = $false
    if ($Capture) {
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    }
    $process = [System.Diagnostics.Process]::Start($info)
    try {
        if ($Capture) {
            # Read while it runs: a full pipe would otherwise stall it.
            $output = $process.StandardOutput.ReadToEndAsync()
            $errors = $process.StandardError.ReadToEndAsync()
        }
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) { Stop-StartedProcessTree $process }
        $lines = @()
        $message = ""
        if ($Capture) {
            # Waited for with a limit too: a process left behind could hold a
            # pipe open.
            try {
                if ($output.Wait(5000)) { $lines = @($output.Result -split "\r?\n" | Where-Object { $_ }) }
                if ($errors.Wait(5000)) { $message = $errors.Result.Trim() }
            } catch { }
        }
        $exitCode = $null
        if (-not $timedOut) { $exitCode = $process.ExitCode }
        return [pscustomobject]@{
            ExitCode = $exitCode
            TimedOut = $timedOut
            Seconds  = $TimeoutSeconds
            Lines    = $lines
            Message  = $message
        }
    } finally {
        $process.Dispose()
    }
}

function Invoke-Docker([string[]]$Arguments, [int]$TimeoutSeconds, [switch]$Capture) {
    # The docker.exe that `& docker` would run.
    $docker = @(Get-Command docker.exe -CommandType Application)[0].Source
    return Invoke-WithTimeout -FilePath $docker -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds -Capture:$Capture
}

function Invoke-TaskWithTimeout([string]$Task, [int]$TimeoutSeconds) {
    # A .\tasks.ps1 task that runs Docker, in a child Windows PowerShell so that
    # it can be given a time limit; its output still appears in this window.
    # The child passes on the task's exit code itself, because a child
    # powershell.exe otherwise exits with 0 after a failed native command, and
    # shows no progress bar (a new PowerShell flashes "Preparing modules for
    # first use"). -ExecutionPolicy Bypass applies to that one process, as it
    # does to this one.
    $command = "`$ProgressPreference = 'SilentlyContinue'; & '$($Tasks.Replace("'", "''"))' $Task; exit `$LASTEXITCODE"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return Invoke-WithTimeout -FilePath "powershell.exe" -TimeoutSeconds $TimeoutSeconds `
        -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded)
}

# Every other .\tasks.ps1 call below is made in-process, never through a child
# powershell.exe. In Windows PowerShell 5.1 a failed native command does not
# stop a script, and a child powershell.exe then exits with code 0 anyway, so a
# failed command would look like success. In-process, the global $LASTEXITCODE
# holds the real result of the one native command each task runs. db-up is the
# exception: it runs Docker, so it needs a time limit, and a time limit needs a
# separate process (Invoke-TaskWithTimeout).

# --------------------------------------------------------------------------
# Processes, ports and the launcher's record of what it started
# --------------------------------------------------------------------------

function Get-PortOwner([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) { return $null }
    return Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
}

function New-ProcessRecord($Process) {
    if (-not $Process) { return $null }
    try {
        return [pscustomobject]@{
            pid     = $Process.Id
            started = [string]$Process.StartTime.ToUniversalTime().Ticks
            name    = $Process.ProcessName
        }
    } catch {
        return $null
    }
}

function Find-RecordedProcess($Record) {
    # Only the process Start launched: the id AND the start time must match. A
    # bare id match could point at an unrelated program that was given the same
    # id after the original exited.
    if (-not $Record -or -not $Record.pid) { return $null }
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    try {
        $started = [string]$process.StartTime.ToUniversalTime().Ticks
    } catch {
        return $null
    }
    if ($started -ne [string]$Record.started) { return $null }
    return $process
}

function Get-LauncherState {
    $state = [pscustomobject]@{
        version                   = 1
        backend                   = $null
        frontend                  = $null
        databaseStartedByLauncher = $false
    }
    if (Test-Path -LiteralPath $StateFile) {
        try {
            $saved = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
            foreach ($name in "backend", "frontend", "databaseStartedByLauncher") {
                if ($saved.PSObject.Properties.Name -contains $name) { $state.$name = $saved.$name }
            }
        } catch {
            Write-Caution "The launcher's record in var\launcher\state.json could not be read; starting a new one."
        }
    }
    return $state
}

function Save-LauncherState($State) {
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Enter-SingleLauncher {
    # One launcher at a time, per repository. A second double-click while the
    # first window is still starting things would otherwise race it and could
    # start a second backend. Windows releases the mutex when this process ends.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant())))
    $name = "Local\CvScreenerLauncher-" + $hash.Replace("-", "").Substring(0, 16)
    $script:LauncherMutex = [System.Threading.Mutex]::new($false, $name)
    try {
        $acquired = $script:LauncherMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        Fail-Launcher @(
            "Start or Stop CvScreener is already running in another window.",
            "Let that window finish, then try again."
        )
    }
}

function Add-DockerToPath {
    if (Get-Command docker -ErrorAction SilentlyContinue) { return $true }
    if (Test-Path -LiteralPath (Join-Path $DockerBinDir "docker.exe")) {
        # Installed, but not on PATH in this window. tasks.ps1 calls `docker` by
        # name, so it is added for this process only.
        $env:PATH = "$DockerBinDir;$env:PATH"
        return $true
    }
    return $false
}

# --------------------------------------------------------------------------
# Start: 1. first-time setup
# --------------------------------------------------------------------------

function Test-FirstTimeSetup {
    Write-Step "First-time setup"
    $missing = @()
    if (-not (Test-Path -LiteralPath $Python)) { $missing += "the Python environment (backend\.venv)" }
    if (-not (Test-Path -LiteralPath $NodeModules)) { $missing += "the frontend packages (frontend\node_modules)" }
    $envMissing = -not (Test-Path -LiteralPath $EnvFile)

    if ($missing.Count -eq 0 -and -not $envMissing) {
        Write-Ok "Dependencies and .env are in place."
        return
    }

    if ($missing.Count -gt 0) {
        Write-Caution ("Not installed yet: " + ($missing -join "; ") + ".")
        $tools = @()
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            $tools += "    Python 3.10 or newer: https://www.python.org/downloads/"
        }
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            $tools += "    Node.js 20 or newer: https://nodejs.org/"
        }
        if ($tools.Count -gt 0) {
            Fail-Launcher (@("First-time setup needs these installed first:") + $tools +
                @("Install them, then double-click Start CvScreener again."))
        }
        Write-Info "Setup runs .\tasks.ps1 install, which downloads the Python and"
        Write-Info "JavaScript packages CvScreener needs from the internet."
        if (-not (Read-YesNo "Run first-time setup now?")) {
            Fail-Launcher @(
                "First-time setup was not run, so CvScreener cannot start.",
                "When you are ready, run this in PowerShell from the repository folder:",
                "    .\tasks.ps1 install"
            )
        }
        & $Tasks install
        # install runs several commands and keeps going after a failed one, so
        # its exit code proves nothing. What it was meant to produce is checked.
        if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $NodeModules)) {
            Fail-Launcher @("First-time setup did not finish. The messages above say what went wrong.")
        }
        Write-Ok "Dependencies installed."
    }

    if ($envMissing) {
        Write-Caution "There is no .env file."
        Write-Info "One can be created by copying .env.example, whose defaults start"
        Write-Info "CvScreener in Demo mode. An existing .env is never overwritten."
        if (-not (Read-YesNo "Create .env from .env.example now?")) {
            Fail-Launcher @(
                "CvScreener needs a .env file to start.",
                "Create one in PowerShell from the repository folder:",
                "    Copy-Item .env.example .env"
            )
        }
        if (-not (Test-Path -LiteralPath $EnvFile)) {
            Copy-Item -LiteralPath $EnvExample -Destination $EnvFile
        }
        Write-Ok ".env created from .env.example."
    }
}

# --------------------------------------------------------------------------
# Start: 2. Docker Desktop
# --------------------------------------------------------------------------

function Test-DockerEngine([int]$TimeoutSeconds = $DockerCheckSeconds) {
    # `docker info` succeeds only when the engine answers. What it found is kept
    # for the message shown if Docker never becomes ready.
    $script:LastDockerCheck = Invoke-Docker -Arguments @("info") -TimeoutSeconds $TimeoutSeconds -Capture
    return ($script:LastDockerCheck.ExitCode -eq 0)
}

function Get-DockerProblem {
    # What the last `docker info` found, as a line for an error message.
    $check = $script:LastDockerCheck
    if (-not $check) { return @() }
    if ($check.TimedOut) {
        return @("Its engine is not responding: 'docker info' had no answer within $($check.Seconds) seconds.")
    }
    $said = @("$($check.Message)" -split "\r?\n" | Where-Object { $_.Trim() }) | Select-Object -First 1
    if ($said) { return @("Docker's last answer: $($said.Trim())") }
    return @()
}

function Start-DockerDesktop {
    Write-Step "Docker Desktop"
    if (-not (Add-DockerToPath)) {
        Fail-Launcher @(
            "Docker Desktop is not installed, and CvScreener's database runs in it.",
            "Install it from https://www.docker.com/products/docker-desktop/ and start it once,",
            "then double-click Start CvScreener again."
        )
    }
    if (Test-DockerEngine) {
        Write-Ok "Docker is running."
        return
    }
    $notReady = if ($script:LastDockerCheck.TimedOut) { "not answering" } else { "not running" }
    if (-not (Test-Path -LiteralPath $DockerDesktopExe)) {
        Fail-Launcher @(
            "Docker is $notReady, and Docker Desktop was not found at:",
            "    $DockerDesktopExe",
            "Start Docker Desktop yourself and wait until its engine is running,",
            "then double-click Start CvScreener again."
        )
    }
    if ($script:LastDockerCheck.TimedOut) {
        Write-Info "Docker did not answer within $DockerCheckSeconds seconds. Starting Docker Desktop and waiting for its engine"
    } else {
        Write-Info "Docker is not running. Starting Docker Desktop (this can take a minute or two)"
    }
    Start-Process -FilePath $DockerDesktopExe
    # Each check is also cut short at the overall deadline, so the wait as a
    # whole still ends after $DockerWaitSeconds.
    $ready = Wait-Until -Seconds $DockerWaitSeconds -Condition {
        param([int]$SecondsLeft)
        Test-DockerEngine ([Math]::Min($DockerCheckSeconds, $SecondsLeft))
    }
    if (-not $ready) {
        Fail-Launcher (@("Docker Desktop did not become ready within $DockerWaitSeconds seconds.") + (Get-DockerProblem) + @(
            "Open Docker Desktop and look for a sign-in, update or error message.",
            "Once it says the engine is running, double-click Start CvScreener again.",
            "The launcher did not stop, restart or reset Docker, and no data was touched."
        ))
    }
    Write-Ok "Docker is running."
}

# --------------------------------------------------------------------------
# Start: 3. PostgreSQL and 4. migrations
# --------------------------------------------------------------------------

function Start-Database($State) {
    Write-Step "Database (PostgreSQL in Docker)"
    $inspect = Invoke-Docker -Arguments @("inspect", "--format", "{{.State.Running}}", $DbContainer) -TimeoutSeconds $DockerCheckSeconds -Capture
    if ($inspect.TimedOut) {
        Fail-Launcher @(
            "Docker stopped answering: 'docker inspect' had no answer within $DockerCheckSeconds seconds.",
            "Open Docker Desktop and look for an error message.",
            "Once it says the engine is running, double-click Start CvScreener again.",
            "The launcher did not stop, restart or reset Docker, and no data was touched."
        )
    }
    $wasRunning = ($inspect.ExitCode -eq 0 -and ("$($inspect.Lines)").Trim() -eq "true")

    $dbUp = Invoke-TaskWithTimeout "db-up" $DatabaseStartSeconds
    if ($dbUp.TimedOut) {
        Fail-Launcher @(
            "The database did not start within $DatabaseStartSeconds seconds, so the launcher stopped waiting and",
            "ended its own 'docker compose up'. Docker, the container and its data were not touched.",
            "On a first start Docker may still have been downloading PostgreSQL. To watch that finish,",
            "run this in PowerShell from the repository folder, then double-click Start CvScreener again:",
            "    .\tasks.ps1 db-up",
            "Otherwise open Docker Desktop and look for an error message."
        )
    }
    if ($dbUp.ExitCode -ne 0) {
        Fail-Launcher @(
            "The database did not start. Docker's message is above.",
            "Nothing was deleted: the launcher never removes containers, volumes or data."
        )
    }

    # Remembered across runs: if an earlier Start brought the database up and it
    # has been running since, it is still the one the launcher started.
    $State.databaseStartedByLauncher = [bool]($State.databaseStartedByLauncher -or -not $wasRunning)
    Save-LauncherState $State
    if ($wasRunning) {
        Write-Ok "The database was already running."
    } else {
        Write-Ok "The database is running."
    }
}

function Get-AlembicRevision([string]$Command) {
    $result = Invoke-Capture -FilePath $Python -Arguments @("-m", "alembic", $Command) -WorkingDirectory $Backend
    foreach ($line in $result.Lines) {
        if ("$line" -match '^([0-9A-Za-z_]{4,})(\s|$)') { return $Matches[1] }
    }
    return $null
}

function Update-DatabaseSchema {
    Write-Step "Database schema (migrations)"
    $current = Get-AlembicRevision "current"
    $latest = Get-AlembicRevision "heads"
    if ($current -and $current -eq $latest) {
        Write-Info "Already at the latest revision ($current)."
    } else {
        $from = if ($current) { $current } else { "none" }
        Write-Info "Current revision: $from. Latest: $latest. Applying the pending migrations."
    }
    $global:LASTEXITCODE = 0
    & $Tasks migrate
    if ($LASTEXITCODE -ne 0) {
        Fail-Launcher @(
            "The migrations failed. The error is above.",
            "The backend was not started, so nothing ran against a half-updated database."
        )
    }
    Write-Ok "Schema is up to date."
}

# --------------------------------------------------------------------------
# Start: 5. the AI provider
# --------------------------------------------------------------------------

function Get-AiSettings {
    # Asked of the application's own settings loader, so the launcher follows
    # exactly the precedence the app does (environment variables, then .env,
    # then defaults) and never opens .env itself. Four values come back, and
    # none of them is a secret.
    $code = "from app.core.config import load_settings; s = load_settings(); " +
        "print('|'.join(str(v) for v in (s.demo_mode, s.llm_provider, s.ollama_base_url, s.ollama_model)))"
    $result = Invoke-Capture -FilePath $Python -Arguments @("-c", $code) -WorkingDirectory $Backend
    $line = @($result.Lines | Where-Object { "$_" -match '\|' }) | Select-Object -Last 1
    if ($result.ExitCode -ne 0 -or -not $line) {
        Fail-Launcher @(
            "CvScreener's configuration could not be loaded.",
            "To see why, run this in PowerShell from the repository folder:",
            "    .\tasks.ps1 check-llm --preflight"
        )
    }
    $parts = "$line".Split("|")
    return [pscustomobject]@{
        DemoMode  = ($parts[0] -eq "True")
        Provider  = $parts[1]
        OllamaUrl = $parts[2].TrimEnd("/")
        Model     = $parts[3]
    }
}

function Test-OllamaAnswers([string]$Url) {
    try {
        $null = Invoke-RestMethod -Uri "$Url/api/tags" -TimeoutSec 5
        return $true
    } catch {
        return $false
    }
}

function Test-ModelInstalled([string[]]$Installed, [string]$Wanted) {
    # The rule scripts/check_llm.py applies, so the launcher and the preflight
    # never disagree about whether a model is present.
    foreach ($name in $Installed) {
        if ($name -eq $Wanted -or $name -eq "$($Wanted):latest" -or $name.Split(":")[0] -eq $Wanted) {
            return $true
        }
    }
    return $false
}

function Start-Ollama([string]$Url) {
    $why = @(
        "",
        "CvScreener is set to Local AI mode (DEMO_MODE=false, LLM_PROVIDER=ollama).",
        "In that mode, screening a CV asks the local model to read it first -- even",
        "when every criterion is structured -- so no CV can be screened without Ollama."
    )
    $hostName = ([Uri]$Url).Host
    if ($hostName -notin @("localhost", "127.0.0.1", "::1", "[::1]")) {
        Fail-Launcher (@("Ollama is not answering at $Url.") + $why + @(
            "",
            "That address is not this computer, so the launcher cannot start Ollama there.",
            "Make sure it is running, then double-click Start CvScreener again."
        ))
    }
    if (-not (Test-Path -LiteralPath $OllamaTrayExe)) {
        Fail-Launcher (@("Ollama is not running, and it is not installed on this computer.") + $why + @(
            "",
            "Install it from https://ollama.com/download and start it once,",
            "then double-click Start CvScreener again."
        ))
    }
    $alreadyRunning = [bool](Get-Process -Name "ollama app", "ollama" -ErrorAction SilentlyContinue)
    if ($alreadyRunning) {
        # Running but not answering at this address yet: starting a second copy
        # would not help, so give the running one time instead.
        Write-Info "Ollama is running but not answering at $Url yet. Waiting"
    } else {
        Write-Info "Ollama is not running. Starting it"
        Start-Process -FilePath $OllamaTrayExe
    }
    # No GetNewClosure(): a closure cannot see this script's own functions. The
    # condition runs inside Wait-Until, called from here, so $Url resolves.
    $answers = Wait-Until -Seconds $OllamaWaitSeconds -Condition { Test-OllamaAnswers $Url }
    if (-not $answers -and $alreadyRunning) {
        Fail-Launcher (@("Ollama is running on this computer, but it is not answering at $Url.") + $why + @(
            "",
            "To fix it:",
            "  1. Check OLLAMA_BASE_URL. Ollama normally listens on http://localhost:11434.",
            "  2. Or quit Ollama from the system tray and open it again from the Start menu.",
            "  3. Double-click Start CvScreener again."
        ))
    }
    if (-not $answers) {
        Fail-Launcher (@("Ollama is not running, and CvScreener needs it.") + $why + @(
            "",
            "To fix it:",
            "  1. Open Ollama from the Start menu.",
            "  2. Wait until its icon appears in the system tray.",
            "  3. Double-click Start CvScreener again.",
            "",
            "CvScreener expects Ollama at $Url."
        ))
    }
}

function Request-ModelDownload([string]$Model) {
    Write-Caution "Ollama is running, but the model CvScreener is set to use is not installed:"
    Write-Info "    $Model"
    Write-Info "It can be downloaded with this command (a download of several gigabytes):"
    Write-Info "    ollama pull $Model"
    if (-not (Read-YesNo "Download it now?")) {
        Fail-Launcher @(
            "The model was not downloaded, and CvScreener cannot screen CVs without it.",
            "When you are ready, run this in PowerShell, then double-click Start CvScreener again:",
            "    ollama pull $Model"
        )
    }
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    $exe = $null
    if ($command) { $exe = $command.Source } elseif (Test-Path -LiteralPath $OllamaCliExe) { $exe = $OllamaCliExe }
    if (-not $exe) {
        Fail-Launcher @("The ollama command was not found. Run this yourself:", "    ollama pull $Model")
    }
    $global:LASTEXITCODE = 0
    & $exe pull $Model
    if ($LASTEXITCODE -ne 0) {
        Fail-Launcher @("The download did not finish. The message above says why. Retry with:", "    ollama pull $Model")
    }
    Write-Ok "$Model downloaded."
}

function Confirm-AiProvider {
    Write-Step "AI model"
    $settings = Get-AiSettings

    if ($settings.DemoMode) {
        Write-Ok "Demo mode is on (DEMO_MODE=true): AI answers are replayed from recordings, so no model is needed."
        return
    }

    if ($settings.Provider -eq "anthropic") {
        Write-Info "Cloud AI mode (LLM_PROVIDER=anthropic). Checking its configuration"
        $global:LASTEXITCODE = 0
        & $Tasks check-llm --preflight
        if ($LASTEXITCODE -ne 0) {
            Fail-Launcher @(
                "Cloud AI mode is not ready. The check above says what is missing.",
                "Fix it, then double-click Start CvScreener again."
            )
        }
        Write-Ok "Cloud AI mode is configured."
        return
    }

    if ($settings.Provider -ne "ollama") {
        Fail-Launcher @("LLM_PROVIDER is '$($settings.Provider)', which the launcher does not know how to check.")
    }

    $url = $settings.OllamaUrl
    if (-not (Test-OllamaAnswers $url)) {
        Start-Ollama $url
    }
    Write-Ok "Ollama is answering at $url."

    $tags = Invoke-RestMethod -Uri "$url/api/tags" -TimeoutSec 10
    $installed = @($tags.models | ForEach-Object { "$($_.name)" })
    if (-not (Test-ModelInstalled $installed $settings.Model)) {
        Request-ModelDownload $settings.Model
    }

    $global:LASTEXITCODE = 0
    & $Tasks check-llm --preflight
    if ($LASTEXITCODE -ne 0) {
        Fail-Launcher @("Ollama is not ready. The check above says what is missing.")
    }
    Write-Ok "$($settings.Model) is installed and ready."
}

# --------------------------------------------------------------------------
# Start: 6. backend, 7. frontend
# --------------------------------------------------------------------------

function Test-BackendHealthy {
    try {
        $health = Invoke-RestMethod -Uri "$BackendUrl/health" -TimeoutSec 3
        # Not merely "something answers on the port": CvScreener's own health
        # document, which carries these fields.
        return ($health.status -eq "ok" -and $null -ne $health.demo_mode -and $null -ne $health.llm_provider)
    } catch {
        return $false
    }
}

function Test-FrontendServing {
    try {
        $page = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 3
        return ($page.StatusCode -eq 200 -and $page.Content -match "<title>CvScreener</title>")
    } catch {
        return $false
    }
}

function Start-ServerWindow([string]$Title, [string]$Task) {
    $literalRoot = $Root.Replace("'", "''")
    $literalTasks = $Tasks.Replace("'", "''")
    $command = @(
        "`$Host.UI.RawUI.WindowTitle = '$Title'",
        "Write-Host 'CvScreener: this window runs .\tasks.ps1 $Task. Closing it stops this part of CvScreener.' -ForegroundColor Cyan",
        "Write-Host ''",
        "Set-Location -LiteralPath '$literalRoot'",
        "& '$literalTasks' $Task"
    ) -join [Environment]::NewLine
    # -EncodedCommand rather than a quoted -Command: the repository path can
    # contain spaces or apostrophes, and Start-Process does not quote arguments.
    # -NoExit keeps the window, and its last lines, open if the server stops.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -NoExit -EncodedCommand $encoded" `
        -WorkingDirectory $Root -WindowStyle Minimized -PassThru
}

function Start-Server($State, [string]$Key, [string]$Label, [int]$Port, [string]$Task, [int]$WaitSeconds, [scriptblock]$IsServing) {
    Write-Step "$Label (http://localhost:$Port)"
    if (& $IsServing) {
        if ($State.$Key -and (Find-RecordedProcess $State.$Key.window)) {
            Write-Ok "Already running."
        } else {
            Write-Ok "Already running, started outside the launcher, so Stop CvScreener will leave it alone."
        }
        return
    }

    $owner = Get-PortOwner $Port
    if ($owner) {
        Fail-Launcher @(
            "Port $Port is already in use by $($owner.ProcessName) (process id $($owner.Id)), which is not CvScreener's $($Label.ToLower()).",
            "The launcher does not stop programs it did not start.",
            "Close that program, then double-click Start CvScreener again."
        )
    }

    $title = "CvScreener - $($Label.ToLower())"
    $window = Start-ServerWindow -Title $title -Task $Task
    # Recorded before waiting, so Stop can still close the window if the server
    # never comes up.
    $State.$Key = [pscustomobject]@{ window = New-ProcessRecord $window; listener = $null }
    Save-LauncherState $State

    # The title is set, but it is not a promise: `npm run dev` runs through
    # cmd.exe, which retitles the frontend's window. The first line printed in
    # each window is what reliably names it.
    Write-Info "Started in a minimized window. Waiting for it to answer"
    if (-not (Wait-Until -Seconds $WaitSeconds -Condition $IsServing)) {
        Fail-Launcher @(
            "The $($Label.ToLower()) did not answer within $WaitSeconds seconds.",
            "Open its minimized window from the taskbar -- the first line reads",
            "'CvScreener: this window runs .\tasks.ps1 $Task' -- and its last lines say why."
        )
    }
    $State.$Key.listener = New-ProcessRecord (Get-PortOwner $Port)
    Save-LauncherState $State
    Write-Ok "$Label is answering."
}

# --------------------------------------------------------------------------
# Start and Stop
# --------------------------------------------------------------------------

function Invoke-Start {
    Write-Host "Starting CvScreener" -ForegroundColor Cyan
    Enter-SingleLauncher
    Test-FirstTimeSetup
    Start-DockerDesktop
    $state = Get-LauncherState
    Start-Database $state
    Update-DatabaseSchema
    Confirm-AiProvider
    Start-Server $state "backend" "Backend" $BackendPort "dev-backend" $BackendWaitSeconds { Test-BackendHealthy }
    Start-Server $state "frontend" "Frontend" $FrontendPort "dev-frontend" $FrontendWaitSeconds { Test-FrontendServing }

    Write-Step "Opening $FrontendUrl"
    Start-Process $FrontendUrl

    Write-Host ""
    Write-Host "CvScreener is running." -ForegroundColor Green
    Write-Info "App:       $FrontendUrl"
    Write-Info "API docs:  $BackendUrl/docs"
    foreach ($part in @(@{ Key = "backend"; Label = "Backend"; Task = "dev-backend" }, @{ Key = "frontend"; Label = "Frontend"; Task = "dev-frontend" })) {
        if ($state.($part.Key)) {
            Write-Info ("{0,-10} logs in a minimized window whose first line reads 'CvScreener: this window runs .\tasks.ps1 {1}'" -f "$($part.Label):", $part.Task)
        } else {
            Write-Info ("{0,-10} started outside the launcher; its logs are in the window it was started from" -f "$($part.Label):")
        }
    }
    if ($state.backend -or $state.frontend) {
        Write-Info "To stop:   double-click Stop CvScreener (your data is kept)"
    } else {
        Write-Info "To stop:   close the windows they were started from (Stop CvScreener stops only what it started)"
    }
}

function Stop-RecordedServer($State, [string]$Key, [string]$Label, [int]$Port) {
    Write-Step $Label
    $entry = $State.$Key
    $stopped = $false
    if ($entry) {
        foreach ($which in "window", "listener") {
            $process = Find-RecordedProcess $entry.$which
            if ($process) {
                # /T takes the process tree -- the window's PowerShell and the
                # server it runs -- and nothing outside that tree.
                Invoke-Quiet "taskkill.exe" @("/PID", [string]$process.Id, "/T", "/F") | Out-Null
                $stopped = $true
            }
        }
    }
    if ($stopped) {
        $portNumber = $Port
        Wait-Until -Seconds 15 -Condition { -not (Get-PortOwner $portNumber) } | Out-Null
    }
    $State.$Key = $null

    $owner = Get-PortOwner $Port
    if ($owner) {
        Write-Caution "Port $Port is still in use by $($owner.ProcessName) (process id $($owner.Id))."
        Write-Info "The launcher did not start that process, so it was left running."
    } elseif ($stopped) {
        Write-Ok "Stopped."
    } else {
        Write-Ok "Was not running from the launcher."
    }
    return [bool]$owner
}

function Invoke-Stop {
    Write-Host "Stopping CvScreener" -ForegroundColor Cyan
    Enter-SingleLauncher
    $state = Get-LauncherState

    $frontendStillUp = Stop-RecordedServer $state "frontend" "Frontend" $FrontendPort
    $backendStillUp = Stop-RecordedServer $state "backend" "Backend" $BackendPort

    Write-Step "Database"
    $databaseLeft = $false
    if ($state.databaseStartedByLauncher) {
        if ((Add-DockerToPath) -and (Test-DockerEngine)) {
            # stop, never down: the container and its data volume both remain.
            $stopDb = Invoke-Docker -Arguments @("compose", "-f", $Compose, "stop", "db") -TimeoutSeconds $DatabaseStopSeconds
            if ($stopDb.TimedOut) {
                Write-Caution "Docker did not finish stopping the database within $DatabaseStopSeconds seconds."
                $databaseLeft = $true
            } elseif ($stopDb.ExitCode -eq 0) {
                Write-Ok "Stopped. The container and its data volume are kept."
            } else {
                Write-Caution "Docker could not stop the database. Its message is above; no data was touched."
            }
        } elseif ($script:LastDockerCheck -and $script:LastDockerCheck.TimedOut) {
            Write-Caution "Docker did not answer within $DockerCheckSeconds seconds, so the database could not be stopped."
            $databaseLeft = $true
        } else {
            Write-Ok "Docker is not running, so neither is the database."
        }
        # A database Docker never got to stop is still the launcher's: the
        # record stays, so the next Stop tries again.
        if (-not $databaseLeft) { $state.databaseStartedByLauncher = $false }
    } else {
        Write-Ok "Left running: the launcher did not start it."
    }

    if (-not $state.backend -and -not $state.frontend -and -not $state.databaseStartedByLauncher) {
        if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile }
    } else {
        Save-LauncherState $state
    }

    if ($databaseLeft) {
        Fail-Launcher @(
            "The database was not stopped, because Docker did not answer in time. No data was touched.",
            "Open Docker Desktop and look for an error message. Once it says the engine is running,",
            "double-click Stop CvScreener again to stop the database."
        )
    }

    Write-Host ""
    if ($frontendStillUp -or $backendStillUp) {
        Write-Host "Everything the launcher started is stopped." -ForegroundColor Green
        Write-Info "Something the launcher did not start is still using a CvScreener port (see above)."
        Write-Info "Close it from the window it was started in."
    } else {
        Write-Host "CvScreener is stopped." -ForegroundColor Green
    }
    Write-Info "Docker Desktop and Ollama were left running, since other programs may use them."
    Write-Info "Quit them from the system tray if you want them closed."
}

$exitCode = 0
try {
    if ($Stop) { Invoke-Stop } else { Invoke-Start }
} catch {
    Write-Host ""
    if ($_.Exception.Data["CvScreenerLauncher"]) {
        Write-Host $_.Exception.Message -ForegroundColor Red
    } else {
        Write-Host "The launcher stopped on an unexpected error:" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor DarkGray
    }
    $exitCode = 1
}

Write-Host ""
try { $null = Read-Host "Press Enter to close this window" } catch { }
exit $exitCode
