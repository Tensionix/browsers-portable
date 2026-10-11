"""The console door: a button of the window and the command line run the same thing.

The door (`system_core/cli.py`) builds the parameters of an operation by the rules the window on
NiceGUI had and runs it by the same runner. Both are checked here: the parameters of every
operation of the manifest - against what that window sent, the run - by a real process of the door.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from system_core import cli
from system_core.core import operation_model as model
from system_core.core.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_manifest(ROOT / "config" / "tool_manifest.yaml")
FROZEN = json.loads((ROOT / "tests" / "former_window_parameters.json").read_text(encoding="utf-8"))
SOURCE, TARGET = FROZEN["source_mark"], FROZEN["target_mark"]


def all_nodes(nodes=None):
    for node in (MANIFEST.operation_groups if nodes is None else nodes):
        yield node
        yield from all_nodes(node.children)


def runnable():
    return [node for node in all_nodes() if node.service] + list(MANIFEST.maintenance_operations)


def described_nodes(nodes):
    for node in nodes:
        yield node
        yield from described_nodes(node["children"])


def test_every_field_is_of_a_kind_the_window_can_draw() -> None:
    described = model.describe(MANIFEST, ROOT)
    nodes = list(described_nodes(described["nodes"])) + described["maintenance"]
    fields = [field for node in nodes for field in node["fields"]]

    # A field of an unknown kind is not an empty place in the form but a refused check.
    assert {field["kind"] for field in fields} - set(model.FIELD_KINDS) == set()
    assert sum(node["runs"] for node in nodes) == len(runnable())
    assert len(fields) > 30
    assert not [field["id"] for field in fields if field.get("options_error")]
    json.dumps(described, ensure_ascii=False)


def test_a_field_named_in_several_forms_is_the_same_field() -> None:
    """The window keeps a value by the id of its field, whichever form shows it.

    `browsers` ticked in Build is ticked in Update too. That holds only while the field is the
    same everywhere: a kind, a default or a set of options that differed between two forms would
    hand one of them a value it does not have.
    """
    described = model.describe(MANIFEST, ROOT)
    nodes = list(described_nodes(described["nodes"])) + described["maintenance"]
    seen: dict[str, tuple[str, dict]] = {}
    differing = []
    for node in nodes:
        for field in node["fields"]:
            shape = {"kind": field["kind"], "default": field.get("default"),
                     "options": [option["value"] for option in field.get("options") or []]}
            first_node, first = seen.setdefault(field["id"], (node["id"], shape))
            if first != shape:
                differing.append(f"{field['id']}: {first_node} {first} != {node['id']} {shape}")

    assert differing == []


def test_options_of_a_provider_are_listed_by_the_door() -> None:
    described = model.describe(MANIFEST, ROOT)
    build = next(node for node in described_nodes(described["nodes"]) if node["id"] == "browsers_build")
    browsers = next(field for field in build["fields"] if field["id"] == "browsers")

    assert [option["value"] for option in browsers["options"]][:2] == ["chrome", "yandex"]
    assert browsers["min_selected"] == 1 and browsers["group"] == "source" and browsers["advanced"] is False


def test_a_failed_provider_is_named_and_the_form_still_draws(tmp_path: Path) -> None:
    field = {"id": "x", "type": "checkboxes", "options_source": "no_such_module_for_the_door:options"}

    row = model.describe_field(field, tmp_path)

    assert row["options"] == [] and "ModuleNotFoundError" in row["options_error"]


def test_advanced_fields_and_blocks_are_told_by_the_door() -> None:
    build = model.find_node(MANIFEST, "browsers_build")
    rows = {model.field_id(field): model.describe_field(field, ROOT) for field in model.form_fields(build)}

    assert [name for name, row in rows.items() if row["advanced"]] == ["chrome_download_url", "yandex_download_url", "keep_temp"]
    assert {row["group"] for row in rows.values()} == {"source", "options", "format", "output", "run"}
    # A field the manifest gives no block is placed by what it is.
    assert model.field_group({"id": "archive_format", "type": "radio"}) == "format"
    assert model.field_group({"id": "anything", "type": "checkbox"}) == "options"


def test_a_guest_is_told_whose_panel_it_shares() -> None:
    described = model.describe(MANIFEST, ROOT)
    nodes = {node["id"]: node for node in described_nodes(described["nodes"])}

    assert nodes["browsers_check"]["beside"] == "browsers_update"
    # A guest reads the fields of its host: every field it has, the host has too.
    assert {field["id"] for field in nodes["browsers_check"]["fields"]} <= {field["id"] for field in nodes["browsers_update"]["fields"]}
    assert nodes["certificates_install"]["direct"] is True and nodes["certificates_install"]["kind"] == "dangerous"
    assert nodes["browsers_build"]["direct"] is False


def test_values_from_the_command_line_take_the_shape_of_the_field() -> None:
    assert model.coerce({"type": "checkbox"}, "true") is True
    assert model.coerce({"type": "checkbox"}, "off") is False
    assert model.coerce({"type": "number"}, "2,5") == 2.5
    assert model.coerce({"type": "number"}, "3") == 3
    assert model.coerce({"type": "checkboxes"}, "chrome, brave") == ["chrome", "brave"]
    assert model.coerce({"type": "radio"}, "7z") == "7z"
    with pytest.raises(ValueError):
        model.coerce({"type": "checkbox"}, "maybe")


def test_a_value_the_operation_does_not_have_is_named() -> None:
    update = model.find_node(MANIFEST, "browsers_update")

    assert model.unknown_values(update, {"force_update": True, "force": True}) == ["force"]
    assert model.find_node(MANIFEST, "update") is None                    # a section is not an operation
    assert model.find_node(MANIFEST, "cleanup_workspace") is not None
    assert model.short_selections(model.find_node(MANIFEST, "browsers_build"), {}) == ["browsers"]
    assert model.short_selections(update, {}) == []                        # nothing ticked - every build in Source


def signature(node) -> str:
    """What the parameters of an operation depend on: the name, kind and default of every field, the
    values of its options, and the parameters the operation sets itself. No captions: a renamed
    checkbox changes no parameter."""
    fields = []
    for field in node.fields:
        shape = {key: field.get(key) for key in ("id", "name", "type", "kind", "default")}
        shape["options"] = [option.get("value") if isinstance(option, dict) else option
                            for option in field.get("options") or []]
        fields.append(shape)
    raw = json.dumps({"fields": fields, "parameters": dict(node.parameters)}, ensure_ascii=False, sort_keys=True,
                     default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def frozen_rows():
    """The operations whose fields are still what they were when the former window was written down.

    tests/former_window_parameters.json holds what `system_core/ui_nicegui/app.py` of 1.4.0 sent to a
    service for every operation - with the defaults and with changed values. An operation changed in
    the manifest since no longer answers to it and is not compared.
    """
    for node in runnable():
        row = FROZEN["operations"].get(node.id)
        if row is not None and row["signature"] == signature(node):
            yield node, row


def test_most_operations_still_answer_to_the_former_window() -> None:
    assert set(FROZEN["operations"]) == {node.id for node in runnable()}
    # So that the comparison below cannot quietly come to compare nothing.
    assert sum(1 for _ in frozen_rows()) * 2 >= len(FROZEN["operations"])


@pytest.mark.parametrize("operation_id", sorted(FROZEN["operations"]))
def test_the_door_builds_what_the_former_window_built(operation_id: str) -> None:
    rows = {node.id: (node, row) for node, row in frozen_rows()}
    if operation_id not in rows:
        pytest.skip("the operation was changed in the manifest after the former window was written down")
    node, row = rows[operation_id]

    assert plain(model.build_parameters(node, {}, SOURCE, TARGET)) == row["defaults"]
    assert plain(model.build_parameters(node, row["changed_values"], SOURCE, TARGET)) == row["changed"]
    assert [model.field_id(field) for field in model.form_fields(node)] == row["form_fields"]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project root of its own: the logs and reports of trial runs do not go into the real one."""
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "system_core").mkdir()
    shutil.copy2(ROOT / "config" / "tool_manifest.yaml", root / "config" / "tool_manifest.yaml")
    (root / "config" / "version.json").write_text('{"version": "9.9.9"}', encoding="utf-8")
    return root


def door(project: Path, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-u", str(ROOT / "system_core" / "cli.py"), "--root", str(project), *args],
                          cwd=str(cwd or ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace", timeout=120, env=env)


def controls(output: str) -> list[dict]:
    return [json.loads(line[len(cli.CONTROL):]) for line in output.splitlines() if line.startswith(cli.CONTROL)]


def test_an_operation_runs_and_says_how_it_ended(project: Path, tmp_path: Path) -> None:
    leftover = project / "workspace" / "chrome" / "installer.exe"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"x")
    result_file = tmp_path / "result.json"

    done = door(project, "run", "--operation", "cleanup_workspace", "--result", str(result_file))

    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert (result["operation"], result["outcome"], result["ok"]) == ("cleanup_workspace", "done", True)
    assert (result["source"], result["target"]) == (str(project / "input"), str(project / "output"))
    assert not leftover.exists()
    events = controls(done.stdout)
    assert events[0]["started"]["operation"] == "cleanup_workspace"
    assert events[-1]["finished"]["outcome"] == "done"
    assert any("progress" in event for event in events)
    # Plain lines of the log go as they are, the service lines apart; the log lies in the project of the run.
    assert "Starting operation: cleanup_workspace" in done.stdout
    assert [path.name.endswith("_cleanup_workspace.log") for path in (project / "logs").iterdir()] == [True]


def test_a_relative_source_is_counted_from_the_project(project: Path, tmp_path: Path) -> None:
    result_file = tmp_path / "result.json"

    done = door(project, "run", "--operation", "cleanup_workspace", "--source", "builds", "--target", str(tmp_path / "out"),
                "--result", str(result_file))

    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert (result["source"], result["target"]) == (str(project / "builds"), str(tmp_path / "out"))


def test_only_a_percent_variable_is_read_in_a_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`%NAME%` as cmd and the window read it. A folder called `$TEMP` is a folder, not a variable."""
    monkeypatch.setenv("BROWSERS_STAND", str(tmp_path))
    fallback, root = tmp_path / "input", tmp_path / "project"

    assert cli.workbench_path("%BROWSERS_STAND%\\Builds", fallback, root) == tmp_path / "Builds"
    assert cli.workbench_path("E:\\$BROWSERS_STAND\\Builds", fallback, root) == Path("E:\\$BROWSERS_STAND\\Builds")
    assert cli.workbench_path("E:\\${BROWSERS_STAND}\\Builds", fallback, root) == Path("E:\\${BROWSERS_STAND}\\Builds")
    # A name nobody set stays as it was written, and so does a lone percent sign.
    assert cli.workbench_path("E:\\%NO_SUCH_NAME_HERE%\\50%", fallback, root) == Path("E:\\%NO_SUCH_NAME_HERE%\\50%")
    assert cli.workbench_path("", fallback, root) == fallback


@pytest.mark.parametrize("args", [
    ("run", "--operation", "no_such_operation"),
    ("run", "--operation", "update"),                                          # a section is not an operation
    ("run", "--operation", "browsers_build"),                                  # no browser ticked
    ("run", "--operation", "browsers_build", "--set", "browsers=chrome", "--set", "force=true"),
    ("run", "--operation", "browsers_build", "--set", "browsers=chrome", "--set", "package_archive=maybe"),
    # A value that is none of the options: the service would have taken its default and said "done".
    ("run", "--operation", "browsers_build", "--set", "browsers=chrome", "--set", "portable_engine=chrome++"),
    ("run", "--operation", "browsers_build", "--set", "browsers=chrome", "--set", "archive_format=7zip"),
    ("run", "--operation", "browsers_build", "--set", "browsers=chrome,netscape"),
    # A backslash before the closing quote glues the next key to the path: one argument with a quote inside.
    ("run", "--operation", "browsers_check", "--source", 'E:\\My Builds" --target D:\\Out'),
    ("run",),
    (),
])
def test_a_wrong_call_is_refused_before_anything_runs(project: Path, args: tuple[str, ...]) -> None:
    done = door(project, *args)

    assert done.returncode == 2, done.stdout + done.stderr
    assert not (project / "logs").exists()


def test_a_folder_that_is_no_project_is_a_wrong_call_not_a_failed_operation(tmp_path: Path) -> None:
    done = door(tmp_path / "nothing here", "run", "--operation", "cleanup_workspace")
    assert done.returncode == 2 and "no manifest" in done.stderr and "Traceback" not in done.stderr

    broken = tmp_path / "broken" / "config"
    broken.mkdir(parents=True)
    (broken / "tool_manifest.yaml").write_text("operations: [", encoding="utf-8")
    done = door(tmp_path / "broken", "describe")
    assert done.returncode == 2 and "does not read" in done.stderr and "Traceback" not in done.stderr


def test_the_files_of_the_caller_are_counted_from_where_it_called(project: Path, tmp_path: Path) -> None:
    """A relative --result or --cancel-file lies beside the caller, not in the program folder."""
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "stop.flag").write_text("stop", encoding="ascii")
    (project / "input").mkdir()
    (project / "output").mkdir()
    (project / "output" / "kept.txt").write_text("x", encoding="utf-8")

    done = door(project, "run", "--operation", "cleanup_input_output", "--cancel-file", "stop.flag",
                "--result", "outcome.json", cwd=caller)

    assert done.returncode == 130, done.stdout + done.stderr           # the stop signal of the caller was seen
    assert json.loads((caller / "outcome.json").read_text(encoding="utf-8"))["outcome"] == "stopped"
    assert not (ROOT / "outcome.json").exists() and (project / "output" / "kept.txt").is_file()


@pytest.fixture()
def captured(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The door run inside the test, with the runner replaced: what would have gone to a service is kept."""
    seen: dict = {"result": cli.execute_operation.__globals__["JobResult"](True, "Operation finished.", {})}

    def runner(paths, operation, log, progress, cancel, pulse):
        seen.update(paths=paths, operation=operation, cancel=cancel)
        log("a line of the operation")
        progress(0.5)
        pulse("[DOWNLOAD] a file: 50% | 1.0 / 2.0 MB | 0.5 MB/s")
        return seen["result"]

    monkeypatch.setattr(cli, "execute_operation", runner)
    monkeypatch.setattr(cli, "_nobody_listens", False)
    monkeypatch.chdir(tmp_path)                                       # the door moves to the program folder
    seen["run"] = lambda *args: cli.main(["--root", str(project), "run", *args])
    return seen


def test_the_values_of_the_caller_reach_the_service(captured, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    values = tmp_path / "values.json"
    values.write_text(json.dumps({"browsers": ["chrome", "brave"], "package_archive": True, "archive_format": "7z"}), encoding="utf-8")
    monkeypatch.setenv("AUDION_TEST_PLACE", str(tmp_path / "flash"))

    code = captured["run"]("--operation", "browsers_build", "--params", str(values), "--set", "keep_temp=true",
                           "--source", "builds", "--target", "%AUDION_TEST_PLACE%\\Portable")

    assert code == 0
    sent = captured["operation"].parameters
    assert (sent["browsers"], sent["package_archive"], sent["archive_format"], sent["keep_temp"]) == (["chrome", "brave"], True, "7z", True)
    assert sent["portable_engine"] == "proxy_library" and sent["guard_defender"] is True      # untouched fields: the defaults
    # A relative Source is counted from the project; a variable is read before the path is tied to it.
    source, target = str(project / "builds"), str(tmp_path / "flash" / "Portable")
    assert (sent["input_path"], sent["gui_input_dir"], sent["output_path"], sent["gui_output_dir"]) == (source, source, target, target)
    assert (str(captured["paths"].input), str(captured["paths"].output)) == (source, target)
    assert captured["operation"].service.endswith(":build_selected") and captured["cancel"] is None


def run_outcome(captured, tmp_path: Path, result, *extra: str) -> tuple[int, dict]:
    captured["result"] = result
    target = tmp_path / "outcome.json"
    code = captured["run"]("--operation", "browsers_update", "--result", str(target), *extra)
    return code, json.loads(target.read_text(encoding="utf-8"))


def test_how_a_run_ended_is_what_the_operation_says(captured, tmp_path: Path) -> None:
    result = type(captured["result"])
    stop = tmp_path / "stop.flag"
    stop.write_text("stop", encoding="ascii")

    code, told = run_outcome(captured, tmp_path, result(True, "ok", {"updated": [1], "failed": []}))
    assert (code, told["outcome"], told["failed"]) == (0, "done", 0)
    # Two browsers of three failed: the operation ended, but not everything asked for was done.
    code, told = run_outcome(captured, tmp_path, result(True, "ok", {"updated": [1], "failed": [{"browser": "a"}, {"browser": "b"}]}))
    assert (code, told["outcome"], told["failed"]) == (1, "partial", 2)
    code, told = run_outcome(captured, tmp_path, result(False, "Cancelled before the download.", {"cancelled": True}), "--cancel-file", str(stop))
    assert (code, told["outcome"]) == (130, "stopped")
    # Something broke while the stop signal lay there: a failure, with its reason, not a stop.
    code, told = run_outcome(captured, tmp_path, result(False, "OSError: the disk is full", {}), "--cancel-file", str(stop))
    assert (code, told["outcome"], told["message"]) == (1, "failed", "OSError: the disk is full")


def test_a_pulse_is_a_service_line_of_its_own(captured, capsys: pytest.CaptureFixture[str]) -> None:
    """Where a long step is right now goes to the caller as a state to show in place, not as a line of the journal."""
    assert captured["run"]("--operation", "browsers_update") == 0

    lines = capsys.readouterr().out.splitlines()
    assert {"pulse": "[DOWNLOAD] a file: 50% | 1.0 / 2.0 MB | 0.5 MB/s"} in controls("\n".join(lines))
    assert not [line for line in lines if "[DOWNLOAD]" in line and not line.startswith(cli.CONTROL)]


class Listener:
    """A context of a job as the door, or as nobody, listens to it."""

    def __init__(self, pulses: bool) -> None:
        self.logged: list[str] = []
        self.pulsed: list[str] = []
        self.pulse_callback = self.pulsed.append if pulses else None

    def log(self, message: str) -> None:
        self.logged.append(message)

    def pulse(self, message: str) -> None:
        self.pulsed.append(message)


def test_a_download_tells_its_way_as_the_listener_can_take_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from system_core.core import jobs

    megabyte = 1024 * 1024
    clock = iter(range(1000))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: float(next(clock)))

    # Nobody shows a pulse - the window before this one, a log file: a line at every tenth, as it always was.
    plain = Listener(pulses=False)
    note = jobs.TransferNote(plain, "Chrome 140: ")
    for done in range(1, 201):
        note.at(done * megabyte, 200 * megabyte)
    assert len(plain.logged) == 11 and plain.pulsed == []
    assert plain.logged[1] == "[DOWNLOAD] Chrome 140: 10% (20,971,520/209,715,200 bytes)"
    assert plain.logged[-1].startswith("[DOWNLOAD] Chrome 140: 100% ")

    # The door listens: the way is a pulse - megabytes and speed - and the log gets no line of it.
    live = Listener(pulses=True)
    note = jobs.TransferNote(live, "", "Vivaldi 7.5: ")
    for done in range(1, 201):
        note.at(done * megabyte, 200 * megabyte)
    assert live.logged == []
    assert live.pulsed[0] == "[DOWNLOAD] Vivaldi 7.5: 0% | 1.0 / 200.0 MB | 0.0 MB/s"
    assert live.pulsed[-1] == "[DOWNLOAD] Vivaldi 7.5: 100% | 200.0 / 200.0 MB | 1.0 MB/s"
    # A file whose size the server did not name has no way to tell.
    note.at(5 * megabyte, 0)
    assert len(live.pulsed) == 200


def test_the_door_outlives_whoever_listened(captured, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window fell and nobody reads the output: the operation goes on, and its outcome is written."""
    class Gone:
        def write(self, _text: str) -> int:
            raise OSError(22, "Invalid argument")

        def flush(self) -> None:
            raise OSError(22, "Invalid argument")

        def reconfigure(self, **_options) -> None:
            pass

    monkeypatch.setattr(sys, "stdout", Gone())

    code, told = run_outcome(captured, tmp_path, type(captured["result"])(True, "ok", {}))

    assert (code, told["outcome"]) == (0, "done")
    assert captured["operation"].id == "browsers_update"            # the runner was reached and came back


def test_an_outcome_that_cannot_be_written_does_not_change_how_the_run_ended(captured, tmp_path: Path, capsys) -> None:
    blocked = tmp_path / "a file, not a folder"
    blocked.write_text("x", encoding="utf-8")

    code = captured["run"]("--operation", "browsers_update", "--result", str(blocked / "outcome.json"))

    said = capsys.readouterr().out
    assert code == 0 and "[WARN] The outcome could not be written" in said
    assert controls(said)[-1]["finished"]["outcome"] == "done"


def test_a_stop_file_stops_the_operation_and_the_door_says_so(project: Path, tmp_path: Path) -> None:
    # The stop file lies there from the start: the cleanup empties input, sees the signal and leaves output alone.
    kept = project / "output" / "Portable" / "build.txt"
    kept.parent.mkdir(parents=True)
    kept.write_text("a finished build", encoding="utf-8")
    (project / "input").mkdir()
    (project / "input" / "old.txt").write_text("x", encoding="utf-8")
    stop = tmp_path / "stop.flag"
    stop.write_text("stop", encoding="ascii")
    result_file = tmp_path / "result.json"

    done = door(project, "run", "--operation", "cleanup_input_output", "--cancel-file", str(stop),
                "--result", str(result_file))

    assert done.returncode == 130, done.stdout + done.stderr
    assert json.loads(result_file.read_text(encoding="utf-8"))["outcome"] == "stopped"
    assert controls(done.stdout)[-1]["finished"]["outcome"] == "stopped"
    assert kept.is_file() and not (project / "input" / "old.txt").exists()


def test_version_and_description_come_from_the_project(project: Path) -> None:
    assert door(project, "version").stdout.strip() == "9.9.9"
    described = json.loads(door(project, "describe").stdout)

    assert (described["version"], described["paths"]["root"]) == ("9.9.9", str(project.resolve()))
    assert [node["id"] for node in described["nodes"]] == [node.id for node in MANIFEST.operation_groups]
    assert [node["id"] for node in described["maintenance"]] == [node.id for node in MANIFEST.maintenance_operations]


def test_the_wrapper_keeps_crlf_and_passes_the_exit_code() -> None:
    raw = (ROOT / "browsers_cli.cmd").read_bytes()

    assert raw.count(b"\n") == raw.count(b"\r\n")                             # cmd stumbles over a lone LF
    raw.decode("ascii")
    assert b'"%BASE_DIR%\\system_core\\cli.py" %*\r\nexit /b %ERRORLEVEL%' in raw
    # The folder of the caller stays the current one: its relative files are counted from it.
    assert b"pushd" not in raw


def test_the_wrapper_runs_from_a_folder_with_brackets_and_spaces(tmp_path: Path) -> None:
    """`C:\\Program Files (x86)\\...`: a bracket of the path inside a block in brackets closed the block,
    and cmd refused the whole file before Python was even looked for."""
    home = tmp_path / "Program Files (x86)" / "Audion Browsers Portable"
    home.mkdir(parents=True)
    wrapper = home / "browsers_cli.cmd"
    shutil.copy2(ROOT / "browsers_cli.cmd", wrapper)

    # No Python beside this copy: the wrapper has to say so itself, in its own words - which it
    # does only if cmd could read the file to its end.
    absent = subprocess.run(f'cmd.exe /d /s /c ""{wrapper}" version"', stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", timeout=60)

    assert absent.returncode == 2 and "Python of the project was not found" in absent.stdout, absent.stdout
    assert str(home) in absent.stdout


def test_the_wrapper_of_the_program_answers_and_passes_the_exit_code(tmp_path: Path) -> None:
    """The real wrapper, called from another folder: the door answers, and its exit code comes through."""
    if not (ROOT / "runtime" / "python.exe").is_file():
        pytest.skip("the environment of the project is not built")
    wrapper = str(ROOT / "browsers_cli.cmd")

    def call(*args: str) -> subprocess.CompletedProcess:
        # The line as a console holds it. With /s cmd takes off the outer pair of quotes alone;
        # without it, two quoted arguments on one line lose the quotes of the first.
        line = " ".join(f'"{item}"' for item in (wrapper, *args))
        return subprocess.run(f'cmd.exe /d /s /c "{line}"', stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(tmp_path))

    version = call("version")
    assert version.returncode == 0 and version.stdout.strip().splitlines()[-1] == cli.project_version(cli.get_project_paths(ROOT))
    refused = call("--root", str(tmp_path / "no project"), "version")
    assert refused.returncode == 2 and "no manifest" in refused.stdout, refused.stdout


@pytest.mark.parametrize("values", [
    {"browsers": ["chrome"], "package_archive": "maybe"},     # refused by --set, and ran as "off" from the file
    {"browsers": ["chrome"], "package_archive": 1},
    {"browsers": "chrome"},                                    # a group of ticks is a list of names
    {"browsers": ["chrome"], "chrome_download_url": ["https://example.test/a.exe"]},
])
def test_a_value_of_the_wrong_shape_in_the_file_of_values_is_a_wrong_call(project: Path, tmp_path: Path, values: dict) -> None:
    params = tmp_path / "params.json"
    params.write_text(json.dumps(values), encoding="utf-8")

    done = door(project, "run", "--operation", "browsers_build", "--params", str(params))

    # A name where a list of ticks is expected is counted as no ticks first: refused all the same.
    refusals = ("not a value the field can hold", "fewer ticks than the field asks for")
    assert done.returncode == 2 and any(text in done.stderr for text in refusals), done.stdout + done.stderr
    assert not (project / "logs").exists()


def test_the_shapes_of_values_a_window_sends_are_all_taken() -> None:
    """What the window writes for every operation - the defaults of the manifest - fits its own fields."""
    for node in runnable():
        values = {model.field_id(field): model.field_default(field) for field in model.form_fields(node)}
        assert model.wrong_shapes(node, values) == [], node.id
