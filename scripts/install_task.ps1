# Registers the scheduled task. Run once, from an elevated PowerShell.

param(
    [string]$ProjectDir = "C:\MarketSearch",
    [string]$TaskName = "MarketSearch"
)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ProjectDir\scripts\run_marketsearch.ps1`" -ProjectDir `"$ProjectDir`""

# Fires every 30 minutes; the wrapper adds up to 30 minutes of jitter on top,
# producing an effective 30-60 minute cadence.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Limited -Force

Write-Output "Registered '$TaskName'. Verify with: Get-ScheduledTask -TaskName $TaskName"
