# Relocate Salvage Radar state out of the AppContainer redirect zone.
#
# WHY: Claude Code's terminal runs in a Windows AppContainer that
# redirects writes to %LOCALAPPDATA% to its private overlay store
# (C:\Users\...\AppData\Local\Packages\Claude_*\LocalCache\Local\...).
# Scheduled tasks (ClWatcher, ClAppraiser) fire OUTSIDE that
# AppContainer and see the real %LOCALAPPDATA% path, which is empty.
# Result: the watcher / appraiser scheduled tasks can't find state.db.
#
# FIX: relocate state.db + appraisal.db to ~\salvage-radar\, which is
# NOT inside any AppContainer redirect zone, so both contexts see the
# same file. The codebase already supports this via the
# SALVAGE_RADAR_STATE_DIR env var.
#
# USAGE: open a NORMAL PowerShell window (NOT through Claude Code),
# then:
#   cd "C:\Users\User\OneDrive\Desktop\Claude Project\cl_watcher"
#   .\relocate_state.ps1

$ErrorActionPreference = "Stop"

$newDir = Join-Path $env:USERPROFILE "salvage-radar"
$oldOverlay = Join-Path $env:LOCALAPPDATA "Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\cl_watcher"
$oldReal = Join-Path $env:LOCALAPPDATA "cl_watcher"

Write-Host "New state dir:        $newDir"
Write-Host "AppContainer overlay: $oldOverlay"
Write-Host "Real %LOCALAPPDATA%:  $oldReal"
Write-Host ""

New-Item -ItemType Directory -Force -Path $newDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $newDir "appraiser") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $newDir "appraiser\log") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $newDir "log") | Out-Null

# Pick the best source: prefer overlay (newer, has all the data Claude
# Code wrote) if it has state.db; otherwise fall back to the real path.
$src = $null
if (Test-Path (Join-Path $oldOverlay "state.db")) {
    $src = $oldOverlay
    Write-Host "Source: AppContainer overlay (has the live data)"
} elseif (Test-Path (Join-Path $oldReal "state.db")) {
    $src = $oldReal
    Write-Host "Source: real %LOCALAPPDATA% (overlay was empty)"
} else {
    Write-Warning "No state.db found in either source. Nothing to migrate."
    Write-Host ""
    Write-Host "Just set the env var and run cl_watcher to populate fresh:"
    Write-Host "  setx SALVAGE_RADAR_STATE_DIR `"$newDir`""
    exit 0
}

$files = @("state.db",
           ".env",
           "appraiser\appraisal.db",
           "appraiser\embed_cache.db",
           "appraiser\comps_cache.db")

foreach ($f in $files) {
    $s = Join-Path $src $f
    $d = Join-Path $newDir $f
    if (Test-Path $s) {
        $size = (Get-Item $s).Length
        Copy-Item -Path $s -Destination $d -Force
        Write-Host ("  Copied {0,-32} ({1:N0} bytes)" -f $f, $size)
    }
}

Write-Host ""
Write-Host "Setting SALVAGE_RADAR_STATE_DIR for the user..."
[System.Environment]::SetEnvironmentVariable(
    "SALVAGE_RADAR_STATE_DIR", $newDir, "User")
Write-Host "  done. (You may need to log out / back in for new processes to see this.)"
Write-Host ""
Write-Host "Re-register the scheduled tasks so they pick up the new env:"
Write-Host "  Unregister-ScheduledTask -TaskName ClWatcher -Confirm:`$false"
Write-Host "  Unregister-ScheduledTask -TaskName ClAppraiser -Confirm:`$false"
Write-Host "  .\register_task.ps1"
Write-Host "  cd ..\appraiser; .\register_task.ps1"
Write-Host ""
Write-Host "Done. State now lives at $newDir."
