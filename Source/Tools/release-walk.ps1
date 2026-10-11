# The built program walked through without a mouse: its own buttons are pressed by UI Automation.
#
#   Source\Tools\release-walk.ps1 -Stand C:\Temp\browsers_stand
#
# A release build has no steps of a check inside and no stand to be pointed at, so the stand is a
# copy of the program: App\, the door, the engine, the Python of the project and config\ with the
# manifest alone. Its input, output, logs and report are its own - nothing of the owner's is read
# or written. The window is found by the id of its process, never by its name or title; the
# pointer is not moved.
#
# What is pressed: a section, an operation that only reads (the state of the certificates), a
# choice of a form (ZIP or 7Z), the clearing of the working folders (its menu and its question;
# two probe files of the stand go to the Recycle Bin), a download with STOP pressed while it
# goes, the same download with the window closed while it goes. Exit code 0 - every check held;
# 1 - one did not, and the line says which.
#
# Needs a PowerShell with the WindowsDesktop assemblies (an installed PowerShell 7).
# -Keep leaves the stand in place for a look; otherwise it is removed at the end.
param(
    [Parameter(Mandatory)][string]$Stand,
    # The built window to walk: App\ of the program, or another build of it (a trial one).
    [string]$App = '',
    [switch]$Keep,
    [switch]$NoDownload
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
foreach ($pair in @(@($App, 'App'), @((Join-Path $project 'system_core'), 'system_core'), @((Join-Path $project 'runtime'), 'runtime'))) {
    $null = robocopy $pair[0] (Join-Path $Stand $pair[1]) /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { "the copy of $($pair[1]) failed"; exit 2 }
}
Copy-Item -LiteralPath (Join-Path $project 'browsers_cli.cmd') -Destination $Stand
foreach ($name in 'tool_manifest.yaml', 'version.json') {
    Copy-Item -LiteralPath (Join-Path $project "config\$name") -Destination (Join-Path $Stand 'config')
}
$exe = Join-Path $Stand "App\$window"

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$auto = [System.Windows.Automation.AutomationElement]
$failed = 0
function Say([bool]$ok, [string]$what) {
    '{0} {1}' -f $(if ($ok) { '[OK]  ' } else { '[FAIL]' }), $what
    if (-not $ok) { $script:failed++ }
}
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
function WaitState($root, [string[]]$words, [int]$seconds) {
    $until = (Get-Date).AddSeconds($seconds)
    do {
        $now = TextOf $root 'StateText'
        if ($words -contains $now) { return $now }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $until)
    return (TextOf $root 'StateText')
}
function FindInProcess([int]$id, [string]$name, [int]$seconds = 15) {
    # A question is a window of its own: looked for among the windows of the process, not inside the main one.
    $mine = [System.Windows.Automation.PropertyCondition]::new($auto::ProcessIdProperty, $id)
    $named = [System.Windows.Automation.PropertyCondition]::new($auto::AutomationIdProperty, $name)
    $until = (Get-Date).AddSeconds($seconds)
    do {
        try {
            foreach ($top in $auto::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $mine)) {
                $found = $top.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $named)
                if ($found) { return $found }
            }
        }
        catch { }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $until)
    return $null
}
function StartWindow {
    $process = Start-Process -FilePath $exe -PassThru
    $until = (Get-Date).AddSeconds(40)
    while ($process.MainWindowHandle -eq 0 -and -not $process.HasExited -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 300; $process.Refresh() }
    if ($process.MainWindowHandle -eq 0) { throw 'the window did not open' }
    return $process
}
# The words of the state line in both languages: the window speaks the language of the system.
$done = 'ГОТОВО', 'DONE'; $stopped = 'ОСТАНОВЛЕНО', 'STOPPED'; $running = 'ВЫПОЛНЯЕТСЯ', 'RUNNING'

try {
    $process = StartWindow
    $root = $auto::FromHandle($process.MainWindowHandle)

    # A section, then an operation that only reads.
    $tab = Find $root 'tab_certificates' 40
    Say ($null -ne $tab) 'the manifest is read: the section "certificates" is in the window'
    Press $tab
    $button = Find $root 'run_certificates_state'
    Say ($null -ne $button -and $button.Current.IsEnabled) 'the button of "certificates_state" is there and can be pressed'
    Press $button
    $state = WaitState $root $done 60
    Say ($done -contains $state) "pressed, the operation ended: the state line says $state"
    $log = @(Get-ChildItem -LiteralPath (Join-Path $Stand 'logs') -Filter '*_certificates_state.log' -ErrorAction SilentlyContinue)
    Say ($log.Count -eq 1 -and (Get-Content -LiteralPath $log[0].FullName -Raw) -match 'Finished operation: certificates_state') 'the operation ran through the door: its journal lies in the logs of the stand'

    # A form: a field that stands on the line of another (ZIP or 7Z beside "Pack into an
    # archive") is a switch of its own, and a rarely needed field is in sight without a press.
    Press (Find $root 'tab_install')
    $zip = Find $root 'field_browsers_build_archive_format_zip'
    $seven = Find $root 'field_browsers_build_archive_format_7z'
    Say ($null -ne $zip -and $null -ne $seven -and (IsOn $zip) -and -not (IsOn $seven)) 'ZIP and 7Z stand in the form of "browsers_build", ZIP is on'
    if ($seven) { Press $seven; Start-Sleep -Milliseconds 400 }
    Say ($null -ne $zip -and $null -ne $seven -and (IsOn $seven) -and -not (IsOn $zip)) '7Z pressed: 7Z is on, ZIP is off'
    if ($zip) { Press $zip; Start-Sleep -Milliseconds 300 }
    $rare = Find $root 'field_browsers_build_keep_temp' 5
    Say ($null -ne $rare -and -not $rare.Current.IsOffscreen) 'a field under "Advanced" is in the form without a press: keep_temp'

    # An operation with a form refuses to start until its field is filled in.
    Press (Find $root 'tab_browser_downloads')
    $download = Find $root 'run_ready_portable_download'
    Press $download
    Start-Sleep -Milliseconds 800
    Say (-not ($running -contains (TextOf $root 'StateText'))) 'no browser ticked: the press starts nothing'

    # The working folders cleared by their own button: its menu, the question, then the contents
    # gone and the folders left. What is deleted goes to the Recycle Bin, as it does for a person.
    $in = Join-Path $Stand 'input'; $out = Join-Path $Stand 'output'
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $in 'release-walk-probe'))
    Set-Content -LiteralPath (Join-Path $in 'release-walk-probe\probe.txt') -Value 'a probe of release-walk.ps1' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $out 'release-walk-probe.txt') -Value 'a probe of release-walk.ps1' -Encoding utf8
    Press (Find $root 'ClearFoldersButton' 5)
    $both = Find $root 'clear_both' 5
    Say ($null -ne $both) 'the button of clearing opens its menu: "both" is in it'
    if ($both) {
        Press $both
        $yes = FindInProcess $process.Id 'OkButton' 10
        Say ($null -ne $yes) 'clearing asks first'
        # "Yes" of a question that cannot be taken back comes alive a moment after it opens.
        $until = (Get-Date).AddSeconds(5)
        while ($yes -and -not $yes.Current.IsEnabled -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 100 }
        Say ($null -ne $yes -and $yes.Current.IsEnabled) '"yes" of the question can be pressed after its pause'
        if ($yes) { Press $yes }
        $until = (Get-Date).AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 300
            $left = @(Get-ChildItem -LiteralPath $in -Force -ErrorAction SilentlyContinue) + @(Get-ChildItem -LiteralPath $out -Force -ErrorAction SilentlyContinue)
        } while ($left.Count -gt 0 -and (Get-Date) -lt $until)
        Say ($left.Count -eq 0 -and (Test-Path -LiteralPath $in) -and (Test-Path -LiteralPath $out)) ("cleared by the button: the contents of input and output are gone, the folders stay ({0} left)" -f $left.Count)
        Start-Sleep -Milliseconds 600
        Say ($download.Current.IsEnabled) 'after the clearing the button of an operation can be pressed again'
    }

    if (-not $NoDownload) {
        # STOP while a download goes.
        Press (Find $root 'field_ready_portable_download_ready_browsers_librewolf')
        Press $download
        $state = WaitState $root $running 15
        Say ($running -contains $state) 'a browser ticked, the button pressed: the download goes'
        Start-Sleep -Seconds 3
        $stop = Find $root 'StopButton' 5
        Say ($null -ne $stop -and $stop.Current.IsEnabled) 'STOP is in sight and can be pressed while the operation goes'
        Press $stop
        $state = WaitState $root $stopped 40
        Say ($stopped -contains $state) "STOP pressed: the state line says $state"
        Start-Sleep -Milliseconds 600
        Say ($download.Current.IsEnabled) 'after the stop the button of the operation can be pressed again'

        # The window closed while a download goes: it asks, stops the operation and then closes.
        Press $download
        $null = WaitState $root $running 15
        Start-Sleep -Seconds 2
        $close = $null
        [void]$root.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$close)
        $close.Close()
        $desktop = $auto::RootElement
        $mine = [System.Windows.Automation.PropertyCondition]::new($auto::ProcessIdProperty, $process.Id)
        $ok = $null
        $until = (Get-Date).AddSeconds(15)
        do {
            foreach ($top in $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, $mine)) {
                $ok = $top.FindFirst([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.PropertyCondition]::new($auto::AutomationIdProperty, 'OkButton'))
                if ($ok) { break }
            }
            if (-not $ok) { Start-Sleep -Milliseconds 300 }
        } while (-not $ok -and (Get-Date) -lt $until)
        Say ($null -ne $ok) 'closing during an operation asks first'
        Say (-not $process.HasExited) 'the window stays while the question stands'
        # "Yes" of a question that cannot be taken back comes alive a moment after it opens.
        $until = (Get-Date).AddSeconds(5)
        while ($ok -and -not $ok.Current.IsEnabled -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 100 }
        if ($ok) { Press $ok }
        Say ($process.WaitForExit(60000)) 'answered: the operation stopped and the window closed'
    }
    else {
        $close = $null
        [void]$root.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$close)
        $close.Close()
        Say ($process.WaitForExit(20000)) 'the window closed'
    }

    Start-Sleep -Seconds 2
    $left = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Stand, [StringComparison]::OrdinalIgnoreCase) })
    Say ($left.Count -eq 0) ("no process of the stand is left: {0}" -f $left.Count)

    # The end of the door is not the end of its output: a door that starts a helper and ends
    # leaves its output open for as long as the helper lives. The door of the stand is replaced
    # with one that does just that - its helper sleeps for 14 seconds - and the window must say
    # how the run ended long before the helper is gone.
    $door = Join-Path $Stand 'system_core\cli.py'
    Move-Item -LiteralPath $door -Destination (Join-Path $Stand 'system_core\cli_real.py')
    Set-Content -LiteralPath $door -Encoding utf8 -Value @'
import os, runpy, subprocess, sys
real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli_real.py")
if "run" in sys.argv:
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(14)"], stdout=sys.stdout, stderr=sys.stderr)
    print('::browsers::{"started": {}}', flush=True)
    print("the door leaves a helper behind and ends", flush=True)
    print('::browsers::{"finished": {"outcome": "done", "message": ""}}', flush=True)
    sys.exit(0)
sys.argv[0] = real
runpy.run_path(real, run_name="__main__")
'@
    $process = StartWindow
    $root = $auto::FromHandle($process.MainWindowHandle)
    Press (Find $root 'tab_certificates' 40)
    $button = Find $root 'run_certificates_state'
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    Press $button
    $state = WaitState $root $done 30
    $took = [Math]::Round($clock.Elapsed.TotalSeconds, 1)
    $helper = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Stand, [StringComparison]::OrdinalIgnoreCase) -and $_.Name -eq 'python.exe' })
    Say ($done -contains $state -and $took -lt 9 -and $helper.Count -gt 0) "a door that left a helper behind: the run is over for the window in $took s, the helper still lives ($($helper.Count))"
    Say ($button.Current.IsEnabled) 'and the next operation can be started'
    $close = $null
    [void]$root.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$close)
    $close.Close()
    Say ($process.WaitForExit(20000)) 'the window closed while the helper lived'
    if (-not $NoDownload) {
        # Said only of a walk that did download: without one there was nothing to leave.
        $parts = @(Get-ChildItem -LiteralPath (Join-Path $Stand 'output') -Recurse -File -ErrorAction SilentlyContinue)
        Say ($parts.Count -eq 0) ("a stopped download leaves no file behind: {0}" -f $parts.Count)
    }
    $crash = @(Get-ChildItem -LiteralPath (Join-Path $Stand 'logs') -Filter 'gui-crash-*.log' -ErrorAction SilentlyContinue)
    Say ($crash.Count -eq 0) ("the window wrote no crash report: {0}" -f $crash.Count)
}
catch {
    Say $false ("the walk broke: " + $_.Exception.Message)
}
finally {
    Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($Stand, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { try { $_.Kill() } catch { } }
    if ($Keep) { "the stand is kept: $Stand" }
    else {
        Start-Sleep -Milliseconds 800
        # The stand is a plain copy - no link of it leads into the program - so it goes whole.
        [System.IO.Directory]::Delete($Stand, $true)
        'the stand is removed'
    }
}
"failed checks: $failed"
exit $(if ($failed) { 1 } else { 0 })
