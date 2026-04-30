# Appraiser cycle runner — invoked by Windows Task Scheduler every 15 min.
#
# What it does
# ------------
# 1. Runs prepare.py with the only-unappraised default. New listings that
#    cl_watcher fetched since the last cycle become a fresh set of batch
#    JSON files in appraiser/batches/.
# 2. If there are 0 new listings, exits silently — no work to do.
# 3. Otherwise invokes Claude Code in headless mode (`claude -p /appraise`)
#    which spawns parallel subagents to appraise each batch, then runs
#    aggregate.py to write into appraisal.db.
# 4. Logs everything to %LOCALAPPDATA%\cl_watcher\appraiser\log\cycle.log.
#
# Requirements
# ------------
# - Python venv at appraiser/.venv (created during initial setup)
# - `claude` CLI on PATH and already authenticated for the user's
#   subscription. Install: https://docs.claude.com/claude-code
# - cl_watcher's state.db populated and updating (cl_watcher's own
#   scheduled task continues to run on its own 15-min cadence)

# IMPORTANT: keep this as "Continue", NOT "Stop". Under "Stop", merging
# native-command stderr (2>&1) wraps every Python `INFO:` log line as a
# NativeCommandError and halts the script before we even reach the first
# Log call. Under "Continue", merged streams flow through as plain text
# and we use $LASTEXITCODE to detect actual failures.
$ErrorActionPreference = "Continue"

$appraiser = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $appraiser
$python = Join-Path $appraiser ".venv\Scripts\python.exe"
$logDir = Join-Path $env:LOCALAPPDATA "cl_watcher\appraiser\log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "cycle.log"

function Log($msg) {
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    "$ts $msg" | Out-File -FilePath $log -Append -Encoding utf8
}

function FailExit($msg) {
    Log "ERROR: $msg"
    Log "=== cycle end (failed) ==="
    exit 1
}

Log "=== cycle start ==="

try {
    # 1. Prepare batches from new (unappraised) listings only.
    Set-Location $appraiser
    $prepareJson = & $python prepare.py 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Log "prepare exit=$LASTEXITCODE; output:"
        Log $prepareJson
        FailExit "prepare.py failed"
    }
    Log "prepare: $prepareJson"

    # Parse the JSON summary on the last line that starts with "{" to find
    # how many new listings were prepared.
    $kept = 0
    try {
        $obj = ($prepareJson | Select-String -Pattern '"kept":\s*(\d+)' -AllMatches)
        if ($obj.Matches.Count -gt 0) {
            $kept = [int]$obj.Matches[0].Groups[1].Value
        }
    } catch { $kept = 0 }

    if ($kept -eq 0) {
        Log "no new listings; skipping appraiser invocation"
        Log "=== cycle end (no work) ==="
        exit 0
    }

    Log "kept=$kept; invoking claude -p /appraise"

    # 2. Invoke Claude Code headlessly. The /appraise slash command lives
    # in .claude/commands/appraise.md and orchestrates the subagent(s).
    Set-Location $projectRoot
    $claude = (Get-Command claude -ErrorAction SilentlyContinue)
    if (-not $claude) {
        FailExit "'claude' CLI not found on PATH"
    }

    $claudeOutput = & claude -p "/appraise 1 100" 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Log "claude exit=$LASTEXITCODE; output:"
        Log $claudeOutput
        # Don't FailExit — try to aggregate any partial results below.
    } else {
        Log "claude: $claudeOutput"
    }

    # 3. Aggregate (idempotent — safe even if /appraise already aggregated).
    Set-Location $appraiser
    $aggregateJson = & $python aggregate.py --top 10 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Log "aggregate exit=$LASTEXITCODE; output:"
        Log $aggregateJson
        FailExit "aggregate.py failed"
    }
    Log "aggregate: $aggregateJson"

    Log "=== cycle end ==="
} catch {
    Log "UNHANDLED EXCEPTION: $($_.Exception.Message)"
    Log $_.ScriptStackTrace
    Log "=== cycle end (exception) ==="
    exit 1
}
