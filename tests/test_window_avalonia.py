"""The window on Avalonia (Source\\) and the console door: what the one reads, the other gives.

The window is the face of the door: it takes the manifest by `describe`, runs an operation by
`run` and reads its course from the service lines. That is a contract of two programs in two
languages, and it breaks without a sound: a key renamed in the door is simply empty in the
window. Here it is compared by the sources, and a built window checks itself (`--self-check`):
every operation of the manifest has a button, every field a control.

The checks open no window and run no operation.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from system_core import cli
from system_core.core import operation_model as model
from system_core.core.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Source"
MANIFEST = load_manifest(ROOT / "config" / "tool_manifest.yaml")
DESCRIBED = model.describe(MANIFEST, ROOT)
CYRILLIC = re.compile("[А-Яа-яЁё]")
ASSEMBLY = "AudionBrowsersPortable"

pytestmark = pytest.mark.skipif(not SOURCE.is_dir(), reason="the sources of the window are not here")


def sources(pattern: str) -> list[Path]:
    return [path for path in sorted(SOURCE.rglob(pattern))
            if not {"bin", "obj"} & set(path.relative_to(SOURCE).parts)]


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def properties(class_name: str) -> set[str]:
    """The properties of a class of the window's model that are read from the answer of the door, as JSON keys."""
    text = (SOURCE / "Core" / "Manifest.cs").read_text(encoding="utf-8")
    body = re.search(r"public sealed class %s\b.*?\n\{(.*?)\n\}" % class_name, text, re.S)
    assert body, class_name
    found = set()
    for line in body.group(1).splitlines():
        if "[JsonIgnore]" in line:
            continue
        match = re.match(r"\s*public [\w<>?\[\], ]+ (\w+) \{ get; set; \}", line)
        if match:
            found.add(snake(match.group(1)))
    return found


def all_nodes(nodes=None):
    for node in (DESCRIBED["nodes"] + DESCRIBED["maintenance"] if nodes is None else nodes):
        yield node
        yield from all_nodes(node["children"])


def window_texts() -> dict[str, str]:
    """The Russian texts of the window: the literals of its code and markup a person sees."""
    found: dict[str, str] = {}
    for path in sources("*.cs"):
        if path.name == "MainWindow.Dev.cs":        # the steps of a check without a mouse: not in a release
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("//"):
                continue
            for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', line.split(" // ")[0]):
                if not CYRILLIC.search(literal):
                    continue
                # The patterns the journal colours its lines by, and the mark of the window's lines, are not texts.
                if path.name == "Journal.cs" and ("|" in literal or literal == "[окно]"):
                    continue
                text = literal.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
                found.setdefault(text, f"{path.relative_to(SOURCE)}:{number}")
    for path in sources("*.axaml"):
        text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
        for _attribute, value in re.findall(r'(Text|ToolTip\.Tip|PlaceholderText|Header|Content|Title)="([^"]*)"', text):
            if CYRILLIC.search(value):
                found.setdefault(value.replace("&quot;", '"'), path.name)
    return found


def test_every_text_of_the_window_has_an_english_pair() -> None:
    dictionary = json.loads((SOURCE / "config" / "i18n.json").read_text(encoding="utf-8-sig"))
    texts = window_texts()

    missing = {text: where for text, where in texts.items() if text not in dictionary}
    assert missing == {}, "Russian texts of the window without an English pair in Source\\config\\i18n.json"
    assert [text for text, pair in dictionary.items() if not str(pair.get("en") or "").strip()] == []
    assert sorted(text for text in dictionary if text not in texts) == [], "entries the window no longer has"
    for text, pair in dictionary.items():
        assert sorted(re.findall(r"\{\d+\}", text)) == sorted(re.findall(r"\{\d+\}", pair["en"])), text
        assert not CYRILLIC.search(pair["en"]), text


def test_the_window_calls_python_as_the_wrapper_does() -> None:
    """The window starts the Python of the project itself, without cmd: what the wrapper does is repeated word for word."""
    wrapper = (ROOT / "browsers_cli.cmd").read_text(encoding="ascii")
    door = (SOURCE / "Core" / "Door.cs").read_text(encoding="utf-8")
    paths = (SOURCE / "Core" / "Paths.cs").read_text(encoding="utf-8")

    assert '"%PYTHON_CMD%" -u "%BASE_DIR%\\system_core\\cli.py" %*' in wrapper
    assert '{ "-u", Paths.DoorScript }' in door
    assert 'Path.Combine(Root, "system_core", "cli.py")' in paths
    for name, value in (("PYTHONUTF8", "1"), ("PYTHONIOENCODING", "utf-8")):
        assert f'set "{name}={value}"' in wrapper
        assert f'["{name}"] = "{value}"' in door
    # Python is looked for in the same places, in the same order.
    for place in (r"runtime\python.exe", r"runtime\python\python.exe"):
        assert place in wrapper and place in paths
    assert paths.index(r"runtime\python.exe") < paths.index(r"runtime\python\python.exe")
    assert 'DoorName = "browsers_cli.cmd"' in paths


def test_the_door_gives_every_key_the_window_reads() -> None:
    nodes = list(all_nodes())
    node_keys = set(nodes[0])
    assert all(set(node) == node_keys for node in nodes), "the nodes of describe carry different sets of keys"
    assert properties("Node") == node_keys

    field_keys = {"id", "kind", "default", "group", "advanced", "options", "options_error"} | set(model.FIELD_KEYS)
    assert properties("Field") == field_keys
    assert {"icon", "block", "block_ru"} <= node_keys
    given = set().union(*(set(field) for node in nodes for field in node["fields"]))
    assert given <= field_keys, "the door gives keys of a field the window does not read"

    options = [option for node in nodes for field in node["fields"] for option in field.get("options", [])]
    assert properties("Option") == set().union(*(set(option) for option in options))
    assert properties("ProjectPaths") == {"root", "input", "output", "logs", "report", "config", "workspace"}
    described = (ROOT / "system_core" / "cli.py").read_text(encoding="utf-8")
    for key in properties("ProjectPaths"):
        assert f'"{key}": str(paths.' in described, key


def test_the_window_knows_every_kind_of_field() -> None:
    text = (SOURCE / "Core" / "Manifest.cs").read_text(encoding="utf-8")
    known = set(re.findall(r'"(\w+)"', re.search(r"Known = \{([^}]*)\}", text).group(1)))

    assert known == set(model.FIELD_KINDS)
    assert {field["kind"] for node in all_nodes() for field in node["fields"]} <= known


def test_every_block_of_a_form_has_a_name_in_the_window() -> None:
    """A block the window has no name for is shown by its id - readable, but in one language only."""
    text = (SOURCE / "Core" / "Manifest.cs").read_text(encoding="utf-8")
    named = set(re.findall(r'"(\w+)"(?: or "")? => Lang\.T\(', text))

    assert {field["group"] for node in all_nodes() for field in node["fields"]} <= named


def test_a_field_beside_another_names_a_field_of_its_own_block() -> None:
    """`beside` puts a field on the line of another. A name that is not a field of the same block
    of the same operation - or is itself a guest, or lies on the other side of "Advanced" - is
    not an error for the window: the field just gets a line of its own, and nobody learns why."""
    wrong = []
    guests = 0
    for node in all_nodes():
        fields = {field["id"]: field for field in node["fields"]}
        for field in node["fields"]:
            if not field.get("beside"):
                continue
            guests += 1
            host = fields.get(field["beside"])
            if (host is None or host.get("beside") or host["group"] != field["group"]
                    or host["advanced"] != field["advanced"]):
                wrong.append(f'{node["id"]}.{field["id"]} beside {field["beside"]}')

    assert wrong == []
    assert guests > 0, "no field of the manifest stands beside another: the rule is not exercised"


def window_signs() -> set[str]:
    """The signs the window carries: the names of Source\\Icons.cs, Lucide and brand marks alike."""
    return set(re.findall(r'^\s+\["([a-z0-9-]+)"\] = ', (SOURCE / "Icons.cs").read_text(encoding="utf-8"), re.M))


def test_every_button_of_the_manifest_has_its_sign() -> None:
    """A section and an operation name their sign in the manifest; the window must carry it.

    A name the set does not have leaves a button without its picture and says nothing - so it is
    a failed test here, and a failed self-check of the built window.
    """
    signs = window_signs()
    nodes = [node for node in all_nodes() if not node["workbench"]]

    assert [node["id"] for node in nodes if not node["icon"]] == [], "sections and operations without a sign"
    assert {node["id"]: node["icon"] for node in nodes if node["icon"] not in signs} == {}
    # The set is made by Source\Tools\build_icons.py from its lists, never by hand.
    listed = set(re.findall(r"'([a-z0-9-]+)'", re.search(r"LUCIDE = \[(.*?)\n\]\n", (SOURCE / "Tools" / "build_icons.py").read_text(encoding="utf-8"), re.S).group(1)))
    assert {node["icon"] for node in nodes} <= listed | {"googlechrome"}


def test_every_browser_wears_its_mark_and_its_colour() -> None:
    """The browsers come from the registries of the engine with the name of their mark and the colour of their logo."""
    signs = window_signs()
    options = {option["value"]: option for node in all_nodes() for field in node["fields"]
               if field["kind"] == "checkboxes" for option in field.get("options", [])}

    assert set(options) >= {"chrome", "yandex", "brave", "chromium_gost", "ungoogled_chromium", "cent", "vivaldi", "librewolf", "duckduckgo"}
    assert {name: option["icon"] for name, option in options.items() if option["icon"] not in signs} == {}
    for name, option in options.items():
        assert option["tone"] == "" or re.fullmatch(r"#[0-9A-Fa-f]{6}", option["tone"]), name
    # Two builds of Chromium share a mark and are told apart by their tone.
    assert options["chromium_gost"]["icon"] == options["ungoogled_chromium"]["icon"]
    assert len({options[name]["tone"] for name in ("chrome", "chromium_gost", "ungoogled_chromium")}) == 3


def test_operations_of_a_block_share_its_name_in_both_languages() -> None:
    for section in DESCRIBED["nodes"]:
        names: dict[str, str] = {}
        for node in section["children"]:
            assert bool(node["block"]) == bool(node["block_ru"]), node["id"]
            if node["block"]:
                assert names.setdefault(node["block"], node["block_ru"]) == node["block_ru"], node["id"]
        # An operation without fields stands in a card named by its block: without one the card has no name of its own.
        assert [node["id"] for node in section["children"] if not node["fields"] and not node["beside"] and not node["block"]] == []


def test_service_lines_and_exit_codes() -> None:
    door = (SOURCE / "Core" / "Door.cs").read_text(encoding="utf-8")
    source = (ROOT / "system_core" / "cli.py").read_text(encoding="utf-8")
    run = (SOURCE / "MainWindow.Run.cs").read_text(encoding="utf-8")

    assert f'Control = "{cli.CONTROL}"' in door
    assert (f"ExitDone = {cli.EXIT_OK}, ExitFailed = {cli.EXIT_FAILED}, ExitUsage = {cli.EXIT_USAGE}, "
            f"ExitStopped = {cli.EXIT_STOPPED}") in door
    said = set(re.findall(r'control\(\{"(\w+)"', source))
    assert said == {"started", "progress", "pulse", "finished"}
    for kind in said:
        assert f'TryGetProperty("{kind}"' in door, kind
    # The outcome of a run: the words the door names it by, and those the window knows.
    named = set(re.findall(r'"(\w+)"', re.search(r"outcome = (.*)", source).group(1)))
    assert named == {"stopped", "failed", "partial", "done"}
    for outcome in named:
        assert f'"{outcome}" =>' in run, f"the window has no verdict for the outcome {outcome}"
    # What the last service line carries besides the outcome, and the window reads it.
    for key in ("outcome", "message", "failed"):
        assert f'"{key}": ' in re.search(r'control\(\{"finished": \{(.*?)\}\}\)', source).group(1)
        assert f'(finished, "{key}")' in door or f'finished.TryGetProperty("{key}"' in door, key
    # The keys of `run` the window calls it with.
    for key in ("--operation", "--source", "--target", "--params", "--result", "--cancel-file", "--root"):
        assert f'"{key}"' in source and f'"{key}"' in door, key


def test_the_verdict_of_a_run_is_coloured_in_both_languages() -> None:
    """The journal colours the line of a verdict by its words, Russian and English alike.

    The words stand in one file (MainWindow.Run.cs), their English in another (i18n.json) and
    the patterns in a third (Journal.cs): a word changed in one of them left the verdict grey,
    or a run done in part as green as a run done.
    """
    journal = (SOURCE / "Core" / "Journal.cs").read_text(encoding="utf-8")
    run = (SOURCE / "MainWindow.Run.cs").read_text(encoding="utf-8")
    dictionary = json.loads((SOURCE / "config" / "i18n.json").read_text(encoding="utf-8-sig"))
    patterns = {name: re.compile(body) for name, body in re.findall(r'static readonly Regex (Head\w+) = new\("([^"]+)"', journal)}
    assert set(patterns) == {"HeadGood", "HeadWarn", "HeadBad"}
    # The order the journal asks them in: the first that matches names the tone.
    asked = re.findall(r"if \((Head\w+)\.IsMatch", journal)
    assert sorted(asked) == sorted(patterns)
    switch = re.search(r"string verdict = outcome switch\s*\{(.*?)\};", run, re.S)
    verdicts = dict(re.findall(r'"(\w+)" => (?:Lang\.F\()?"([^"]+)"', switch.group(1)))
    tone = {"done": "HeadGood", "partial": "HeadWarn", "stopped": "HeadWarn",
            "refused": "HeadBad", "nostart": "HeadBad", "failed": "HeadBad"}
    assert set(verdicts) == set(tone)

    for outcome, russian in verdicts.items():
        for words in (russian, dictionary[russian]["en"]):
            line = f"===== Section → Operation: {words.format(3)} · 00:05"
            first = [name for name in asked if patterns[name].search(line)][:1]
            assert first == [tone[outcome]], (outcome, line)


def test_a_line_of_an_operation_is_never_read_for_words() -> None:
    """Colours in the journal are the operation's own; the window colours its own lines alone.

    The window before this one showed the colour codes of the engine and added none. Marks of
    the engine in the patterns of the journal - "[OK]", "[WARN]", "[ERR]" - are the window
    painting somebody else's lines by their first word again.
    """
    journal = (SOURCE / "Core" / "Journal.cs").read_text(encoding="utf-8")
    code = "\n".join(line for line in journal.splitlines() if not line.strip().startswith("//"))
    patterns = " ".join(re.findall(r'new\(@?"([^"]+)"', code))

    for mark in ("OK", "DONE", "WARN", "ERR", "FAIL", "SKIP", "INFO", "Traceback"):
        assert mark not in patterns, mark
    # What an operation says goes to the panel as it came, marked as told; the tone is taken for the window's own lines.
    show = re.search(r"public static void Show\(.*?\n    \}", journal, re.S).group(0)
    assert "LogTone.Plain, true" in show and "Tone(" not in show
    window = (SOURCE / "MainWindow.axaml.cs").read_text(encoding="utf-8")
    assert "if (lines[i].Told) read[i] = _ansi.Read(lines[i].Text, out plain);" in window


def test_a_stand_is_a_thing_of_debug_builds_alone() -> None:
    """A release build works in the folder it stands in: no variable can point it at another project."""
    door = (SOURCE / "Core" / "Door.cs").read_text(encoding="utf-8")
    block = re.search(r"public static string Project \{ get; \} =\n#if DEBUG\n(.*?)\n#else\n(.*?)\n#endif", door, re.S)

    assert block and "AUDION_DEV_PROJECT" in block.group(1) and block.group(2).strip() == '"";'
    assert door.count("AUDION_DEV_PROJECT") == 1
    assert (SOURCE / "MainWindow.Dev.cs").read_text(encoding="utf-8").startswith("#if DEBUG")


def test_the_built_window_holds_the_whole_manifest(tmp_path: Path) -> None:
    """A built window builds every section without being shown and counts what it built against the manifest."""
    # What the executable is built from: its code, its markup and its project. (The dictionary
    # travels beside it as a file and is checked by its own test.)
    newest = max(path.stat().st_mtime for pattern in ("*.cs", "*.axaml", "*.csproj") for path in sources(pattern))
    built = [path for path in (ROOT / "App" / f"{ASSEMBLY}.exe",
                               SOURCE / "bin" / "Release" / "net10.0" / "win-x64" / f"{ASSEMBLY}.exe",
                               SOURCE / "bin" / "Release" / "net10.0" / f"{ASSEMBLY}.exe",
                               SOURCE / "bin" / "Debug" / "net10.0" / f"{ASSEMBLY}.exe")
             if path.is_file() and path.stat().st_mtime >= newest]
    if not built:
        pytest.skip("no build of the window newer than its sources: install\\Build-App.cmd")
    report = tmp_path / "self_check.json"

    done = subprocess.run([str(built[0]), "--root", str(ROOT), f"--self-check={report}"], timeout=180)

    assert report.is_file(), "the window did not write the result of its self-check"
    result = json.loads(report.read_text(encoding="utf-8"))
    assert done.returncode == 0 and result["ok"], result
    operations = [node for node in all_nodes() if node["runs"]]
    shown = [node for node in operations if not node["workbench"]]
    assert result["operations"] == len(operations)
    assert result["operations_with_button"] == len(shown)
    assert result["operations_of_the_workbench"] == [node["id"] for node in operations if node["workbench"]]
    fields = sum(len(node["fields"]) for node in shown)
    assert (result["fields"], result["fields_built"]) == (fields, fields)
    assert result["sections"] == len(DESCRIBED["nodes"])
