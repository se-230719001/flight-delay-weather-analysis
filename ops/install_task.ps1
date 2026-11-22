$repo = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "$repo\ops\run_pipeline.bat" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At "00:07" -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "FlightWeatherPipeline" -Action $action -Trigger $trigger -Settings $settings -Force