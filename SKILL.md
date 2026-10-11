---
name: audion-browsers-portable
description: Build, update and check portable Chromium browsers with Audion Browsers Portable, from its window or from the console door browsers_cli.cmd - Google Chrome, Yandex Browser, Brave, Chromium-Gost, Ungoogled Chromium; download the official files of Cent Browser, Vivaldi, LibreWolf and DuckDuckGo; change the engine (Python) or the window (C#, Avalonia 12, .NET 10) and prove the change; rebuild App\, walk the built program without the mouse, prepare a release. Use when asked to build or update a portable browser ("собери Chrome", "обнови сборки на флешке"), to run an operation of the program from a script, to add a browser or an operation, to fix a finding of an audit, to check a change in its window, or to build, clean or prepare Audion Browsers Portable for a release ("собирай", "пересобери", "готовь к релизу").
---

# Audion Browsers Portable

One engine for the whole Chromium stack: it downloads a browser, unpacks its installer
instead of running it, puts a portability library beside the executable, and later keeps the
build current. Nothing is installed into Windows; the profile lies in the build.

Two programs make it up:

- **the engine** - Python in `system_core\`: the services, and the console door
  `browsers_cli.cmd` every operation is run through;
- **the window** - C# on Avalonia in `Source\`, built into `App\`: the face of that door.
  It draws the manifest the door describes and runs nothing itself. `Start.exe` opens it.

Before a change read `AGENTS.md`: `The browser stack` for the engine, `The window on Avalonia`
for the window. This file is the short form of how the work is done and checked.

## Running an operation

Everything a button of the window does, a console does with the same words:

```bat
browsers_cli.cmd describe
browsers_cli.cmd run --operation browsers_build --set browsers=chrome,brave --target D:\Portable
browsers_cli.cmd run --operation browsers_check --source E:\Flash\Browsers
browsers_cli.cmd run --operation browsers_update --source E:\Flash\Browsers --set force_update=true
browsers_cli.cmd version
```

- `describe` prints the manifest as JSON: the sections, the operations, the fields of each
  with their defaults and options. Read it instead of guessing a name.
- `--set NAME=VALUE` gives one field; `--params FILE` gives a JSON object of them;
  `--result FILE` receives the outcome; `--cancel-file FILE` is the stop signal - the file
  appears, the operation stops at its next safe point.
- A plain line of the output is a line of the operation's log. A line that starts with
  `::browsers::` carries JSON: `started`, `progress`, `pulse`, `finished`.
- Outcomes and exit codes: `done` 0; `partial` 1 - the operation ended, some of the browsers
  it was given failed; `failed` 1; `stopped` 130; a wrong call 2, and nothing is run.
- A relative `--source` or `--target` is counted from the program folder. Source is the
  folder that holds the builds, or one build itself; Target is where new builds, archives and
  downloads go. An update happens where the build lies - the Target is not used for it.

Operations that change Windows - `certificates_install`, `certificates_remove`,
`defender_exclusion_remove`, and any build or update with `guard_defender` on (it asks for
UAC) - are a person's to press. A script sets `--set guard_defender=false`.

## Changing the engine

- Which browsers are built and why: `system_core\services\browser_registry.py`. A browser is
  an entry there, not new code.
- An operation is an entry of `config\tool_manifest.yaml` naming a service
  (`module:function`) and its fields. The window shows it by itself.
- A service looks for a stop where stopping still costs nothing (`_check_cancelled`) and
  raises `OperationCancelled`; a long step that copies, packs or unpacks looks for it too.
- What is the person's is not deleted: a profile is moved by renaming only; what is in the
  way goes into `Old files` inside the build; a file nobody can name stays.
- A download is counted against what the server said it would send, and a certificate is
  proved by its thumbprint before anything is done with its file.
- Only `%NAME%` is read in a path (`system_core\core\paths.py`, `expand_percent`).

Check: `runtime\python.exe -m pytest` - all of it - then `install\verify_portable_env.cmd`.
Every fix comes with a test that fails without it.

## Changing the window

- The shell - themes, sizes, styles - is `Source\App.axaml`, kept whole. What this window
  adds lies in `Source\Window.Styles.axaml`. No colour is written in markup or code: only a
  resource of the shell.
- The window knows no operation by its name: it draws `describe`. A new kind of field is a
  change of `Source\Views\Form.cs` and of the door's model together.
- Texts are Russian in the code; the English pair of each lies in `Source\config\i18n.json`,
  and the tests do not pass without it.
- A line of an operation is shown in the colours its writer gave it (`Source\Core\Ansi.cs`);
  the window colours its own lines alone (`Journal.Tone`).
- An icon is never drawn by hand: its Lucide name goes into `Source\Tools\build_icons.py`.

Check, in this order:

1. `runtime\python.exe -m pytest tests\test_window_avalonia.py tests\test_cli_door.py`
2. `Source\Tools\dev-run.ps1 -Dev "idle;tab=update;shot=<file.png>;close"` - a Debug copy
   walked by steps, without the mouse and off the screen; `shot` draws the window into a
   file. A step that runs an operation or clears a folder needs `-Stand <folder>`.
3. `install\Build-App.cmd` - the checks of the window, the PowerShell of the program, the
   window, the self-check of the built copy, then the swap of `App\`.
4. `Source\Tools\release-walk.ps1 -Stand <new folder>` - the built program copied to a stand
   and its own buttons pressed by UI Automation: a section, an operation, a choice of a form,
   the clearing of the working folders, STOP during a download, closing during a download.
5. Remove `Source\bin`, `Source\obj`, `Source\Ps\bin`, `Source\Ps\obj` and the stands.

A button is checked by the button, on the built program: a step that calls the method behind
a button proves nothing about the button. The real mouse is never moved.

## Before a release

1. The number: `config\version.json` and `version` in `config\tool_manifest.yaml`, one bump
   per logical change, with its entry in `CHANGELOG.md`.
2. `runtime\python.exe -m pytest`, `install\Build-App.cmd`, `release-walk.ps1`.
3. `Source\Tools\live-walk.ps1 -Stand <new folder on a disk with room>` - the built program
   at real work: Downloads, Build, Check, Update, Update with "rebuild without a new
   version", the library alone. It needs the network, about a gigabyte and several minutes.
   It compares the values the window handed to the door with what it set, and counts what
   each operation says it did.
4. The builder in the order a release build runs it - `02` Python, `06` 7-Zip, `71` verify,
   `70` the cache of the install - on a **copy** of the project, after `cleanup_project.cmd`
   there. In the working folder the cleanup is run only with `/DRYRUN`.
5. The guides in `docs\` and `README.md` say what the program does now.

What no check here shows: the Defender guard for real - UAC accepted, the exclusion in
Defender, the build, the exclusion gone. A person presses it once on a machine where Defender
runs.

## Rules that are not negotiable

- `.cmd` files are UTF-8 without BOM and CRLF; a `.ps1` the PowerShell of Windows runs
  (`system_core\services\defender_guard.ps1`) is plain ASCII.
- A check never runs an operation that changes Windows, never starts the owner's browsers
  with their profiles, and writes only into a stand of its own.
- What is removed from the project goes to the Recycle Bin.
- `App\` is not a product of a release build: it ships as it is and is rebuilt only when
  `Source\` changes.
