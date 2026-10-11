# The elevated half of the Defender guard of Audion Browsers Portable.
#
# The engine (system_core\services\browsers_portable_service.py, _defender_guard) starts this
# script through UAC with the PowerShell of Windows and never waits for it: the two sides talk
# through files.
#
#   -Action Begin   exclude the folders from Windows Defender, write the Ready file, then wait
#                   until the engine deletes the Lock file, or the engine's process is gone, or
#                   MaxSeconds have passed - and take the exclusions back. Whatever ends the
#                   wait, the exclusions are taken back: an exclusion is a hole in the
#                   protection of the machine and must not outlive the build it was opened for.
#   -Action Remove  take the exclusions of the folders back and say so: the safety net for a
#                   guard that was ended hard.
#
# A folder that was excluded before the guard came is not the guard's: Begin does not take it
# back. Remove takes back whatever it is asked about - it is pressed by a person, by name.
#
# The Result file holds one line, "STATUS<tab>detail": REMOVED, FAILED. It is written without a
# byte order mark - the engine compares its first word.
#
# Kept in plain ASCII and in the language of Windows PowerShell 5.1: that is the PowerShell
# every Windows has, the module of Defender is its own, and the question of UAC then names a
# file signed by Microsoft. It lies beside the service that calls it, not in a folder a cleanup
# empties: until 11.10.2026 it was expected in system_core\powershell, the folder of the
# portable PowerShell, which cleanup_project.cmd clears - and it was not there.
param(
    [Parameter(Mandatory = $true)][ValidateSet('Begin', 'Remove')][string]$Action,
    [Parameter(Mandatory = $true)][string]$Path,
    [int]$ParentPid = 0,
    [string]$Lock = '',
    [string]$Ready = '',
    [string]$Result = '',
    [int]$MaxSeconds = 3600
)

$ErrorActionPreference = 'Stop'

function Write-Note([string]$File, [string]$Text) {
    if (-not $File) { return }
    try { [System.IO.File]::WriteAllText($File, $Text, (New-Object System.Text.UTF8Encoding($false))) } catch { }
}

# One line: a tab parts the status from the detail, so the detail holds neither.
function Write-Result([string]$Status, [string]$Detail) {
    $plain = ($Detail -replace '[\t\r\n]+', ' ').Trim()
    Write-Note $Result ($Status + "`t" + $plain)
}

function Get-Excluded {
    try { return @((Get-MpPreference).ExclusionPath | Where-Object { $_ }) } catch { return @() }
}

function Test-ParentAlive {
    if ($ParentPid -le 0) { return $true }
    return $null -ne (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)
}

# The folders come as one argument joined with "|", a character no Windows path can hold.
$folders = @($Path -split '\|' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($folders.Count -eq 0) {
    Write-Result 'FAILED' 'no folder was named'
    exit 2
}

if ($Action -eq 'Remove') {
    $failed = @()
    foreach ($folder in $folders) {
        try { Remove-MpPreference -ExclusionPath $folder } catch { $failed += ($folder + ' (' + $_.Exception.Message + ')') }
    }
    if ($failed.Count -gt 0) {
        Write-Result 'FAILED' ('the exclusion was not removed: ' + ($failed -join '; '))
        exit 1
    }
    Write-Result 'REMOVED' ($folders -join '; ')
    exit 0
}

# Begin.
$before = Get-Excluded
$added = @()
try {
    foreach ($folder in $folders) {
        $known = @($before | Where-Object { $_.TrimEnd('\') -ieq $folder.TrimEnd('\') }).Count -gt 0
        if ($known) { continue }
        Add-MpPreference -ExclusionPath $folder
        $added += $folder
    }
}
catch {
    # What was added before the failure goes back at once: half a guard is no guard.
    $reason = $_.Exception.Message
    foreach ($folder in $added) { try { Remove-MpPreference -ExclusionPath $folder } catch { } }
    Write-Result 'FAILED' ('the exclusion was not added: ' + $reason)
    exit 1
}

Write-Note $Ready 'ready'

$left = @()
try {
    $limit = [Math]::Max(1, $MaxSeconds)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $limit) {
        if ($Lock -and -not (Test-Path -LiteralPath $Lock)) { break }
        if (-not (Test-ParentAlive)) { break }
        Start-Sleep -Milliseconds 300
    }
}
finally {
    foreach ($folder in $added) {
        try { Remove-MpPreference -ExclusionPath $folder } catch { $left += ($folder + ' (' + $_.Exception.Message + ')') }
    }
    if ($left.Count -gt 0) { Write-Result 'FAILED' ('the exclusion was not removed: ' + ($left -join '; ')) }
    else { Write-Result 'REMOVED' ($folders -join '; ') }
}
if ($left.Count -gt 0) { exit 1 }
exit 0
