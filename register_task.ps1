$ErrorActionPreference = "Stop"

$projectDir = "C:\Users\User\OneDrive\Desktop\Claude Project\cl_watcher"
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
$script = Join-Path $projectDir "watcher.py"
$wrapper = Join-Path $projectDir "run_cycle.ps1"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found at $python. From the project dir, run: python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "watcher.py not found at $script."
    exit 1
}
if (-not (Test-Path $wrapper)) {
    Write-Error "run_cycle.ps1 wrapper not found at $wrapper."
    exit 1
}

# Use the wrapper so the appraiser fires immediately when the scraper
# exits (chained via Start-ScheduledTask inside the wrapper). ClAppraiser
# still has its own 5-min trigger as a failsafe in case ClWatcher fails
# or skips a cycle.
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"" + $wrapper + "`"") `
    -WorkingDirectory $projectDir

# Tight cadence (5 min) for testing — was 15 min in production. The
# scraper itself typically completes in <30 s, so 5 min leaves plenty of
# headroom for the appraiser to fire a minute later and still finish.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 90) `
    -MultipleInstances IgnoreNew `
    -WakeToRun:$false `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "ClWatcher" `
    -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Craigslist Vancouver robotics-parts watcher (every 5 min, testing)" `
    -Force | Out-Null

Write-Host "Registered scheduled task 'ClWatcher'."
Write-Host "Run now:  Start-ScheduledTask -TaskName ClWatcher"
Write-Host "Pause:    Disable-ScheduledTask -TaskName ClWatcher"
Write-Host "Resume:   Enable-ScheduledTask  -TaskName ClWatcher"
Write-Host "Remove:   Unregister-ScheduledTask -TaskName ClWatcher -Confirm:`$false"
