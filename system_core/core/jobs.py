from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import importlib
import json
import os
import subprocess
import time
import traceback

from .logging_utils import append_log, timestamp
from .manifest import Operation
from .paths import ProjectPaths


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[float], None]
CancelCallback = Callable[[], bool]
PulseCallback = Callable[[str], None]


@dataclass
class JobContext:
    paths: ProjectPaths
    operation: Operation
    log_file: Path
    report_dir: Path
    log_callback: LogCallback | None = None
    progress_callback: ProgressCallback | None = None
    cancel_callback: CancelCallback | None = None
    pulse_callback: PulseCallback | None = None

    def log(self, message: str) -> None:
        append_log(self.log_file, message)
        if self.log_callback:
            self.log_callback(message)

    def progress(self, value: float) -> None:
        if self.progress_callback:
            self.progress_callback(max(0.0, min(1.0, float(value))))

    def cancelled(self) -> bool:
        return bool(self.cancel_callback and self.cancel_callback())

    def pulse(self, message: str) -> None:
        """Where a long step is right now. A state, not an event: whoever shows it rewrites the
        pulse before it in place, and it is not written to the log file. Nobody listening -
        nothing is said."""
        if self.pulse_callback:
            self.pulse_callback(message)


MEGABYTE = 1024 * 1024


class TransferNote:
    """How far a download is, told the way the listener can take it.

    To a listener of pulses (the door, for a window) - one line rewritten in place a few times
    a second: the share, then - each after a bar - megabytes and the speed. Ten lines of a
    journal for one file say less than one line that lives. Without such a listener - a line of the log at every tenth of the way,
    as it always was: a log file and a plain console keep what they are told.
    """

    def __init__(self, context: JobContext, log_prefix: str, pulse_prefix: str | None = None) -> None:
        self.context = context
        self.log_prefix = log_prefix
        self.pulse_prefix = log_prefix if pulse_prefix is None else pulse_prefix
        self.logged = -10
        self.told = float("-inf")
        self.marks: list[tuple[float, int]] = []

    def at(self, downloaded: int, total: int) -> None:
        if total <= 0:
            return
        percent = int(min(1.0, downloaded / total) * 100)
        # A context made by hand - a test's, an older caller's - may know nothing of pulses.
        if getattr(self.context, "pulse_callback", None) is None:
            if percent >= self.logged + 10:
                self.context.log(f"[DOWNLOAD] {self.log_prefix}{percent}% ({downloaded:,}/{total:,} bytes)")
                self.logged = percent
            return
        now = time.monotonic()
        self.marks.append((now, downloaded))
        # The speed of the last few seconds: of the whole way it lags, of one read it jumps.
        while len(self.marks) > 2 and now - self.marks[0][0] > 3.0:
            self.marks.pop(0)
        if downloaded < total and now - self.told < 0.25:
            return
        self.told = now
        (since, had) = self.marks[0]
        speed = (downloaded - had) / (now - since) / MEGABYTE if now > since else 0.0
        # The measures stand apart, each after a bar: whoever shows a pulse may set them off -
        # the window writes what follows a bar in its accent.
        self.context.pulse(f"[DOWNLOAD] {self.pulse_prefix}{percent}% | "
                           f"{downloaded / MEGABYTE:,.1f} / {total / MEGABYTE:,.1f} MB | {speed:.1f} MB/s")


@dataclass
class JobResult:
    ok: bool
    message: str
    data: dict[str, Any]


class OperationCancelled(Exception):
    """A service stopped because Cancel was pressed.

    Raised by a service at a point where stopping still costs nothing. The
    runner reports it as an operation that was not done - and without the
    traceback a failure gets, because nothing failed. Not a `RuntimeError` on
    purpose: code that answers those by trying another way must not answer a
    cancel like that.
    """


SENSITIVE_PARAMETER_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "api_key",
    "access_key",
    "private_key",
    "credential",
)


def redact_parameters(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in SENSITIVE_PARAMETER_PARTS):
                result[key_text] = "***REDACTED***" if item not in {"", None} else item
                continue
            result[key_text] = redact_parameters(item)
        return result
    if isinstance(value, list):
        return [redact_parameters(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_parameters(item) for item in value)
    return value


def utf8_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    env.setdefault("PYTHONUTF8", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("NO_COLOR", None)
    if env.get("CLICOLOR") == "0":
        env.pop("CLICOLOR", None)
    env.setdefault("CLICOLOR", "1")
    env.setdefault("CLICOLOR_FORCE", "1")
    env.setdefault("FORCE_COLOR", "1")
    env.setdefault("AUDION_GUI_TERMINAL", "1")
    return env


def hidden_subprocess_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def hidden_subprocess_creationflags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


def hidden_subprocess_kwargs() -> dict[str, Any]:
    return {
        "startupinfo": hidden_subprocess_startupinfo(),
        "creationflags": hidden_subprocess_creationflags(),
    }


def _load_callable(service: str) -> Callable[[JobContext], Any]:
    module_name, function_name = service.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def execute_operation(
    paths: ProjectPaths,
    operation: Operation,
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    pulse_callback: PulseCallback | None = None,
) -> JobResult:
    run_stamp = timestamp().replace(":", "-")
    log_file = paths.logs / f"{run_stamp}_{operation.id}.log"
    report_dir = paths.report / f"{run_stamp}_{operation.id}"
    context = JobContext(paths, operation, log_file, report_dir, log_callback, progress_callback, cancel_callback,
                         pulse_callback)

    try:
        # Inside the `try`: a program folder that cannot be written to - a read-only share, a
        # disk that is full - is an operation that failed, said as one, not an error nobody
        # catches after the caller was told the run had started.
        report_dir.mkdir(parents=True, exist_ok=True)
        context.log(f"Starting operation: {operation.id}")
        if operation.parameters:
            safe_parameters = redact_parameters(operation.parameters)
            context.log(f"Parameters: {json.dumps(safe_parameters, ensure_ascii=False, sort_keys=True)}")
        context.progress(0.0)
        result = _load_callable(operation.service)(context)
        context.progress(1.0)
        context.log(f"Finished operation: {operation.id}")

        if isinstance(result, dict):
            return JobResult(True, "Operation finished.", result)
        return JobResult(True, str(result or "Operation finished."), {})

    except OperationCancelled as exc:
        _say_anyway(context, str(exc))
        # Said in the data too: whoever started the operation tells a stop from a failure by it,
        # not by a stop signal that happens to lie there when something else broke.
        return JobResult(False, str(exc), {"cancelled": True})
    except Exception as exc:
        _say_anyway(context, traceback.format_exc())
        return JobResult(False, f"{exc.__class__.__name__}: {exc}", {})


def _say_anyway(context: JobContext, message: str) -> None:
    """The last word of an operation, whether or not its log file can be written.

    When it is the log itself that failed - `logs` cannot be written to - saying why through
    the same log raised once more, out of the handler, and the outcome was lost with it.
    """
    try:
        context.log(message)
    except OSError:
        if context.log_callback:
            try:
                context.log_callback(message)
            except Exception:  # noqa: BLE001 - nobody is left to tell
                pass
