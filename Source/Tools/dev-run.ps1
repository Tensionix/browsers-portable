# A check of the window without a mouse: closes the Debug copy of THIS project (picked by the
# full path of its exe - never by the name of the process: the owner's own copy may be open),
# builds, starts it again with a script of steps and waits for it to close.
#
#   Source\Tools\dev-run.ps1 -Dev "idle;tab=update;shot=C:\Temp\window.png;close"
#   Source\Tools\dev-run.ps1 -Reset -Stand C:\Temp\stand -Dev "idle;run=cleanup_workspace;answer=yes;idle;close" -Wait 120
#
# The steps are read by MainWindow.Dev.cs (Debug builds only). A step that runs an operation or
# clears a folder works only on a stand: -Stand names a folder that becomes a project of the
# check's own - its config (a copy of the manifest and of the version), its input, output, logs
# and report. The engine is the program's; nothing of the owner's is read or written by such a
# step. Without -Stand the window refuses those steps itself.
# -Reset removes the settings of the Debug copy (bin\Debug\net10.0\config), so the window opens
# in its default size and theme. The Debug copy keeps its settings and its logs beside itself:
# the copy the owner works in is not touched. The lines of the journal of the window the run
# wrote are printed at the end.
param(
    [string]$Dev = '',
    [int]$Wait = 30,
    [switch]$NoBuild,
    [switch]$NoStart,
    [string]$Misses = '',
    [string]$Stand = '',
    [switch]$Reset,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $PSScriptRoot
$project = Split-Path -Parent $source
$bin = Join-Path $source 'bin\Debug\net10.0'
$exe = Join-Path $bin 'AudionBrowsersPortable.exe'

$mine = @(Get-Process | Where-Object { $_.Path -eq $exe })
# A window with work in it is not closed: an operation started by it is its child process.
# -Force closes it all the same.
if ($mine.Count -and -not $Force) {
    $ids = @($mine | ForEach-Object { $_.Id })
    $busy = @(Get-CimInstance Win32_Process | Where-Object { $ids -contains $_.ParentProcessId -and $_.Name -eq 'python.exe' })
    if ($busy.Count) {
        'the window is at work ({0} operation) - not closed, nothing built' -f $busy.Count
        exit 2
    }
}
foreach ($p in $mine) { [void]$p.CloseMainWindow() }
if ($mine.Count) {
    Start-Sleep -Milliseconds 1500
    Get-Process | Where-Object { $_.Path -eq $exe } | ForEach-Object { $_.Kill() }
    Start-Sleep -Milliseconds 400
}
if ($Reset) {
    Get-ChildItem (Join-Path $bin 'config') -Filter 'window.json*' -ErrorAction SilentlyContinue | Remove-Item -Force
}
if (-not $NoBuild) {
    $out = & dotnet build (Join-Path $source 'AudionBrowsersPortable.csproj') -c Debug --nologo -v q 2>&1 | Out-String
    $bad = @($out -split "`r?`n" | Where-Object { $_ -match 'error|warning CS|warning AVLN' } |
        ForEach-Object { ($_ -replace ' \[[A-Z]:.*', '').Trim() } | Sort-Object -Unique)
    if ($bad.Count) { $bad[0..([Math]::Min($bad.Count, 25) - 1)]; if ($out -match 'error') { exit 1 } }
    else { 'build ok' }
}
if ($NoStart) { exit 0 }

if ($Stand) {
    # The stand is a project folder with nothing but its config: the door makes the rest itself.
    $standConfig = Join-Path $Stand 'config'
    [void](New-Item -ItemType Directory -Force -Path $standConfig)
    Copy-Item -LiteralPath (Join-Path $project 'config\tool_manifest.yaml') -Destination $standConfig -Force
    Copy-Item -LiteralPath (Join-Path $project 'config\version.json') -Destination $standConfig -Force
    $Stand = (Resolve-Path -LiteralPath $Stand).Path
    if ($Stand.TrimEnd('\') -eq $project.TrimEnd('\')) { 'the stand must not be the program itself'; exit 2 }
}

$log = Join-Path $bin ('logs\gui-{0}.log' -f (Get-Date -Format 'yyyyMMdd'))
$before = if (Test-Path -LiteralPath $log) { @(Get-Content -LiteralPath $log -Encoding UTF8).Count } else { 0 }

# AUDION_I18N_MISSES collects every Russian text the dictionary left untranslated (Core\Lang.cs).
if ($Misses) { $env:AUDION_I18N_MISSES = $Misses; if (Test-Path $Misses) { Remove-Item $Misses -Force } }
$env:AUDION_DEV_SCRIPT = $Dev
$env:AUDION_DEV_PROJECT = $Stand
# The history of paths of the check is its own file beside the Debug copy, never the project's.
$env:AUDION_DEV_HISTORY = Join-Path $bin 'config\path_history.dev.json'
if ($Reset -and (Test-Path -LiteralPath $env:AUDION_DEV_HISTORY)) { Remove-Item -LiteralPath $env:AUDION_DEV_HISTORY -Force }
$process = Start-Process -FilePath $exe -PassThru
$env:AUDION_DEV_SCRIPT = $null
$env:AUDION_DEV_PROJECT = $null
$env:AUDION_DEV_HISTORY = $null
$env:AUDION_I18N_MISSES = $null

$closed = $process.WaitForExit($Wait * 1000)
'window: {0}' -f $(if ($closed) { 'closed, exit code ' + $process.ExitCode } else { "still open after $Wait s" })
if (Test-Path -LiteralPath $log) {
    $lines = @(Get-Content -LiteralPath $log -Encoding UTF8)
    if ($lines.Count -gt $before) { $lines[$before..($lines.Count - 1)] }
}
