$ErrorActionPreference = "Stop"

$projectDir = "C:\Users\User\OneDrive\Desktop\Claude Project\appraiser"
$script = Join-Path $projectDir "run_cycle.ps1"

if (-not (Test-Path $script)) {
    Write-Error "run_cycle.ps1 not found at $script."
    exit 1
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"" + $script + "`"") `
    -WorkingDirectory $projectDir

# Anchor the appraiser 3 minutes after the scraper, so each cycle is
# guaranteed to run AFTER cl_watcher has finished pulling new listings.
# We read ClWatcher's StartBoundary directly so this stays in sync if the
# scraper schedule ever shifts.
$watcher = Get-ScheduledTask -TaskName "ClWatcher" -ErrorAction SilentlyContinue
if ($watcher -and $watcher.Triggers[0].StartBoundary) {
    $watcherStart = [DateTime]$watcher.Triggers[0].StartBoundary
    $startAt = $watcherStart.AddMinutes(3)
    Write-Host ("Anchoring to ClWatcher StartBoundary {0} + 3 min = {1}" -f $watcherStart, $startAt)
} else {
    # Fallback: if cl_watcher's task isn't registered yet, just start in 3 min.
    $startAt = (Get-Date).AddMinutes(3)
    Write-Host "ClWatcher task not found; using fallback start time $startAt"
}

$trigger = New-ScheduledTaskTrigger -Once -At $startAt `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -WakeToRun:$false `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "ClAppraiser" `
    -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Salvage appraiser - runs 3 min after each ClWatcher cycle, on new listings only (1 agent)" `
    -Force | Out-Null

Write-Host "Registered scheduled task 'ClAppraiser'."
Write-Host "Run now:  Start-ScheduledTask -TaskName ClAppraiser"
Write-Host "Pause:    Disable-ScheduledTask -TaskName ClAppraiser"
Write-Host "Resume:   Enable-ScheduledTask -TaskName ClAppraiser"
Write-Host 'Remove:   Unregister-ScheduledTask -TaskName ClAppraiser -Confirm:$false'
Write-Host ('Logs: ' + $env:LOCALAPPDATA + '\cl_watcher\appraiser\log\cycle.log')
