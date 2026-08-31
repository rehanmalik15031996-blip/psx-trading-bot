# Removes every Windows Scheduled Task registered by register_tasks.ps1.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\local_scheduler\unregister_tasks.ps1

Get-ScheduledTask -TaskName "PSX_*" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Removing $($_.TaskName)"
    Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
}
Write-Host "Done."
