# Registers the monitor as its own task, separate from the pipeline task.
#     powershell -ExecutionPolicy Bypass -File ops\install_monitor_task.ps1
# Export for the repository:  schtasks /query /tn FlightWeatherMonitor /xml > ops\task_scheduler_monitor.xml
$repo = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "$repo\ops\monitor.bat" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At "00:12" -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "FlightWeatherMonitor" -Action $action -Trigger $trigger -Settings $settings -Force
