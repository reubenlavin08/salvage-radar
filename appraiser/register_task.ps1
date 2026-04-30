$ErrorActionPreference = "Stop"

# Registers a Windows Task Scheduler entry that runs the appraiser cycle
# every 15 minutes, offset by 5 min from cl_watcher so the watcher has a
# chance to insert any new listings before we try to appraise them.

$projectDir = "C:\Users\User\OneDrive\Desktop\Claude Project\appraiser"
$script = Join-Path $projectDir "run_cycle.ps1"

if (-not (Test-Path $script)) {
    Write-Error "run_cycle.ps1 not found at $script."
    exit 1
}

# Run cycle.ps1 via PowerShell with execution policy bypass so the
# scheduled-task host doesn't refuse a script that wasn't signed.
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $projectDir

# Offset 5 minutes from cl_watcher's :00/:15/:30/:45 cadence so we run
# at :05/:20/:35/:50, after the watcher has inserted that cycle's rows.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
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
    -Description "Salvage appraiser — runs every 15 min on new cl_watcher listings only" `
    -Force | Out-Null

Write-Host "Registered scheduled task 'ClAppraiser'."
Write-Host "Run now:  Start-ScheduledTask -TaskName ClAppraiser"
Write-Host "Pause:    Disable-ScheduledTask -TaskName ClAppraiser"
Write-Host "Resume:   Enable-ScheduledTask  -TaskName ClAppraiser"
Write-Host "Remove:   Unregister-ScheduledTask -TaskName ClAppraiser -Confirm:`$false"
Write-Host ""
Write-Host "Logs: %LOCALAPPDATA%\cl_watcher\appraiser\log\cycle.log"
