# Wrapper invoked by Task Scheduler.
# Adds jitter so runs do not land on a perfectly regular cadence, and keeps the
# tool idle overnight where a listing appearing at 3am will still be there at 7.

param(
    [string]$ProjectDir = "C:\MarketSearch",
    [int]$MaxJitterSeconds = 1800,
    [int]$ActiveStartHour = 7,
    [int]$ActiveEndHour = 22
)

$hour = (Get-Date).Hour
if ($hour -lt $ActiveStartHour -or $hour -ge $ActiveEndHour) {
    Write-Output "Outside active hours ($ActiveStartHour-$ActiveEndHour); skipping."
    exit 0
}

Start-Sleep -Seconds (Get-Random -Minimum 0 -Maximum $MaxJitterSeconds)

Set-Location $ProjectDir
& "$ProjectDir\.venv\Scripts\marketsearch.exe" run
exit $LASTEXITCODE
