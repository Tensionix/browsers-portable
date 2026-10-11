"""The console door of Audion Browsers Portable.

A button of the window and the command line run the same thing: an operation of the manifest, by
the same runner (`execute_operation`), with the same defaults of its fields. Before the door an
operation could be run "as in the window" only from the window - the window called the service
itself.

  python system_core/cli.py describe
  python system_core/cli.py run --operation browsers_build --set browsers=chrome,brave --target D:\\Portable
  python system_core/cli.py run --operation browsers_check --source E:\\Flash\\Browsers
  python system_core/cli.py version

The output of `run`: a plain line is a line of the operation's log; a line that starts with
`::browsers::` is a service line and carries JSON - the start, the share of the work done, the
end. The outcome goes to the file of `--result`.

Outcomes and exit codes: `done` 0 - everything asked for was done; `partial` 1 - the operation
ended, but some of the browsers it was given failed (the log names them); `failed` 1; `stopped`
130; a wrong call - 2, and nothing is run.

Paths: a relative `--source` or `--target` is counted from the project folder, as the window
shows them; a relative `--params`, `--result` or `--cancel-file` - from the folder of the caller.

Stopping: the caller creates the file of `--cancel-file`. The operation sees it at its next safe
point and stops by itself - a half-published build and a Defender exclusion are not left behind.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system_core.core import operation_model as model  # noqa: E402
from system_core.core.jobs import execute_operation  # noqa: E402
from system_core.core.manifest import load_manifest  # noqa: E402
from system_core.core.paths import ensure_project_dirs, expand_percent, get_project_paths  # noqa: E402

CONTROL = "::browsers::"
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_STOPPED = 0, 1, 2, 130

# The caller is gone - its window fell, or Windows is going down - and nobody reads the output.
# The operation goes on without it: its own log file is written all the same, and a build ended
# in the middle would leave more behind than a build that finishes unseen.
_nobody_listens = False


def say(line: str) -> None:
    global _nobody_listens
    if _nobody_listens:
        return
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        _nobody_listens = True


def control(payload: dict[str, Any]) -> None:
    say(CONTROL + json.dumps(payload, ensure_ascii=False))


def usage_error(message: str) -> int:
    try:
        print("[ERR] " + message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass
    return EXIT_USAGE


def project(root: str | None):
    paths = get_project_paths(Path(root).resolve() if root else ROOT)
    manifest = paths.config / "tool_manifest.yaml"
    if not manifest.is_file():
        raise ValueError(f"not a project folder, no manifest: {manifest}")
    try:
        return paths, load_manifest(manifest)
    except Exception as error:  # noqa: BLE001 - whatever the manifest stumbles over, nothing was run
        raise ValueError(f"the manifest does not read: {manifest}: {error}") from error


def project_version(paths) -> str:
    try:
        return str(json.loads((paths.config / "version.json").read_text(encoding="utf-8")).get("version", ""))
    except (OSError, ValueError):
        return ""


def command_describe(args: argparse.Namespace) -> int:
    paths, manifest = project(args.root)
    payload = model.describe(manifest, paths.root)
    payload["version"] = project_version(paths)
    payload["paths"] = {"root": str(paths.root), "input": str(paths.input), "output": str(paths.output),
                        "logs": str(paths.logs), "report": str(paths.report), "config": str(paths.config),
                        "workspace": str(paths.workspace)}
    say(json.dumps(payload, ensure_ascii=False))
    return EXIT_OK


def read_values(args: argparse.Namespace, node) -> dict[str, Any]:
    """The values of the fields: the file of `--params`, over it every `--set name=value`."""
    values: dict[str, Any] = {}
    if args.params:
        loaded = json.loads(Path(args.params).read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("--params: a JSON object \"field name: value\" is expected")
        values.update(loaded)
    fields = {model.field_id(field): field for field in model.form_fields(node)}
    for item in args.set or []:
        name, separator, text = item.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"--set: name=value is expected, got {item!r}")
        if name not in fields:
            raise ValueError(f"--set: the operation has no field {name!r}")
        try:
            values[name] = model.coerce(fields[name], text)
        except ValueError as error:
            raise ValueError(f"--set {name}: {error}") from error
    return values


def write_result(path: str, payload: dict[str, Any]) -> str:
    """The outcome into its file, whole or not at all; the reason when it could not be written."""
    if not path:
        return ""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        draft = target.with_name(target.name + ".tmp")
        draft.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        draft.replace(target)
        return ""
    except OSError as error:
        return str(error)


def workbench_path(text: str, fallback: Path, root: Path) -> Path:
    """Source or Target as named by the caller; a relative one is counted from the project folder.

    A variable of the environment is read here, before the path is tied to the project: left for
    the service, `%TEMP%\\x` became `<project>\\C:\\...\\Temp\\x`.
    """
    if not text:
        return fallback
    cleaned = text.strip().strip('"').strip()
    if '"' in cleaned:
        # `"E:\My Builds\" --target D:\Out` reaches a program as one argument with a quote
        # inside: the backslash before the closing quote glued the next key to the path.
        raise ValueError(f"a quotation mark inside a path - was the path quoted with a backslash at its end? {text!r}")
    path = Path(expand_percent(cleaned)).expanduser()
    return path if path.is_absolute() else root / path


def own_file(text: str) -> str:
    """A file of the caller - the values, the outcome, the stop signal - as a full path, counted from where it called."""
    return str(Path(text).expanduser().resolve()) if text else ""


def command_run(args: argparse.Namespace) -> int:
    paths, manifest = project(args.root)
    node = model.find_node(manifest, args.operation)
    if node is None:
        return usage_error(f"the manifest has no operation {args.operation!r}")
    # Before the door moves to the project folder: these three are counted from the caller's.
    args.params, args.result, args.cancel_file = own_file(args.params), own_file(args.result), own_file(args.cancel_file)
    try:
        values = read_values(args, node)
        source = workbench_path(args.source, paths.input, paths.root)
        target = workbench_path(args.target, paths.output, paths.root)
    except (OSError, ValueError) as error:
        return usage_error(str(error))
    unknown = model.unknown_values(node, values)
    if unknown:
        return usage_error("the operation has no fields: " + ", ".join(unknown))
    short = model.short_selections(node, values)
    if short:
        return usage_error("fewer ticks than the field asks for: " + ", ".join(short))
    wrong = model.wrong_choices(node, values, paths.root)
    if wrong:
        return usage_error("not one of the options of the field: " + ", ".join(wrong))
    misshapen = model.wrong_shapes(node, values)
    if misshapen:
        return usage_error("not a value the field can hold: " + ", ".join(misshapen))

    try:
        ensure_project_dirs(paths)
        # Where the window on NiceGUI ran its services from: the program folder.
        os.chdir(ROOT)
    except OSError as error:
        # The call was right and the program folder cannot be written to: an operation that
        # failed, with its outcome said and written, not "a wrong call".
        message = f"{error.__class__.__name__}: {error}"
        write_result(args.result, {"operation": node.id, "outcome": "failed", "ok": False, "message": message,
                                   "failed": 0, "data": {}, "source": str(source), "target": str(target), "seconds": 0})
        say("[ERR] The program folder cannot be prepared: " + message)
        control({"finished": {"outcome": "failed", "message": message, "failed": 0, "seconds": 0}})
        return EXIT_FAILED
    cancel_file = Path(args.cancel_file) if args.cancel_file else None

    parameters = model.build_parameters(node, values, source, target)
    operation = node.to_operation(parameters) if hasattr(node, "to_operation") \
        else dataclasses.replace(node, parameters=parameters)
    run_paths = dataclasses.replace(paths, input=source, output=target)

    control({"started": {"operation": operation.id, "source": str(source), "target": str(target)}})
    began = time.monotonic()
    result = execute_operation(run_paths, operation, say,
                               lambda value: control({"progress": round(float(value), 4)}),
                               (lambda: cancel_file.exists()) if cancel_file is not None else None,
                               # Where a long step is right now: a line the caller rewrites in place.
                               lambda text: control({"pulse": str(text)}))
    # A stop is what the operation says it is - it raised a cancel, or came back early and said
    # so. A stop signal that lies there while something else broke does not make a failure a stop.
    stopped = bool(result.data.get("cancelled"))
    failed = model.failed_items(result.data) if result.ok else 0
    outcome = "stopped" if stopped else "failed" if not result.ok else "partial" if failed else "done"
    seconds = round(time.monotonic() - began, 1)
    trouble = write_result(args.result, {"operation": operation.id, "outcome": outcome, "ok": result.ok,
                                         "message": result.message, "failed": failed, "data": result.data,
                                         "source": str(source), "target": str(target), "seconds": seconds})
    if trouble:
        say(f"[WARN] The outcome could not be written to {args.result}: {trouble}")
    control({"finished": {"outcome": outcome, "message": result.message, "failed": failed, "seconds": seconds}})
    return EXIT_STOPPED if stopped else EXIT_OK if outcome == "done" else EXIT_FAILED


def command_version(args: argparse.Namespace) -> int:
    paths, _manifest = project(args.root)
    say(project_version(paths))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browsers_cli", description="Audion Browsers Portable: the console door.")
    parser.add_argument("--root", default="", help="The project folder, when it is not the one the door lies in")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("describe", help="The manifest as JSON: sections, operations, fields").set_defaults(func=command_describe)

    run = commands.add_parser("run", help="Run an operation of the manifest")
    run.add_argument("--operation", required=True, help="The id of the operation")
    run.add_argument("--source", default="", help="Source: a build or a folder of builds; input by default")
    run.add_argument("--target", default="", help="Target: where finished builds go; output by default")
    run.add_argument("--params", default="", help="A JSON file with the values of the fields by their names")
    run.add_argument("--set", action="append", metavar="NAME=VALUE", help="The value of one field; may be repeated")
    run.add_argument("--result", default="", help="Where to write the outcome of the operation (JSON)")
    run.add_argument("--cancel-file", default="", help="The stop signal: once the file appears, the operation stops")
    run.set_defaults(func=command_run)

    commands.add_parser("version", help="The version number").set_defaults(func=command_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code not in (0, None) else EXIT_OK
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except (OSError, ValueError) as error:
        # A project that is not one - no manifest, a manifest that does not read: nothing was
        # run, and that is a wrong call, not an operation that failed.
        return usage_error(f"{error.__class__.__name__}: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
