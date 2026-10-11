# The built program at real work, without a mouse: its own buttons are pressed by UI Automation
# and the operations run to their end - official files downloaded, builds made, checked, updated.
#
#   Source\Tools\live-walk.ps1 -Stand E:\Temp\browsers_live
#
# release-walk.ps1 proves the buttons and the stop; this one proves that a press does the work.
# It needs the network and takes hundreds of megabytes and many minutes. The stand is a copy of
# the program - App\, the door, the engine, the Python of the project, tools\ and config\ with
# the manifest alone: its input, output, workspace and logs are its own, nothing of the owner's
# is read or written. The window is found by the id of its process; the pointer is not moved.
#
# Windows itself is left alone: "work around Defender" is unticked before every run (it asks for
# UAC and changes the exclusions of Defender), and no certificate operation is pressed.
#
# What is pressed, in order: Downloads - Download; Install - Build; the builds are moved from
# output to input, where Update looks; Update - Check, Update, Update with "rebuild without a
# new version", then the library alone. Exit code 0 - every operation ended DONE; 1 - one did
# not, and its line says how it ended.
#
# Needs a PowerShell with the WindowsDesktop assemblies (an installed PowerShell 7).
# -Keep leaves the stand in place for a look; otherwise it is removed at the end.
param(
    [Parameter(Mandatory)][string]$Stand,
    # The built window to walk: App\ of the program, or another build of it (a trial one).
    [string]$App = '',
    [string[]]$Browsers = @('chrome', 'yandex', 'brave', 'chromium_gost', 'ungoogled_chromium'),
    [string[]]$Ready = @('cent', 'vivaldi', 'librewolf', 'duckduckgo'),
    [switch]$Keep
)
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $PSScriptRoot
$project = Split-Path -Parent $source
$window = 'AudionBrowsersPortable.exe'
if (-not $App) { $App = Join-Path $project 'App' }

if (Test-Path -LiteralPath $Stand) { "the stand must not exist yet: $Stand"; exit 2 }
if (-not (Test-Path -LiteralPath (Join-Path $App $window))) { "no built window in $App`: install\Build-App.cmd"; exit 2 }
[void](New-Item -ItemType Directory -Force -Path (Join-Path $Stand 'config'))
$Stand = (Resolve-Path -LiteralPath $Stand).Path
foreach ($pair in @(@($App, 'App'), @((Join-Path $project 'system_core'), 'system_core'), @((Join-Path $project 'runtime'), 'runtime'), @((Join-Path $project 'tools'), 'tools'))) {
    $null = robocopy $pair[0] (Join-Path $Stand $pair[1]) /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { "the copy of $($pair[1]) failed"; exit 2 }
}
Copy-Item -LiteralPath (Join-Path $project 'browsers_cli.cmd') -Destination $Stand
foreach ($name in 'tool_manifest.yaml', 'version.json') {
    Copy-Item -LiteralPath (Join-Path $project "config\$name") -Destination (Join-Path $Stand 'config')
}
$exe = Join-Path $Stand "App\$window"
$notes = Join-Path $Stand 'walk'
[void](New-Item -ItemType Directory -Force -Path $notes)

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$auto = [System.Windows.Automation.AutomationElement]
$failed = 0
# Said to the host, not returned: a line said inside a function that returns a value would go
# with that value to whoever took it, and the walk would run in silence.
function Say([bool]$ok, [string]$what) {
    Write-Host ('{0} {1} {2}' -f (Get-Date -Format 'HH:mm:ss'), $(if ($ok) { '[OK]  ' } else { '[FAIL]' }), $what)
    if (-not $ok) { $script:failed++ }
}
function Note([string]$what) { Write-Host ('{0}        {1}' -f (Get-Date -Format 'HH:mm:ss'), $what) }
function Find($root, [string]$id, [int]$seconds = 20) {
    $condition = [System.Windows.Automation.PropertyCondition]::new($auto::AutomationIdProperty, $id)
    $until = (Get-Date).AddSeconds($seconds)
    do {
        # The tree may be rebuilt under the search - a section was just pressed - and the search
        # then breaks instead of finding nothing: asked again like any other "not yet".
        $found = $null
        try { $found = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition) } catch { }
        if ($found) { return $found }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $until)
    return $null
}
function Press($element) {
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) { $pattern.Invoke(); return }
    if ($element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) { $pattern.Toggle(); return }
    throw 'the element can be neither invoked nor toggled'
}
function IsOn($element) {
    $pattern = $null
    if (-not $element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) { return $false }
    return $pattern.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On
}
function TextOf($root, [string]$id) {
    $found = Find $root $id 5
    if ($found) { $found.Current.Name } else { '' }
}
# A switch of a form brought to the state asked for: pressed only when it is in the other one.
function Tick($root, [string]$id, [bool]$on) {
    $switch = Find $root $id 10
    if (-not $switch) { Say $false "no switch in the form: $id"; return }
    if ((IsOn $switch) -ne $on) { Press $switch; Start-Sleep -Milliseconds 300 }
    if ((IsOn $switch) -ne $on) { Say $false "the switch did not turn: $id" }
}
# The words of the state line in both languages: the window speaks the language of the system.
$running = 'ВЫПОЛНЯЕТСЯ', 'RUNNING'
$done = 'ГОТОВО', 'DONE'
$ended = $done + @('ГОТОВО НЕ ПОЛНОСТЬЮ', 'DONE IN PART', 'ОШИБКА', 'FAILED', 'ОСТАНОВЛЕНО', 'STOPPED')

# One operation from the press to its end. The window keeps the outcome of a run in the folder
# of its session; a copy of it goes into the notes of the walk, and the line says how it ended.
#
# "DONE" alone proves little: an Update that found nothing to update is done too, and a switch
# the walk turned may not have reached the operation. So the values the window handed to the
# door are compared with what the walk set ($want: the name of a field and its value), and after
# the run the walk counts what the operation says it did (Count).
$step = 0
function RunOperation($root, [string]$id, [string]$name, [int]$minutes, [hashtable]$want = @{}) {
    $script:step++
    $button = Find $root ("run_" + $id) 15
    if (-not $button -or -not $button.Current.IsEnabled) { Say $false "$name`: the button of $id is not there or is off"; return $null }
    $result = Join-Path $session 'result.json'
    $before = if (Test-Path -LiteralPath $result) { (Get-Item -LiteralPath $result).LastWriteTimeUtc } else { [datetime]::MinValue }
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    Press $button
    $started = $false
    $until = (Get-Date).AddSeconds(25)
    do {
        Start-Sleep -Milliseconds 300
        $fresh = (Test-Path -LiteralPath $result) -and (Get-Item -LiteralPath $result).LastWriteTimeUtc -gt $before
        if ($fresh -or $running -contains (TextOf $root 'StateText')) { $started = $true }
    } while (-not $started -and (Get-Date) -lt $until)
    if (-not $started) { Say $false "$name`: pressed, nothing started; the state line says $(TextOf $root 'StateText'); $(TextOf $root 'StatusText')"; return $null }
    if ($want.Count -gt 0) {
        # The values of this very run: the window wrote them for the door at the press.
        $sent = $null
        try { $sent = Get-Content -LiteralPath (Join-Path $session 'params.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
        $wrong = @(foreach ($field in $want.Keys) {
            $got = if ($sent) { $sent.$field } else { $null }
            if ($null -eq $got -or "$got" -ne "$($want[$field])") { "$field = $got (set: $($want[$field]))" }
        })
        Say ($wrong.Count -eq 0) ("$name`: the window handed the door what was set in the form: " + $(if ($wrong.Count) { $wrong -join '; ' } else { ($want.Keys | Sort-Object | ForEach-Object { "$_ = $($want[$_])" }) -join ', ' }))
    }
    $until = (Get-Date).AddMinutes($minutes)
    $told = Get-Date
    do {
        Start-Sleep -Seconds 2
        $state = TextOf $root 'StateText'
        if (((Get-Date) - $told).TotalSeconds -ge 60) { Note "$name goes on: $(TextOf $root 'StatusText') $(TextOf $root 'CourseText')"; $told = Get-Date }
    } while (-not ($ended -contains $state) -and (Get-Date) -lt $until)
    Start-Sleep -Seconds 1
    $took = [Math]::Round($clock.Elapsed.TotalSeconds)
    $outcome = $null
    if ((Test-Path -LiteralPath $result) -and (Get-Item -LiteralPath $result).LastWriteTimeUtc -gt $before) {
        Copy-Item -LiteralPath $result -Destination (Join-Path $notes ('{0:00}_{1}.json' -f $script:step, $id))
        try { $outcome = Get-Content -LiteralPath $result -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
    }
    $message = if ($outcome) { $outcome.message } else { '(the window has no outcome of this run)' }
    Say ($done -contains $state) "$name`: $state in $took s - $message"
    if ($outcome -and $outcome.failed) { foreach ($item in @($outcome.failed)) { Note "failed: $($item | ConvertTo-Json -Compress -Depth 4)" } }
    return $outcome
}
# What an operation says it did, counted: the lists of its outcome by their names.
function Count($outcome, [string]$name, [string[]]$lists, [int]$expected) {
    if (-not $outcome) { Say $false "$name`: no outcome to count"; return }
    $counts = foreach ($list in $lists) { '{0} {1}' -f $list, @($outcome.data.$list).Count }
    $sum = 0
    foreach ($list in $lists) { $sum += @($outcome.data.$list).Count }
    Say ($sum -eq $expected) ("$name`: the operation names {0} of {1} - {2}" -f $sum, $expected, ($counts -join ', '))
}
function Folder([string]$name) { Join-Path $Stand $name }
function Lies([string]$name) {
    $folder = Folder $name
    if (-not (Test-Path -LiteralPath $folder)) { return }
    foreach ($item in Get-ChildItem -LiteralPath $folder -Force) {
        if ($item.PSIsContainer) {
            $size = (Get-ChildItem -LiteralPath $item.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
            Note ('{0}\{1}\  {2:N0} MB' -f $name, $item.Name, ($size / 1MB))
        }
        else { Note ('{0}\{1}  {2:N0} MB' -f $name, $item.Name, ($item.Length / 1MB)) }
    }
}

$process = $null
try {
    $process = Start-Process -FilePath $exe -PassThru
    $until = (Get-Date).AddSeconds(40)
    while ($process.MainWindowHandle -eq 0 -and -not $process.HasExited -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 300; $process.Refresh() }
    if ($process.MainWindowHandle -eq 0) { throw 'the window did not open' }
    $root = $auto::FromHandle($process.MainWindowHandle)
    $session = Join-Path ([IO.Path]::GetTempPath()) "browsers_window_$($process.Id)"
    Say ($null -ne (Find $root 'tab_install' 40)) "the window of the stand is open and has read the manifest: $Stand"

    # Downloads: the vendors' own files into output.
    Press (Find $root 'tab_browser_downloads')
    foreach ($key in $Ready) { Tick $root "field_ready_portable_download_ready_browsers_$key" $true }
    $told = RunOperation $root 'ready_portable_download' 'Download' 30
    Count $told 'Download' @('downloaded') $Ready.Count
    Lies 'output'
    # They are not builds: kept aside, so Update does not have to look past them.
    $kept = Folder 'downloads'
    [void](New-Item -ItemType Directory -Force -Path $kept)
    Get-ChildItem -LiteralPath (Folder 'output') -Force -ErrorAction SilentlyContinue | Move-Item -Destination $kept

    # Install: new builds into output. Defender is left alone.
    Press (Find $root 'tab_install')
    foreach ($key in $Browsers) { Tick $root "field_browsers_build_browsers_$key" $true }
    Tick $root 'field_browsers_build_guard_defender' $false
    $told = RunOperation $root 'browsers_build' 'Build' 60 @{ guard_defender = $false }
    Count $told 'Build' @('built') $Browsers.Count
    Lies 'output'

    # A build is updated where it lies, and Update looks in Source: the builds go to input, each
    # right under it. Check and Update find a build anywhere under Source; "the library alone"
    # takes one only from Source itself or right under it, so Source is made their parent - as
    # it is for a person who took the folders of the browsers from output to a flash drive.
    [void](New-Item -ItemType Directory -Force -Path (Folder 'input'))
    $holder = Join-Path (Folder 'output') 'Portable'
    if (-not (Test-Path -LiteralPath $holder)) { $holder = Folder 'output' }
    Get-ChildItem -LiteralPath $holder -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'App') } | Move-Item -Destination (Folder 'input')
    $builds = @(Get-ChildItem -LiteralPath (Folder 'input') -Directory -Force)
    Say ($builds.Count -eq $Browsers.Count) ("the builds moved from output to input: {0} of {1}" -f $builds.Count, $Browsers.Count)

    # Update: check, update, then the whole path of an update whatever the version.
    Press (Find $root 'tab_update')
    Press (Find $root 'part_browsers_update')
    Tick $root 'field_browsers_update_guard_defender' $false
    $told = RunOperation $root 'browsers_check' 'Check' 15
    Count $told 'Check' @('browsers') $Browsers.Count
    # Builds made a minute ago are current: each is either updated or passed by, none is lost.
    $told = RunOperation $root 'browsers_update' 'Update' 60 @{ guard_defender = $false; force_update = $false }
    Count $told 'Update' @('updated', 'skipped') $Browsers.Count
    # With "rebuild without a new version" every one of them goes the whole way of an update.
    Tick $root 'field_browsers_update_force_update' $true
    $told = RunOperation $root 'browsers_update' 'Update, rebuilt without a new version' 60 @{ guard_defender = $false; force_update = $true }
    Count $told 'Update, rebuilt without a new version' @('updated') $Browsers.Count
    Tick $root 'field_browsers_update_force_update' $false

    # The library alone.
    Press (Find $root 'part_browsers_library_update')
    Tick $root 'field_browsers_library_update_guard_defender' $false
    $told = RunOperation $root 'browsers_library_update' 'Library alone' 20 @{ guard_defender = $false }
    Count $told 'Library alone' @('updated', 'skipped') $Browsers.Count
    Lies 'input'

    $close = $null
    [void]$root.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$close)
    $close.Close()
    Say ($process.WaitForExit(30000)) 'the window closed'
    Start-Sleep -Seconds 2
    $left = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Stand, [StringComparison]::OrdinalIgnoreCase) })
    Say ($left.Count -eq 0) ("no process of the stand is left: {0}" -f $left.Count)
    $crash = @(Get-ChildItem -LiteralPath (Folder 'logs') -Filter 'gui-crash-*.log' -ErrorAction SilentlyContinue)
    Say ($crash.Count -eq 0) ("the window wrote no crash report: {0}" -f $crash.Count)
}
catch {
    Say $false ("the walk broke: " + $_.Exception.Message)
}
finally {
    Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($Stand, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { try { $_.Kill() } catch { } }
    if ($Keep) { "the stand is kept: $Stand" }
    else {
        Start-Sleep -Seconds 1
        try { [IO.Directory]::Delete($Stand, $true); 'the stand is removed' } catch { "the stand could not be removed: $Stand - $($_.Exception.Message)" }
    }
}
"failed checks: $failed"
exit ([int]($failed -gt 0))
