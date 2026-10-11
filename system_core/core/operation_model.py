"""An operation of the manifest as data: its fields, their defaults, the parameters of a run.

What a window knows about an operation without it being a matter of looks: the default of a
field, which fields are kept under "Advanced", which block of the form a field belongs to, and
which parameters go to the service. While the window on NiceGUI held these rules itself, an
operation could be run "as in the window" only from the window. Here is one copy of them - for
the console door (`system_core/cli.py`) and for the window that calls the door.

The rules are carried over from `system_core/ui_nicegui/app.py` unchanged; that they give the same
parameters for every operation of the manifest is checked by tests/test_cli_door.py.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from .manifest import CommandNode, Operation, ToolManifest

FIELD_KINDS = {
    "checkbox": {"checkbox", "bool", "boolean", "toggle"},
    "checkboxes": {"checkboxes", "multi_checkbox", "multicheckbox", "multi-select", "multiselect"},
    "number": {"number", "int", "integer", "float"},
    "select": {"select", "choice", "format"},
    "radio": {"radio", "radiobuttons", "radio-buttons"},
    "text": {"text", "textarea", "multiline", "string", ""},
}
# The paths of the workbench: a field of this name is not drawn, its value is Source or Target.
ROUTE_FIELDS = {"gui_input_dir": "source", "gui_output_dir": "target"}
# The names the services read the workbench by; every run carries all four.
SOURCE_PARAMETERS = ("gui_input_dir", "input_path")
TARGET_PARAMETERS = ("gui_output_dir", "output_path")
ADVANCED_MARKS = {"advanced", "expert", "rare"}
# Operations a window gives no button of their own: its workbench clears the very paths chosen
# there, asking first. They stay operations of the door. So the window on NiceGUI had it.
WORKBENCH_OPERATIONS = {"cleanup_input_output"}
GROUP_KEYS = ("group", "ui_group", "section_group")


def field_id(field: dict[str, Any]) -> str:
    return str(field.get("id") or field.get("name") or "").strip()


def raw_kind(field: dict[str, Any]) -> str:
    return str(field.get("type", field.get("kind", "text"))).lower()


def field_kind(field: dict[str, Any]) -> str:
    """The kind of a field as one of six words; an unknown kind comes back as it is - a check notices it."""
    kind = raw_kind(field)
    for name, aliases in FIELD_KINDS.items():
        if kind in aliases:
            return name
    return kind


def route_of(field: dict[str, Any]) -> str:
    """"source" or "target" when the field is a path of the workbench and is not drawn; else empty."""
    return ROUTE_FIELDS.get(field_id(field), "")


def field_default(field: dict[str, Any]) -> Any:
    if "default" in field:
        return field["default"]
    options = field.get("options", [])
    if field_kind(field) == "checkboxes":
        if not isinstance(options, list):
            return []
        return [option.get("value", option.get("id", option.get("label")))
                for option in options if isinstance(option, dict) and option.get("default", False)]
    if isinstance(options, list) and options:
        first = options[0]
        if isinstance(first, dict):
            return first.get("value", first.get("id", ""))
        return first
    return ""


def is_advanced(field: dict[str, Any]) -> bool:
    """A field kept under "Advanced": rarely needed, set apart from the everyday ones."""
    if bool(field.get("advanced", False)):
        return True
    return str(field.get("priority") or field.get("section") or "").strip().lower() in ADVANCED_MARKS


def field_group(field: dict[str, Any]) -> str:
    """The block of the form a field stands in: named by the manifest, else told by the field itself."""
    key = field_id(field)
    kind = field_kind(field)
    section = str(field.get("section") or "").strip().lower()
    explicit = next((str(field[name]).strip().lower() for name in GROUP_KEYS if field.get(name)), "")
    if not explicit and section and section not in ADVANCED_MARKS:
        explicit = section
    if explicit:
        return explicit
    if key in {"overwrite", "dry_run", "limit_first_file", "test_first_file"} or key.endswith(("_dry_run", "_overwrite")):
        return "run"
    if any(part in key for part in ("source", "input", "url", "file", "folder", "path")):
        return "source"
    if any(part in key for part in ("format", "container", "profile", "preset", "quality", "dpi", "bitrate", "resolution")):
        return "format"
    if any(part in key for part in ("output", "report", "export", "package", "release")):
        return "output"
    if any(part in key for part in ("codec", "encode", "model", "engine")):
        return "encoding"
    if kind in {"checkbox", "checkboxes"}:
        return "options"
    return "parameters"


def form_fields(node: CommandNode | Operation) -> list[dict[str, Any]]:
    """The fields a person sees in the form of an operation: the paths of the workbench are not among them."""
    return [field for field in node.fields if field_id(field) and not route_of(field)]


def is_direct(node: CommandNode | Operation) -> bool:
    """An operation with nothing to set: it runs at a click."""
    return bool(node.service) and not getattr(node, "children", ()) and not form_fields(node)


def build_parameters(node: CommandNode | Operation, values: dict[str, Any], source: str | Path,
                     target: str | Path) -> dict[str, Any]:
    """The parameters of a run: what the manifest sets, the values of the form, the defaults of the rest,
    and the two paths of the workbench under every name a service reads them by."""
    parameters = dict(node.parameters)
    for field in node.fields:
        key = field_id(field)
        if key:
            parameters[key] = values.get(key, field_default(field))
    for name in SOURCE_PARAMETERS:
        parameters[name] = str(source)
    for name in TARGET_PARAMETERS:
        parameters[name] = str(target)
    return parameters


def unknown_values(node: CommandNode | Operation, values: dict[str, Any]) -> list[str]:
    """The names of values the operation has no field for: a mistyped name must not vanish quietly."""
    known = {field_id(field) for field in form_fields(node)}
    return sorted(key for key in values if key not in known)


def short_selections(node: CommandNode | Operation, values: dict[str, Any]) -> list[str]:
    """The groups of checkboxes with fewer ticks than the field asks for (`min_selected`)."""
    short = []
    for field in form_fields(node):
        if field_kind(field) != "checkboxes":
            continue
        wanted = int(field.get("min_selected", 0) or 0)
        chosen = values.get(field_id(field), field_default(field))
        if wanted > 0 and (not isinstance(chosen, list) or len(chosen) < wanted):
            short.append(field_id(field))
    return short


def wrong_choices(node: CommandNode | Operation, values: dict[str, Any], root: Path) -> list[str]:
    """Values that are none of the options of their field, as "field=value".

    A window cannot give such a value; a command line can, and the services answer an option
    they do not know with their default - `portable_engine=chrome++` built with the proxy
    library and said "done".
    """
    wrong = []
    for field in form_fields(node):
        key, kind = field_id(field), field_kind(field)
        if key not in values or kind not in {"radio", "select", "checkboxes"}:
            continue
        options, _error = listed_options(field, root)
        known = {str(option.get("value", option.get("id", option.get("label", ""))) if isinstance(option, dict) else option)
                 for option in options}
        if not known:
            continue
        given = values[key] if kind == "checkboxes" else [values[key]]
        if not isinstance(given, (list, tuple)):
            given = [given]
        wrong.extend(f"{key}={item}" for item in given if str(item) not in known)
    return wrong


def wrong_shapes(node: CommandNode | Operation, values: dict[str, Any]) -> list[str]:
    """Values of a kind their field cannot hold, as "field=value".

    `--set` shapes a value by its field and refuses what does not fit; the file of `--params`
    was taken as it came: `"package_archive": "maybe"` was refused on the command line and ran
    as "off" from the file. A flag is true or false, a number is a finite number, a group of
    ticks is a list of names.
    """
    wrong = []
    for field in form_fields(node):
        key, kind = field_id(field), field_kind(field)
        if key not in values:
            continue
        value = values[key]
        if kind == "checkbox":
            fits = isinstance(value, bool)
        elif kind == "number":
            fits = isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
                and value not in (float("inf"), float("-inf"))
        elif kind == "checkboxes":
            fits = isinstance(value, list) and all(isinstance(item, str) for item in value)
        elif kind == "text":
            fits = not isinstance(value, (list, dict, bool))
        else:
            fits = not isinstance(value, (list, dict))
        if not fits:
            wrong.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    return wrong


def failed_items(data: dict[str, Any]) -> int:
    """How many browsers an operation that ended well says it could not do (`failed` in its answer)."""
    failed = data.get("failed") if isinstance(data, dict) else None
    return len(failed) if isinstance(failed, (list, tuple)) else 0


def coerce(field: dict[str, Any], text: str) -> Any:
    """A value of the command line (`--set name=value`) in the shape the field expects."""
    kind = field_kind(field)
    if kind == "checkbox":
        lowered = text.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
        raise ValueError("true or false is expected")
    if kind == "number":
        number = float(text.replace(",", "."))
        return int(number) if number.is_integer() else number
    if kind == "checkboxes":
        return [item.strip() for item in text.split(",") if item.strip()]
    return text


def find_node(manifest: ToolManifest, operation_id: str) -> CommandNode | Operation | None:
    def search(nodes) -> CommandNode | None:
        for node in nodes:
            if node.id == operation_id and node.service:
                return node
            found = search(node.children)
            if found is not None:
                return found
        return None

    found = search(manifest.operation_groups)
    if found is not None:
        return found
    return next((item for item in [*manifest.operations, *manifest.maintenance_operations]
                 if item.id == operation_id), None)


def option_source(field: dict[str, Any]) -> str:
    return str(field.get("options_source") or field.get("source") or "").strip()


def listed_options(field: dict[str, Any], root: Path) -> tuple[list[Any], str]:
    """The options of a field and, when its provider failed, why.

    A field may name a provider (`options_source: module:function`) instead of listing its options;
    the provider is called the way the window on NiceGUI called it, with fewer arguments each try.
    """
    source = option_source(field)
    if source:
        try:
            if ":" not in source:
                raise RuntimeError(f"Dynamic option source must use module:function syntax: {source}")
            module_name, function_name = source.split(":", 1)
            provider = getattr(importlib.import_module(module_name), function_name)
            values = {"gui_input_dir": str(root / "input"), "gui_output_dir": str(root / "output")}
            for arguments in ((root, values, field), (root, values), (root,), ()):
                try:
                    options = provider(*arguments)
                    break
                except TypeError:
                    if not arguments:
                        raise
            if not isinstance(options, list):
                raise RuntimeError(f"Dynamic option source returned {type(options).__name__}, expected list.")
            if options:
                return options, ""
        except Exception as error:  # noqa: BLE001 - the reason goes to the caller, the form still draws
            return [], f"Option source failed: {error.__class__.__name__}: {error}"
    options = field.get("options", [])
    return (options if isinstance(options, list) else []), ""


FIELD_KEYS = ("label", "label_ru", "hint", "hint_ru", "tooltip", "tooltip_ru", "placeholder", "min", "max", "step",
              "min_selected",
              # `obvious` - the short line beside the control says nothing the control does not
              # (ZIP or 7Z): a window keeps it for the tooltip and does not write it in the form.
              "obvious",
              # `beside` - the id of a field of the same block on whose line this one stands, as
              # its control alone: ZIP or 7Z on the line of "Pack into an archive".
              "beside")


def describe_option(option: Any) -> dict[str, Any]:
    """An option of a choice. A provider may give it a face: the name of its mark in the icon sets
    and the colour of its logo - a browser is known by both sooner than by its name."""
    if not isinstance(option, dict):
        return {"value": option, "label": str(option), "label_ru": "", "icon": "", "tone": ""}
    return {"value": option.get("value", option.get("id", option.get("label", ""))),
            "label": str(option.get("label") or option.get("title") or option.get("value", "")),
            "label_ru": str(option.get("label_ru") or ""),
            "icon": str(option.get("icon") or ""), "tone": str(option.get("tone") or "")}


def describe_field(field: dict[str, Any], root: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"id": field_id(field), "kind": field_kind(field), "default": field_default(field),
                           "group": field_group(field), "advanced": is_advanced(field)}
    for key in FIELD_KEYS:
        if key in field:
            row[key] = field[key]
    if "options" in field or option_source(field):
        options, error = listed_options(field, root)
        row["options"] = [describe_option(option) for option in options]
        if error:
            row["options_error"] = error
    return row


def describe_node(node: CommandNode | Operation, root: Path, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """A section or an operation. `raw` is the same node as the manifest wrote it: the keys of
    looks - the icon, the logical block - are read from there, the engine has no use for them."""
    raw = raw if isinstance(raw, dict) else {}
    children = tuple(getattr(node, "children", ()))
    raw_children = {str(item.get("id", "")).strip(): item for item in raw.get("children", []) if isinstance(item, dict)}
    return {
        "id": node.id, "title": node.title, "title_ru": node.title_ru,
        "description": node.description, "description_ru": node.description_ru,
        "tooltip": node.tooltip, "tooltip_ru": node.tooltip_ru,
        "kind": node.kind,
        # The sign on the button of the node: a name of the icon set of the window.
        "icon": str(raw.get("icon") or ""),
        # The logical block of its section an operation stands in: operations of one block are
        # shown together under its name.
        "block": str(raw.get("block") or ""), "block_ru": str(raw.get("block_ru") or ""),
        # The command whose panel this one shares: its button stands beside that command's own.
        "beside": str(getattr(node, "beside", "") or ""),
        "workbench": node.id in WORKBENCH_OPERATIONS,
        "runs": bool(node.service), "direct": is_direct(node),
        "fields": [describe_field(field, root) for field in form_fields(node)],
        "children": [describe_node(child, root, raw_children.get(child.id)) for child in children],
    }


def describe(manifest: ToolManifest, root: Path) -> dict[str, Any]:
    """The manifest taken apart: what a window draws its sections, operations and fields by."""
    def raw_by_id(key: str) -> dict[str, Any]:
        return {str(item.get("id", "")).strip(): item for item in manifest.raw.get(key, []) if isinstance(item, dict)}

    groups, maintenance = raw_by_id("operation_groups"), raw_by_id("maintenance_operations")
    return {
        "tool": dict(manifest.raw.get("tool", {})),
        "nodes": [describe_node(node, root, groups.get(node.id)) for node in manifest.operation_groups],
        "maintenance": [describe_node(node, root, maintenance.get(node.id)) for node in manifest.maintenance_operations],
    }
