"""The elevated half of the Defender guard: system_core/services/defender_guard.ps1.

The engine starts the script through UAC and talks to it through files. For years the engine
looked for it in a folder the cleanup of the project empties, and it was not there: the guard
asked for UAC and guarded nothing. So these tests hold three things - the script exists where
the engine looks, it takes every argument the engine gives, and it behaves: the exclusion is
added, and whatever ends the wait, it is taken back.

Windows Defender itself is never touched here and nothing is elevated: the three cmdlets of
Defender are replaced by functions that write what they were asked into a file, and the script
is run by the PowerShell of Windows under them.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from system_core.services import browsers_portable_service as service

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "system_core" / "services" / "defender_guard.ps1"
POWERSHELL = shutil.which("powershell.exe")

needs_powershell = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="the PowerShell of Windows is not here")


def test_the_guard_script_lies_where_the_engine_looks() -> None:
    context = type("Context", (), {})()
    assert service._defender_guard_script(context) == SCRIPT
    assert SCRIPT.is_file()
    # Not in a folder a cleanup empties, and not in one git ignores.
    cleanup = (ROOT / "cleanup_project.cmd").read_text(encoding="utf-8", errors="replace")
    assert "system_core\\services" not in cleanup
    # Windows PowerShell 5.1 reads a file without a mark as ANSI: the script is plain ASCII.
    assert SCRIPT.read_bytes().isascii()


def test_the_guard_script_takes_every_argument_the_engine_gives() -> None:
    engine = (ROOT / "system_core" / "services" / "browsers_portable_service.py").read_text(encoding="utf-8")
    given = set(re.findall(r'"-(Action|Path|ParentPid|Lock|Ready|Result|MaxSeconds)"', engine))
    assert given == {"Action", "Path", "ParentPid", "Lock", "Ready", "Result", "MaxSeconds"}
    head = SCRIPT.read_text(encoding="ascii").split("$ErrorActionPreference", 1)[0]
    declared = set(re.findall(r"\$(\w+)(?:\s*=|\s*,|\s*\))", head.split("param(", 1)[1]))
    assert given <= declared, f"the script does not declare: {sorted(given - declared)}"
    for action in re.findall(r'"-Action", "(\w+)"', engine):
        assert f"'{action}'" in head, action


class Guard:
    """The script under functions that stand in for the cmdlets of Defender."""

    def __init__(self, folder: Path, already: tuple[str, ...] = (), fail_on: str = "") -> None:
        self.folder = folder
        self.calls = folder / "calls.txt"
        self.lock = folder / "guard.lock"
        self.ready = folder / "guard.ready"
        self.result = folder / "guard.result"
        listed = ", ".join(f"'{item}'" for item in already)
        self.stubs = (
            f"$calls = '{self.calls}'; "
            f"function Get-MpPreference {{ [pscustomobject]@{{ ExclusionPath = @({listed}) }} }}; "
            "function Add-MpPreference { param($ExclusionPath) "
            f"if ('{fail_on}' -and $ExclusionPath -eq '{fail_on}') {{ throw 'Defender said no' }}; "
            "Add-Content -LiteralPath $calls -Value ('ADD ' + $ExclusionPath) }; "
            "function Remove-MpPreference { param($ExclusionPath) Add-Content -LiteralPath $calls -Value ('REMOVE ' + $ExclusionPath) }; "
        )

    def start(self, *arguments: str) -> subprocess.Popen:
        command = self.stubs + f"& '{SCRIPT}' " + " ".join(arguments)
        return subprocess.Popen([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def begin(self, paths: str, parent: int | None = None, seconds: int = 60) -> subprocess.Popen:
        self.lock.write_text("lock", encoding="utf-8")
        return self.start("-Action", "Begin", "-Path", f"'{paths}'", "-ParentPid", str(os.getpid() if parent is None else parent),
                          "-Lock", f"'{self.lock}'", "-Ready", f"'{self.ready}'", "-Result", f"'{self.result}'", "-MaxSeconds", str(seconds))

    def said(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8-sig").split("\n")[:-1] if self.calls.exists() else []

    def told(self) -> bytes:
        return self.result.read_bytes() if self.result.exists() else b""


def wait_for(path: Path, seconds: float = 30.0) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def lines(guard: Guard) -> list[str]:
    return [line.strip() for line in guard.said() if line.strip()]


@needs_powershell
def test_the_exclusion_lives_while_the_lock_lies_and_goes_with_it(tmp_path: Path) -> None:
    guard = Guard(tmp_path)
    process = guard.begin(r"D:\out|D:\build x")
    try:
        assert wait_for(guard.ready), process.communicate(timeout=5)
        assert lines(guard) == [r"ADD D:\out", r"ADD D:\build x"]
        assert process.poll() is None and not guard.result.exists()      # it waits, the exclusion stands

        guard.lock.unlink()                                               # the engine is done
        process.wait(timeout=30)
    finally:
        process.kill() if process.poll() is None else None
    assert lines(guard)[2:] == [r"REMOVE D:\out", r"REMOVE D:\build x"]
    # The engine compares the first word of the file: no byte order mark before it.
    assert guard.told().startswith(b"REMOVED\t")
    assert service._read_guard_result(guard.result).startswith("REMOVED")


@needs_powershell
def test_an_engine_that_is_gone_does_not_leave_its_exclusion(tmp_path: Path) -> None:
    gone = subprocess.Popen([POWERSHELL, "-NoProfile", "-Command", "exit 0"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    gone.wait(timeout=30)
    guard = Guard(tmp_path)
    process = guard.begin(r"D:\out", parent=gone.pid)
    process.wait(timeout=30)

    assert guard.lock.exists()                                            # nobody released it: the engine died
    assert lines(guard) == [r"ADD D:\out", r"REMOVE D:\out"]
    assert guard.told().startswith(b"REMOVED\t")


@needs_powershell
def test_the_guard_does_not_wait_longer_than_it_was_given(tmp_path: Path) -> None:
    guard = Guard(tmp_path)
    process = guard.begin(r"D:\out", seconds=2)
    process.wait(timeout=30)

    assert guard.lock.exists() and lines(guard) == [r"ADD D:\out", r"REMOVE D:\out"]


@needs_powershell
def test_a_folder_excluded_before_the_guard_is_not_the_guards(tmp_path: Path) -> None:
    guard = Guard(tmp_path, already=("D:\\OUT\\",))
    process = guard.begin(r"D:\out|D:\build", seconds=2)
    process.wait(timeout=30)

    assert lines(guard) == [r"ADD D:\build", r"REMOVE D:\build"]


@needs_powershell
def test_half_a_guard_is_taken_back_and_said(tmp_path: Path) -> None:
    guard = Guard(tmp_path, fail_on=r"D:\build")
    process = guard.begin(r"D:\out|D:\build")
    process.wait(timeout=30)

    assert lines(guard) == [r"ADD D:\out", r"REMOVE D:\out"]
    assert not guard.ready.exists()
    assert guard.told().startswith(b"FAILED\t") and b"Defender said no" in guard.told()


@needs_powershell
def test_remove_takes_the_named_folders_back(tmp_path: Path) -> None:
    guard = Guard(tmp_path)
    process = guard.start("-Action", "Remove", "-Path", r"'D:\out|D:\in'", "-Result", f"'{guard.result}'")
    process.wait(timeout=30)

    assert lines(guard) == [r"REMOVE D:\out", r"REMOVE D:\in"]
    assert service._read_guard_result(guard.result) == r"REMOVED: D:\out; D:\in"
