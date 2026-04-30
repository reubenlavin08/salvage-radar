# Wrapper invoked by ClWatcher's scheduled task. Runs the Python scraper,
# then immediately triggers ClAppraiser so the appraiser fires the moment
# the indexer finishes (instead of waiting for ClAppraiser's own next slot).
#
# Why this exists: the user wants strict ordering (index → appraise) every
# cycle. Two independent scheduled tasks with a fixed offset can't promise
# that — if the scraper takes longer than the offset, the appraiser starts
# on stale data; if shorter, the appraiser sits idle until its own next
# trigger. Chaining via Start-ScheduledTask gives us "kick the next stage
# the instant this one exits".

$ErrorActionPreference = "Continue"

$watcher = Split-Path -Parent $MyInvocation.MyCommand.Definition
$python = Join-Path $watcher ".venv\Scripts\python.exe"
$logDir = Join-Path $env:LOCALAPPDATA "cl_watcher\log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "wrapper.log"

function Log($msg) {
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    "$ts $msg" | Out-File -FilePath $log -Append -Encoding utf8
}

Log "=== watcher cycle start ==="

Set-Location $watcher
$pyOut = & $python watcher.py 2>&1 | Out-String
$pyExit = $LASTEXITCODE
Log "python exit=$pyExit"
if ($pyOut.Trim().Length -gt 0) { Log $pyOut }

# Kick the appraiser regardless of watcher exit code — even on partial
# failure there may be enough new rows in the DB to make the appraiser
# worth running. The appraiser's own prepare.py exits cleanly when
# there's nothing new to do, so this is safe.
Log "triggering ClAppraiser..."
try {
    Start-ScheduledTask -TaskName "ClAppraiser" -ErrorAction Stop
    Log "ClAppraiser kicked off"
} catch {
    Log ("Start-ScheduledTask failed: " + $_.Exception.Message)
}

Log "=== watcher cycle end ==="
