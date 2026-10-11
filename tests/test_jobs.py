from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

from system_core.core.ansi import strip_ansi
from system_core.core.jobs import JobContext, OperationCancelled, execute_operation, redact_parameters
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths


def _run_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, service) -> tuple[object, list[str]]:  # noqa: ANN001
    """Run one function as an operation, the way the window does."""
    module = types.ModuleType("service_for_the_job_runner_test")
    module.run = service
    monkeypatch.setitem(sys.modules, module.__name__, module)
    paths = get_project_paths(tmp_path)
    ensure_project_dirs(paths)
    operation = Operation(id="test", title="Test", description="", service=f"{module.__name__}:run")
    lines: list[str] = []
    return execute_operation(paths, operation, log_callback=lines.append), lines


def test_a_cancelled_operation_is_not_done_and_is_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def stop(_context: JobContext) -> dict[str, object]:
        raise OperationCancelled("Cancelled before anything was changed.")

    result, lines = _run_service(tmp_path, monkeypatch, stop)

    assert result.ok is False and result.message == "Cancelled before anything was changed."
    assert "Cancelled before anything was changed." in lines
    assert not any("Traceback" in line for line in lines)


def test_a_failed_operation_still_gets_its_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_context: JobContext) -> dict[str, object]:
        raise RuntimeError("the disk is full")

    result, lines = _run_service(tmp_path, monkeypatch, fail)

    assert result.ok is False and result.message == "RuntimeError: the disk is full"
    assert any("Traceback" in line for line in lines)


def test_redact_parameters_masks_sensitive_values() -> None:
    safe = redact_parameters(
        {
            "linux_username": "audion",
            "linux_password": "secret-pass",
            "nested": {"api_key": "abc123", "plain": "visible"},
        }
    )

    assert safe["linux_username"] == "audion"
    assert safe["linux_password"] == "***REDACTED***"
    assert safe["nested"]["api_key"] == "***REDACTED***"
    assert safe["nested"]["plain"] == "visible"


def test_a_line_goes_into_the_log_without_the_paint_of_a_console() -> None:
    assert strip_ansi("\x1b[31mred\x1b[0m and \x1b]0;a title\x07plain") == "red and plain"
    # A pipe can swallow the escape and leave the rest of the sequence: "[36m" is noise, "[OK]" is text.
    assert strip_ansi("[36m[OK] done[0m") == "[OK] done"
    assert strip_ansi("tab\there\x00\x08") == "tab\there"
