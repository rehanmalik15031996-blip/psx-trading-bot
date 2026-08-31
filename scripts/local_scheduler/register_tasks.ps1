# Registers Windows Scheduled Tasks that run this repo's data pipeline
# locally, on the same cadence as .github/workflows/*.yml (all times below
# are PKT, which matches this machine's local clock).
#
# Why: GitHub Actions' automated runs depend on secrets and a CI environment
# that have both broken independently (invalid ANTHROPIC_API_KEY, missing
# `rich` dependency -- see reports/master_strategist_2026-09-01.md). Running
# the same scripts here on a schedule keeps data moving even when GitHub
# Actions itself is degraded, and uses whichever local .env keys are valid.
#
# This does NOT replace GitHub Actions -- both can run; each pushes to the
# same origin/main, and either one commits/pushes only when there's an
# actual diff, so duplicate runs are harmless.
#
# Uses the ScheduledTasks PowerShell module (not schtasks.exe) so the
# executable path and arguments are passed as separate, properly-typed
# parameters instead of a single hand-quoted command-line string.
#
# Monthly-cadence workflows (ingest_mf_holdings, ingest_amc_fmr,
# validate_ranker) are registered as DAILY triggers at the right time; the
# day-of-month/quarter gate lives inside run_workflow.py itself, since this
# machine's ScheduledTasks module has no native monthly trigger type.
#
# Usage (run from a normal PowerShell prompt in this repo -- no admin needed):
#     powershell -ExecutionPolicy Bypass -File scripts\local_scheduler\register_tasks.ps1
#
# Tasks run only while this user is logged on (the module default). To run
# unattended even when logged off, edit a task in Task Scheduler GUI and
# check "Run whether user is logged on or not" (prompts for your Windows
# password once, stored securely by Windows -- not something this script
# does for you).

$ErrorActionPreference = "Stop"

$RepoRoot  = (Resolve-Path "$PSScriptRoot\..\..").Path
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunnerPy  = Join-Path $RepoRoot "scripts\local_scheduler\run_workflow.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- create the venv first."
}
if (-not (Test-Path $RunnerPy)) {
    throw "run_workflow.py not found at $RunnerPy"
}

function Register-PSXTask {
    param(
        [string]$TaskName,
        [string]$Workflow,
        [string]$Kind,        # Weekly | Daily
        [string[]]$Days,      # DayOfWeek names, only used when Kind=Weekly
        [string]$Time         # HH:mm
    )
    $fullName = "PSX_$TaskName"
    $argument = '"' + $RunnerPy + '" --workflow ' + $Workflow
    $action  = New-ScheduledTaskAction -Execute $PythonExe -Argument $argument -WorkingDirectory $RepoRoot
    $atTime  = [datetime]::ParseExact($Time, "HH:mm", $null)

    if ($Kind -eq "Weekly") {
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At $atTime
    } else {
        $trigger = New-ScheduledTaskTrigger -Daily -At $atTime
    }

    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

    Write-Host "Registering $fullName -> $Workflow @ $Kind $($Days -join ',') $Time"
    Register-ScheduledTask -TaskName $fullName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}

$WEEKDAYS = @("Monday","Tuesday","Wednesday","Thursday","Friday")

# --- Weekday jobs (times are PKT = this machine's local time) ---
Register-PSXTask -TaskName "macro_series"        -Workflow "macro_series"        -Kind Weekly -Days $WEEKDAYS -Time "06:55"
Register-PSXTask -TaskName "news_scoring_1"      -Workflow "news_scoring"        -Kind Weekly -Days $WEEKDAYS -Time "07:00"
Register-PSXTask -TaskName "macro_kpis_1"        -Workflow "macro_kpis"          -Kind Weekly -Days $WEEKDAYS -Time "08:30"
Register-PSXTask -TaskName "health_check_1"      -Workflow "health_check"        -Kind Weekly -Days $WEEKDAYS -Time "08:40"
Register-PSXTask -TaskName "overnight"           -Workflow "overnight"           -Kind Weekly -Days $WEEKDAYS -Time "09:00"
Register-PSXTask -TaskName "predictions"         -Workflow "predictions"         -Kind Weekly -Days $WEEKDAYS -Time "09:20"
Register-PSXTask -TaskName "master_strategist"   -Workflow "master_strategist"   -Kind Weekly -Days $WEEKDAYS -Time "09:30"
Register-PSXTask -TaskName "intraday_session_1"  -Workflow "intraday_session"    -Kind Weekly -Days $WEEKDAYS -Time "11:30"
Register-PSXTask -TaskName "news_scoring_2"      -Workflow "news_scoring"        -Kind Weekly -Days $WEEKDAYS -Time "13:00"
Register-PSXTask -TaskName "intraday_session_2"  -Workflow "intraday_session"    -Kind Weekly -Days $WEEKDAYS -Time "13:30"
Register-PSXTask -TaskName "eod"                 -Workflow "eod"                 -Kind Weekly -Days $WEEKDAYS -Time "16:30"
Register-PSXTask -TaskName "health_check_2"      -Workflow "health_check"        -Kind Weekly -Days $WEEKDAYS -Time "16:05"
Register-PSXTask -TaskName "macro_kpis_2"        -Workflow "macro_kpis"          -Kind Weekly -Days $WEEKDAYS -Time "17:00"
Register-PSXTask -TaskName "material_info"       -Workflow "material_info"       -Kind Weekly -Days $WEEKDAYS -Time "17:30"
Register-PSXTask -TaskName "news_scoring_3"      -Workflow "news_scoring"        -Kind Weekly -Days $WEEKDAYS -Time "18:00"

# --- Weekly single-day jobs ---
Register-PSXTask -TaskName "financial_results"   -Workflow "financial_results"   -Kind Weekly -Days @("Saturday") -Time "11:00"
Register-PSXTask -TaskName "fundamentals"        -Workflow "fundamentals"        -Kind Weekly -Days @("Sunday")   -Time "07:00"

# --- Monthly / quarterly jobs (daily trigger; gated inside run_workflow.py) ---
Register-PSXTask -TaskName "ingest_mf_holdings"  -Workflow "ingest_mf_holdings"  -Kind Daily -Time "09:00"
Register-PSXTask -TaskName "ingest_amc_fmr"      -Workflow "ingest_amc_fmr"      -Kind Daily -Time "09:00"
Register-PSXTask -TaskName "validate_ranker"     -Workflow "validate_ranker"     -Kind Daily -Time "08:30"
Register-PSXTask -TaskName "fundamentals_history" -Workflow "fundamentals_history" -Kind Daily -Time "07:30"

Write-Host ""
Write-Host "Done. List all registered tasks with:"
Write-Host '  Get-ScheduledTask -TaskName "PSX_*" | Select-Object TaskName, State'
Write-Host "Logs land in logs\local_scheduler\<workflow>_<timestamp>.log"
