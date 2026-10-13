"""What a build or an update must never cost the person who runs it.

Five ways it used to: a second Build took the profile of the first, a flat
build lost the files lying beside the browser, two builds of one browser were
packed into one archive, a copy that stopped halfway left no browser in place,
and Cancel was answered with "finished". Every vendor request is replaced by
local fixtures; the searching, the rearranging, the swapping and the packing are
the real ones.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import ctypes
import os
import shutil
import struct
import zipfile

import pytest

from system_core.core.jobs import JobContext, execute_operation
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import browser_registry
from system_core.services import browsers_portable_service as service
from system_core.services import build_adoption as adoption
from system_core.services import portable_libraries as libraries


def _put(path: Path, data: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _pe(path: Path, tail: bytes = b"") -> Path:
    """The smallest file the architecture check reads, with a tail to tell copies apart."""
    blob = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", blob, 60, 64)
    return _put(path, bytes(blob) + b"PE\0\0" + struct.pack("<H", 0x8664) + tail)


def _profile(root: Path, bookmarks: str) -> Path:
    _put(root / "Local State", "{}")
    _put(root / "Default" / "Preferences", "{}")
    return _put(root / "Default" / "Bookmarks", bookmarks)


def _context(root: Path, service_name: str = "build_selected", **parameters: object) -> JobContext:
    paths = get_project_paths(root)
    ensure_project_dirs(paths)
    operation = Operation(
        id="test",
        title="Test",
        description="",
        service=f"system_core.services.browsers_portable_service:{service_name}",
        parameters={"browsers": ["brave"], "portable_engine": "proxy_library", "guard_defender": False,
                    "force_update": True, **parameters},
    )
    return JobContext(paths, operation, paths.logs / "test.log", paths.report)


def _log(context: JobContext) -> str:
    return context.log_file.read_text(encoding="utf-8") if context.log_file.exists() else ""


NEW_BROWSER = b"new browser"


def _offline(context: JobContext, monkeypatch: pytest.MonkeyPatch) -> browser_registry.BrowserSpec:
    """Brave as the vendor would serve it, from local files: the browser and the wrapper."""
    spec = browser_registry.browser("brave")
    payload = context.paths.workspace / "payload"
    _pe(payload / spec.executable, NEW_BROWSER)
    _put(payload / "resources.pak", "new resources")
    _put(payload / "200.0.0.0" / "brave.dll", "new library")
    wrapper = context.paths.workspace / "proxy-library.zip"
    with zipfile.ZipFile(wrapper, "w") as zipped:
        zipped.write(_pe(context.paths.workspace / "wrapper.dll", b"wrapper"), libraries.PROXY_DLLS["x64"])
    installer = _put(context.paths.workspace / "installer.zip", "fixture")
    asset = service.DownloadedAsset("fixture", "https://example.test/brave.zip", installer, "", 7)
    monkeypatch.setattr(service, "_require_7zip", lambda _context: Path("unused.exe"))
    monkeypatch.setattr(service, "_browser_source", lambda _context, _spec: ("200.0.0.0", asset.url, "brave.zip"))
    monkeypatch.setattr(service, "_download", lambda *_arguments, **_options: asset)
    monkeypatch.setattr(service, "_unpack_browser", lambda *_arguments: payload)
    monkeypatch.setattr(service, "_download_wrapper", lambda *_arguments: (wrapper, "2.0.0.0"))
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_arguments: "2.0.0.0")
    return spec


def _our_build(home: Path, spec: browser_registry.BrowserSpec, bookmarks: str = "my bookmarks") -> Path:
    _pe(home / "App" / spec.executable, b"old browser")
    _put(home / "App" / "150.0.0.0" / "brave.dll", "old library")
    _profile(home / "Data", bookmarks)
    _put(home / "Cache" / "data_0", "my cache")
    return home


def _is_new(home: Path, spec: browser_registry.BrowserSpec) -> bool:
    return (home / "App" / spec.executable).read_bytes().endswith(NEW_BROWSER)


def _leftovers(home: Path) -> list[str]:
    """Folders this program makes inside a build while it works, if any were left."""
    return sorted(item.name for item in home.iterdir() if item.name.lower().startswith(adoption.WORK_PREFIXES))


# --- Build over a build that is already there -------------------------------------


def test_building_again_keeps_the_profile_of_the_build_already_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    spec = _offline(context, monkeypatch)
    home = _our_build(service._portable_root(context) / spec.folder, spec)
    _put(home / "my-notes.txt", "mine")

    result = service.build_selected(context)

    assert len(result["built"]) == 1 and result["failed"] == []
    assert _is_new(home, spec)
    assert (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "my bookmarks"
    assert (home / "Cache" / "data_0").read_text(encoding="utf-8") == "my cache"
    assert (home / "my-notes.txt").read_text(encoding="utf-8") == "mine"
    assert not (home / "App" / "150.0.0.0").exists() and _leftovers(home) == []
    assert "[KEEP] Brave" in _log(context)


def test_a_new_build_is_published_where_nothing_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = _offline(context, monkeypatch)

    result = service.build_selected(context)

    home = Path(result["built"][0]["artifact"])
    assert home == service._portable_root(context) / spec.folder and _is_new(home, spec)
    assert (home / "Data").is_dir() and (home / "Cache").is_dir()
    assert "[KEEP]" not in _log(context)


def test_publishing_never_clears_a_place_that_is_taken(tmp_path: Path) -> None:
    context = _context(tmp_path)
    taken = service._portable_root(context) / "Brave Portable"
    _profile(taken / "Data", "my bookmarks")
    fresh = _put(tmp_path / "fresh" / "App" / "brave.exe", "new").parent.parent

    with pytest.raises(RuntimeError, match="already exists"):
        service._publish(context, fresh, "Brave Portable")

    assert (taken / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "my bookmarks"


def test_building_into_an_archive_leaves_the_folder_beside_it_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, package_archive=True, archive_format="zip")
    spec = _offline(context, monkeypatch)
    home = _our_build(service._portable_root(context) / spec.folder, spec)

    result = service.build_selected(context)

    assert Path(result["built"][0]["artifact"]).name == f"{spec.folder}.zip"
    assert not _is_new(home, spec)
    assert (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "my bookmarks"


@pytest.mark.skipif(os.name != "nt", reason="the sharing rule that marks a running image is a Windows one")
def test_building_over_a_running_browser_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = _offline(context, monkeypatch)
    home = _our_build(service._portable_root(context) / spec.folder, spec)
    # Held the way a running image is: nobody else gets write access.
    handle = ctypes.windll.kernel32.CreateFileW(str(home / "App" / spec.executable), 0x80000000, 0x00000001, None, 3, 0, None)
    assert handle not in (0, -1)
    try:
        with pytest.raises(RuntimeError, match="in use"):
            service.build_selected(context)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)

    assert not _is_new(home, spec) and _leftovers(home) == []
    assert (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "my bookmarks"


# --- a build from elsewhere: only the browser is replaced ---------------------------


def test_a_flat_build_keeps_the_files_lying_beside_the_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected")
    spec = _offline(context, monkeypatch)
    home = context.paths.input / "My carried browser"
    _pe(home / spec.executable, b"old browser")
    _put(home / "resources.pak", "old resources")
    _put(home / "150.0.0.0" / "brave.dll", "old library")
    _put(home / "version.dll", "another wrapper")
    _profile(home / "User Data", "personal bookmarks")
    _put(home / "Downloads" / "invoice.txt", "personal document")
    _put(home / "my-notes.txt", "personal notes")
    _put(home / "portable-tool.exe", "a program of my own")

    result = service.update_selected(context)

    build = Path(result["updated"][0]["artifact"])
    assert build == context.paths.input / spec.folder and not home.exists()
    assert (build / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "personal bookmarks"
    # Where they were, not hidden away: nothing about them was in the way.
    assert (build / "Downloads" / "invoice.txt").read_text(encoding="utf-8") == "personal document"
    assert (build / "my-notes.txt").read_text(encoding="utf-8") == "personal notes"
    # An executable at the root could start the old browser, so it is put away - and kept.
    assert (build / adoption.ASIDE_NAME / "portable-tool.exe").read_text(encoding="utf-8") == "a program of my own"
    # The old browser is gone, the new one is in App, and nothing of it is left at the root.
    assert _is_new(build, spec)
    assert (build / "App" / "resources.pak").read_text(encoding="utf-8") == "new resources"
    assert not (build / "App" / "150.0.0.0").exists()
    assert not list(build.rglob("150.0.0.0")) and not (build / spec.executable).exists()
    assert _leftovers(build) == []


def test_a_file_kept_beside_the_executable_in_its_branch_is_moved_aside(tmp_path: Path) -> None:
    home = tmp_path / "input" / "Build"
    _put(home / "Chrome" / "chrome.exe", "MZ")
    _put(home / "Chrome" / "145.0.7632.110" / "chrome.dll", "old")
    _put(home / "Chrome" / "version.dll", "wrapper")
    _put(home / "Chrome" / "GChromeLauncher.bat", "start chrome")
    _put(home / "Chrome" / "my-passwords-export.csv", "mine")
    _profile(home / "Data", "keep me")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")
    log: list[str] = []

    adoption.normalize(layout, log.append, shutil.rmtree, ["chrome.exe", "chrome_proxy.exe", "154.0.8037.98"])

    assert sorted(item.name for item in (home / "App").iterdir()) == ["145.0.7632.110", "chrome.exe", "version.dll"]
    aside = home / adoption.ASIDE_NAME
    assert (aside / "my-passwords-export.csv").read_text(encoding="utf-8") == "mine"
    assert (aside / "GChromeLauncher.bat").is_file()
    assert any("instead of being deleted" in line and "my-passwords-export.csv" in line for line in log)


def test_the_folders_of_another_packer_are_put_away_not_deleted(tmp_path: Path) -> None:
    home = tmp_path / "input" / "GoogleChromePortable"
    _put(home / "App" / "Chrome-bin" / "chrome.exe", "MZ")
    _put(home / "App" / "AppInfo" / "appinfo.ini", "packer")
    _profile(home / "Data" / "profile", "keep me")
    _put(home / "Data" / "settings" / "GoogleChromePortableSettings.ini", "packer settings")
    _put(home / "GoogleChromePortable.exe", "the packer's launcher")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    adoption.normalize(layout, lambda _line: None, shutil.rmtree, ["chrome.exe"])

    aside = home / adoption.ASIDE_NAME
    assert (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "keep me"
    assert (aside / "Data" / "settings" / "GoogleChromePortableSettings.ini").read_text(encoding="utf-8") == "packer settings"
    assert (aside / "AppInfo" / "appinfo.ini").is_file()
    assert (aside / "GoogleChromePortable.exe").read_text(encoding="utf-8") == "the packer's launcher"
    assert (home / "App" / "Chrome-bin" / "chrome.exe").is_file()


def test_two_things_of_one_name_both_fit_into_old_files(tmp_path: Path) -> None:
    home = tmp_path / "input" / "Build"
    _put(home / "Chrome" / "chrome.exe", "MZ")
    _put(home / "Chrome" / "start.cmd", "inside the branch")
    _put(home / "start.cmd", "at the root")
    _profile(home / "Data", "keep me")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    adoption.normalize(layout, lambda _line: None, shutil.rmtree, ["chrome.exe"])

    kept = sorted(item.read_text(encoding="utf-8") for item in (home / adoption.ASIDE_NAME).iterdir())
    assert kept == ["at the root", "inside the branch"]


def test_what_was_put_away_or_left_by_a_failed_run_is_not_taken_for_a_build(tmp_path: Path) -> None:
    scope = tmp_path / "input"
    home = scope / "Build"
    _put(home / "App" / "chrome.exe", "MZ")
    _profile(home / "Data", "keep me")
    _put(home / adoption.ASIDE_NAME / "App" / "chrome.exe", "MZ")
    _profile(home / adoption.ASIDE_NAME / "Data", "an old profile")
    _put(home / "App.incoming-1234" / "chrome.exe", "MZ")

    [layout] = adoption.builds_in(scope, "chrome.exe")

    assert layout.home == home and layout.browser_dir == home / "App" and layout.profile_dir == home / "Data"


# --- one archive for each build ------------------------------------------------------


def _bookmarks_in(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zipped:
        return [zipped.read(name).decode("utf-8") for name in zipped.namelist() if name.endswith("/Data/Default/Bookmarks")]


def _with_library_settings(home: Path, mark: str) -> Path:
    """The build with the proxy library in it and a line the person added to its settings file."""
    _put(home / "App" / "portable-library.json", '{"engine": "proxy_library"}')
    edited = libraries.PROXY_INI.replace("REGOFF=0", "REGOFF=1").replace("RUNPARAM=", f"RUNPARAM=--user-note={mark}")
    return _put(home / "App" / "version.ini", edited.replace("\n", "\r\n").encode("ascii"))


def _settings_in(archive: Path) -> list[str]:
    """The line the person added, as the archive carries it."""
    with zipfile.ZipFile(archive) as zipped:
        texts = [zipped.read(name).decode("ascii") for name in zipped.namelist() if name.endswith("/App/version.ini")]
    return [line.split("--user-note=", 1)[1] for text in texts for line in text.splitlines() if "--user-note=" in line]


def test_two_builds_of_one_browser_are_packed_into_two_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected", package_archive=True, archive_format="zip")
    spec = _offline(context, monkeypatch)
    for name in ("Work browser", "Private browser"):
        _with_library_settings(_our_build(context.paths.input / name, spec, bookmarks=name), name)

    result = service.update_selected(context)

    archives = [Path(row["artifact"]) for row in result["updated"]]
    assert sorted(item.name for item in archives) == ["Private browser.zip", "Work browser.zip"]
    # Each archive is of its own build - told by the settings of the library, which travel with it.
    assert {item.stem: _settings_in(item) for item in archives} == {
        "Private browser": ["Private browser"],
        "Work browser": ["Work browser"],
    }


def test_builds_whose_folders_share_a_name_still_get_an_archive_each(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected", package_archive=True, archive_format="zip")
    spec = _offline(context, monkeypatch)
    for shelf in ("At home", "At work"):
        _with_library_settings(_our_build(context.paths.input / shelf / "Brave", spec, bookmarks=shelf), shelf)

    result = service.update_selected(context)

    archives = [Path(row["artifact"]) for row in result["updated"]]
    assert sorted(item.name for item in archives) == ["Brave (2).zip", "Brave.zip"]
    assert sorted(_settings_in(item)[0] for item in archives) == ["At home", "At work"]


def test_the_archive_of_an_updated_build_carries_no_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Ready to hand over" - and it was the whole folder: bookmarks, passwords and cookies with it."""
    context = _context(tmp_path, "update_selected", package_archive=True, archive_format="zip")
    spec = _offline(context, monkeypatch)
    home = _our_build(context.paths.input / spec.folder, spec, bookmarks="mine alone")
    _put(home / "Old files" / "another launcher.bat", "start")
    _put(home / "my-notes.txt", "mine")
    _put(home / "Certificates" / "russian_trusted_root_ca.crt", "certificate")

    result = service.update_selected(context)

    archive = Path(result["updated"][0]["artifact"])
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
    tops = sorted({name.split("/")[1] for name in names if name.count("/") >= 1 and name.split("/")[1]})
    assert tops == ["App", f"{spec.folder}.cmd", "Cache", "Certificates", "Data", service.BUILD_STAMP_FILE]
    # Data and Cache are there as the folders a browser fills itself: empty.
    assert [name for name in names if "/Data/" in name or "/Cache/" in name] == [f"{spec.folder}/Data/", f"{spec.folder}/Cache/"]
    assert _bookmarks_in(archive) == [] and f"{spec.folder}/App/{spec.executable}" in names
    # The build itself keeps everything: the archive is a copy, not a move.
    assert (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "mine alone"
    assert (home / "my-notes.txt").is_file() and (home / "Old files" / "another launcher.bat").is_file()
    assert "the profile and the cache are not in it" in _log(context)


def test_an_update_keeps_the_settings_of_the_library_as_the_person_left_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Update DLL only" kept the file and "Update" wrote a fresh one over it with the new App."""
    context = _context(tmp_path, "update_selected")
    spec = _offline(context, monkeypatch)
    home = _our_build(context.paths.input / spec.folder, spec)
    edited = _with_library_settings(home, "kept").read_bytes()
    # An edited file is no update of the wrapper: Check does not say so, and Update is not asked for by it.
    assert not libraries.needs_refresh(home, "proxy_library")

    service.update_selected(context)

    assert _is_new(home, spec)
    assert (home / "App" / "version.ini").read_bytes() == edited
    assert "version.ini: kept as it was in the build" in _log(context)


def test_another_library_gets_its_own_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected")
    spec = _offline(context, monkeypatch)
    home = _our_build(context.paths.input / spec.folder, spec)
    _put(home / "App" / "portable-library.json", '{"engine": "chrome_plus"}')
    _put(home / "App" / "chrome++.ini", "[general]\nlaunch_on_exit=\n")

    service.update_selected(context)

    assert not (home / "App" / "chrome++.ini").exists()
    assert (home / "App" / "version.ini").read_bytes() == libraries.PROXY_INI.replace("\n", "\r\n").encode("ascii")


def test_unique_names_ignore_letter_case() -> None:
    taken: set[str] = set()

    assert [service._unique_name(name, taken) for name in ("Brave", "brave", "BRAVE")] == ["Brave", "brave (2)", "BRAVE (3)"]


# --- the copy that stops halfway ------------------------------------------------------


def test_a_copy_that_stops_halfway_leaves_the_old_browser_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / spec.folder, spec)
    staged = _put(context.paths.workspace / "staged-App" / spec.executable, "new").parent

    def volume_fills_up(source: str, target: str, **_options: object) -> None:
        # What a move across volumes leaves when the target runs out of room: part of the tree.
        _put(Path(target) / "copied-first.bin", "half of it")
        raise shutil.Error([(source, target, "[Errno 28] No space left on device")])

    monkeypatch.setattr(service.shutil, "move", volume_fills_up)

    with pytest.raises(RuntimeError) as failure:
        service._replace_app_in_place(context, spec, home, staged)

    message = str(failure.value)
    assert "the build is as it was" in message and "No space left on device" in message and "\\\\?\\" not in message
    assert not _is_new(home, spec) and (home / "App" / "150.0.0.0" / "brave.dll").is_file()
    assert _leftovers(home) == []


def test_a_swap_that_fails_puts_the_old_browser_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / spec.folder, spec)
    staged = _pe(context.paths.workspace / "staged-App" / spec.executable, NEW_BROWSER).parent
    rename = Path.rename

    def refuse_the_new_one(self: Path, target: Path) -> Path:
        if self.name.startswith("App.incoming-"):
            raise PermissionError(13, "fixture: the new App cannot take its place")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", refuse_the_new_one)

    with pytest.raises(PermissionError):
        service._replace_app_in_place(context, spec, home, staged)

    assert not _is_new(home, spec) and (home / "App" / "150.0.0.0" / "brave.dll").is_file()
    assert _leftovers(home) == []


def test_the_new_browser_takes_the_place_of_the_old_one(tmp_path: Path) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / spec.folder, spec)
    staged = _pe(context.paths.workspace / "staged-App" / spec.executable, NEW_BROWSER).parent

    service._replace_app_in_place(context, spec, home, staged)

    assert _is_new(home, spec) and not (home / "App" / "150.0.0.0").exists()
    assert _leftovers(home) == [] and not staged.exists()


# --- Cancel ----------------------------------------------------------------------------


def _cancel_once_the_wrapper_is_placed(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Cancel is pressed when everything is prepared and nothing is yet put in place."""
    pressed: list[bool] = []
    place = service._place_wrapper

    def place_then_cancel(*arguments: object, **options: object) -> object:
        value = place(*arguments, **options)
        pressed.append(True)
        return value

    monkeypatch.setattr(service, "_place_wrapper", place_then_cancel)
    return pressed


def test_cancel_before_a_new_build_is_published_publishes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = _offline(context, monkeypatch)
    pressed = _cancel_once_the_wrapper_is_placed(monkeypatch)

    result = execute_operation(context.paths, context.operation, cancel_callback=lambda: bool(pressed))

    assert pressed and result.ok is False and "Cancelled before Brave was put in place" in result.message
    assert not (service._portable_root(context) / spec.folder).exists()
    assert not (service._portable_root(context) / "_tmp").exists()


def test_cancel_before_an_update_is_put_in_place_leaves_the_build_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, "update_selected")
    spec = _offline(context, monkeypatch)
    home = _our_build(context.paths.input / spec.folder, spec)
    pressed = _cancel_once_the_wrapper_is_placed(monkeypatch)

    result = execute_operation(context.paths, context.operation, cancel_callback=lambda: bool(pressed))

    assert result.ok is False and "Updated before that: nothing" in result.message
    assert not _is_new(home, spec) and _leftovers(home) == []
    assert not (home / service.BUILD_STAMP_FILE).exists()


def test_cancel_while_the_new_browser_is_being_copied_takes_the_copy_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, "update_selected")
    spec = _offline(context, monkeypatch)
    home = context.paths.input / "My carried browser"
    _pe(home / spec.executable, b"old browser")
    _profile(home / "User Data", "personal bookmarks")
    pressed: list[bool] = []
    bring = service._bring_app_beside

    def bring_then_cancel(*arguments: object, **options: object) -> Path:
        incoming = bring(*arguments, **options)
        pressed.append(True)
        return incoming

    monkeypatch.setattr(service, "_bring_app_beside", bring_then_cancel)

    result = execute_operation(context.paths, context.operation, cancel_callback=lambda: bool(pressed))

    # Not rearranged, not renamed, and the copy of the new browser is gone again.
    assert result.ok is False and _leftovers(home) == []
    assert (home / spec.executable).read_bytes().endswith(b"old browser")
    assert (home / "User Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "personal bookmarks"


def test_cancel_between_two_builds_stops_the_batch_and_names_what_was_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, "update_selected")
    spec = _offline(context, monkeypatch)
    first = _our_build(context.paths.input / "A first build", spec)
    second = _our_build(context.paths.input / "B second build", spec)
    done: list[str] = []
    build_one = service._build_one

    def build_then_cancel(*arguments: object, **options: object) -> dict[str, object]:
        result = build_one(*arguments, **options)
        done.append(str(result["artifact"]))
        return result

    monkeypatch.setattr(service, "_build_one", build_then_cancel)

    result = execute_operation(context.paths, context.operation, cancel_callback=lambda: bool(done))

    assert result.ok is False and "Updated before that: A first build" in result.message
    assert _is_new(first, spec) and not _is_new(second, spec)


def test_cancel_during_a_download_leaves_no_half_of_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream(BytesIO):
        headers = {"Content-Length": str(4 * 1024 * 1024)}

    context = _context(tmp_path)
    reads: list[int] = []
    context.cancel_callback = lambda: len(reads) >= 1
    stream = Stream(b"x" * (4 * 1024 * 1024))
    read = stream.read
    stream.read = lambda size=-1: reads.append(size) or read(size)  # type: ignore[method-assign]
    monkeypatch.setattr(service, "urlopen", lambda *_arguments, **_options: stream)
    target = context.paths.workspace / "installer.exe"

    with pytest.raises(service.OperationCancelled):
        service._download(context, "https://example.test/installer.exe", target, "Brave", use_cache=False)

    assert len(reads) == 1 and not target.exists()
    assert not target.with_name(target.name + ".part").exists()


def test_a_cancel_is_not_answered_by_falling_back_to_the_reserve(tmp_path: Path) -> None:
    """The library download takes a RuntimeError for "the source is unavailable" and carries on."""
    context = _context(tmp_path)

    def cancelled_download(*_arguments: object, **_options: object) -> None:
        raise service.OperationCancelled("Cancelled before the library was downloaded.")

    assert not issubclass(service.OperationCancelled, RuntimeError)
    with pytest.raises(service.OperationCancelled):
        libraries.download_library(
            context, "proxy_library", "x64", lambda: ("1.0", "library.zip", "https://example.test/library.zip"),
            cancelled_download, context.paths.workspace,
        )


# --- the findings of the audit of 11.10.2026 ------------------------------------------


class _Body(BytesIO):
    """A reply of a server: its headers, and a body that may be shorter than they say."""

    def __init__(self, body: bytes, length: int | None = None, breaks: BaseException | None = None) -> None:
        super().__init__(body)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self._breaks = breaks

    def read(self, size: int = -1) -> bytes:  # noqa: D102
        chunk = super().read(size)
        if not chunk and self._breaks is not None:
            raise self._breaks
        return chunk

    def __enter__(self) -> "_Body":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def test_a_download_cut_short_is_not_kept_as_the_whole_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection that closes early ends the reading without an error: the bytes are counted."""
    context = _context(tmp_path)
    target = context.paths.workspace / "installer.exe"
    monkeypatch.setattr(service, "urlopen", lambda *_arguments, **_options: _Body(b"six by", length=10))

    with pytest.raises(RuntimeError, match="closed at 6 of 10 bytes"):
        service._download(context, "https://example.test/installer.exe", target, "Brave", use_cache=False)

    assert not target.exists() and list(target.parent.glob("*.part")) == []


@pytest.mark.parametrize("failure", [ConnectionResetError("reset by peer"), TimeoutError("timed out")])
def test_a_connection_that_breaks_is_a_failed_download_and_leaves_no_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    context = _context(tmp_path)
    target = context.paths.workspace / "installer.exe"
    monkeypatch.setattr(service, "urlopen", lambda *_arguments, **_options: _Body(b"some", length=10, breaks=failure))

    with pytest.raises(RuntimeError, match="Download failed"):
        service._download(context, "https://example.test/installer.exe", target, "Brave", use_cache=False)

    assert not target.exists() and list(target.parent.glob("*.part")) == []


def test_a_download_that_does_not_open_is_not_kept_for_the_next_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected")
    spec = _offline(context, monkeypatch)
    _our_build(context.paths.input / spec.folder, spec)
    kept = _put(service._archives_dir(context) / "brave.zip", "cut short")
    note = _put(service._etag_note(kept), "etag")
    asset = service.DownloadedAsset("fixture", "https://example.test/brave.zip", kept, "", 9)
    monkeypatch.setattr(service, "_download", lambda *_arguments, **_options: asset)

    def does_not_open(*_arguments: object) -> Path:
        raise RuntimeError("7-Zip failed with exit code 2.")

    monkeypatch.setattr(service, "_unpack_browser", does_not_open)

    with pytest.raises(RuntimeError, match="7-Zip failed"):
        service.update_selected(context)

    assert not kept.exists() and not note.exists()
    assert "did not open and is not kept" in _log(context)


def test_of_the_downloads_of_a_browser_only_the_last_one_stays(tmp_path: Path) -> None:
    context = _context(tmp_path)
    archives = service._archives_dir(context)
    old = _put(archives / "brave-v1.90.100-win32-x64.zip", b"x" * 2048)
    keep = _put(archives / "brave-v1.93.134-win32-x64.zip", b"y" * 2048)
    others = [_put(archives / name) for name in
              ("ChromeStandaloneSetup64-150.0.1.2.exe", "ChromeStandaloneSetup64-150.0.1.2.exe.etag",
               "chrome_plus-1.2.zip", "brave-notes.txt")]

    service._prune_archives(context, browser_registry.browser("brave"), keep)

    assert not old.exists() and keep.is_file() and all(item.is_file() for item in others)
    chrome_keep = others[0]
    _put(archives / "ChromeStandaloneSetup64-149.0.0.1.exe")
    _put(archives / "ChromeStandaloneSetup64-149.0.0.1.exe.etag")
    _put(archives / "chrome-custom-0123456789ab-latest.exe")
    service._prune_archives(context, browser_registry.browser("chrome"), chrome_keep)
    assert sorted(item.name for item in archives.iterdir() if "chrome" in item.name.lower() and "plus" not in item.name) == [
        "ChromeStandaloneSetup64-150.0.1.2.exe", "ChromeStandaloneSetup64-150.0.1.2.exe.etag"]


def test_a_file_kept_under_a_name_without_a_version_is_fetched_anew(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Yandex-latest.exe`: the address stopped naming the version, and the name says nothing of what is inside."""
    context = _context(tmp_path, "update_selected", browsers=["yandex"])
    asked: list[dict[str, object]] = []

    def download(*_arguments: object, **options: object) -> None:
        asked.append(options)
        raise RuntimeError("enough for this check")

    monkeypatch.setattr(service, "_download", download)
    for published, filename in (("", "Yandex-latest.exe"), ("25.8.1.865", "Yandex-25.8.1.865.exe")):
        monkeypatch.setattr(service, "_browser_source",
                            lambda _context, _spec, p=published, f=filename: (p, "https://example.test/Yandex.exe", f))
        with pytest.raises(RuntimeError, match="enough"):
            service._build_one(context, browser_registry.browser("yandex"), engine="proxy_library", plus_archive=None,
                               plus_version="", wipe_registry=False, with_certificates=False, package_archive=False)

    assert [options["use_cache"] for options in asked] == [False, True]


# --- certificates ---------------------------------------------------------------------


def _certificates(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Certificates of the check's own, by their addresses: the service is told to expect their thumbprints."""
    import hashlib

    bodies, items = {}, []
    for item in service.RUSSIAN_TRUSTED_CERTIFICATES:
        body = b"\x30\x82" + str(item["name"]).encode()
        bodies[str(item["url"])] = body
        items.append({**item, "thumbprint": hashlib.sha1(body).hexdigest().upper()})
    monkeypatch.setattr(service, "RUSSIAN_TRUSTED_CERTIFICATES", tuple(items))
    return bodies


def _serving(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, bytes]) -> list[str]:
    asked: list[str] = []

    def download(_context: JobContext, url: str, target: Path, label: str, **_options: object) -> service.DownloadedAsset:
        asked.append(url)
        _put(target, bodies[url])
        return service.DownloadedAsset(label, url, target, "", len(bodies[url]))

    monkeypatch.setattr(service, "_download", download)
    return asked


def test_the_thumbprint_of_a_certificate_is_read_from_its_file(tmp_path: Path) -> None:
    import base64
    import hashlib

    body = b"\x30\x82\x01\x0a" + b"certificate"
    wanted = hashlib.sha1(body).hexdigest().upper()
    pem = b"-----BEGIN CERTIFICATE-----\r\n" + base64.encodebytes(body) + b"-----END CERTIFICATE-----\r\n"

    assert service._certificate_thumbprint(_put(tmp_path / "der.crt", body)) == wanted
    assert service._certificate_thumbprint(_put(tmp_path / "pem.crt", pem)) == wanted
    # Two certificates in one file: `certutil -addstore` would add both, and only one was compared.
    assert service._certificate_thumbprint(_put(tmp_path / "two.crt", pem + pem)) == ""
    assert service._certificate_thumbprint(_put(tmp_path / "junk.crt", "<html>not found</html>")) == ""
    assert service._certificate_thumbprint(tmp_path / "absent.crt") == ""


def test_a_file_lying_under_the_name_of_a_certificate_never_reaches_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared Target held another file as `russian_trusted_root_ca.crt`: it went into Root, then the thumbprint was looked for."""
    context = _context(tmp_path)
    bodies = _certificates(monkeypatch)
    _serving(monkeypatch, bodies)
    folder = service._certificates_dir(context)
    root = service.RUSSIAN_TRUSTED_CERTIFICATES[0]
    foreign = _put(folder / str(root["name"]), b"\x30\x82" + b"somebody else's root")
    added: list[Path] = []
    present: set[str] = set()

    class _Done:
        returncode, stdout, stderr = 0, "", ""

    def certutil(command: list[str], **_options: object) -> _Done:
        if "-addstore" in command:
            added.append(Path(command[-1]))
            present.add(service._certificate_thumbprint(Path(command[-1])))
        return _Done()

    monkeypatch.setattr(service.subprocess, "run", certutil)
    monkeypatch.setattr(service, "_certificate_present", lambda thumbprint, _store: thumbprint in present)

    result = service.install_certificates(context)

    assert len(result["installed"]) == 2 and len(added) == 2
    wanted = {str(item["thumbprint"]) for item in service.RUSSIAN_TRUSTED_CERTIFICATES}
    assert {service._certificate_thumbprint(path) for path in added} == wanted
    # The other file is somebody's: set aside under a name of its own, not deleted.
    aside = [item for item in folder.iterdir() if ".not-" in item.name]
    assert len(aside) == 1 and aside[0].read_bytes() == b"\x30\x82" + b"somebody else's root"
    assert foreign.read_bytes() == bodies[str(root["url"])]


def test_a_server_that_gives_another_file_installs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    bodies = _certificates(monkeypatch)
    _serving(monkeypatch, {url: b"<html>moved</html>" for url in bodies})
    monkeypatch.setattr(service.subprocess, "run", lambda *_a, **_k: pytest.fail("nothing may reach certutil"))

    with pytest.raises(RuntimeError, match="Nothing was installed"):
        service.install_certificates(context)

    assert [item.name for item in service._certificates_dir(context).iterdir()] == []


def test_certificates_are_fetched_before_the_browser_is_swapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server of certificates that does not answer costs nothing: the build is as it was, and says so.

    They used to be fetched after the swap: the browser was new, the run said "failed", the note
    of the build kept the old version, and the next run passed the build by as current.
    """
    context = _context(tmp_path, "update_selected", stage_certificates=True)
    spec = _offline(context, monkeypatch)
    home = _our_build(context.paths.input / spec.folder, spec)
    _certificates(monkeypatch)
    offline_download = service._download

    def no_route(_context: JobContext, url: str, *arguments: object, **options: object):
        if "gu-st.ru" in url:
            raise RuntimeError(f"Download failed: {url} (no route)")
        return offline_download(_context, url, *arguments, **options)

    monkeypatch.setattr(service, "_download", no_route)

    with pytest.raises(RuntimeError, match="no route"):
        service.update_selected(context)

    assert not _is_new(home, spec) and _leftovers(home) == []
    assert not (home / service.BUILD_STAMP_FILE).exists()


def test_certificates_go_into_the_updated_build_from_the_work_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected", stage_certificates=True)
    spec = _offline(context, monkeypatch)
    home = _our_build(context.paths.input / spec.folder, spec)
    bodies = _certificates(monkeypatch)
    offline_download = service._download
    asked: list[str] = []

    def download(_context: JobContext, url: str, target: Path, label: str, **options: object):
        if url in bodies:
            asked.append(url)
            _put(target, bodies[url])
            return service.DownloadedAsset(label, url, target, "", len(bodies[url]))
        return offline_download(_context, url, target, label, **options)

    monkeypatch.setattr(service, "_download", download)

    result = service.update_selected(context)

    assert len(result["updated"]) == 1 and result["updated"][0]["certificates"] is True and _is_new(home, spec)
    names = sorted(item.name for item in (home / "Certificates").iterdir())
    assert names == sorted([str(item["name"]) for item in service.RUSSIAN_TRUSTED_CERTIFICATES]
                           + ["Install-Russian-Trusted-CA.cmd", "Uninstall-Russian-Trusted-CA.cmd"])
    assert service._certificates_in(home) and len(asked) == 2
    # Updated again: the files proved in the build are taken from there, and the server is not asked.
    service.update_selected(context)
    assert len(asked) == 2


# --- the Defender guard ---------------------------------------------------------------


def _arguments(line: str) -> list[str]:
    """A command line taken apart the way Windows hands it to a program."""
    ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    words = ctypes.windll.shell32.CommandLineToArgvW(ctypes.c_wchar_p(line), ctypes.byref(count))
    try:
        return [words[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(words)


@pytest.mark.skipif(os.name != "nt", reason="the command line of Windows")
@pytest.mark.parametrize("folders", [["D:\\out", "E:\\"], ["E:\\"], ["D:\\out\\"], ["D:\\My Builds", "E:\\x"]])
def test_a_guarded_folder_that_ends_in_a_backslash_keeps_its_own_argument(folders: list[str]) -> None:
    """`"D:\\out|E:\\"`: the backslash escaped the closing quote and glued the next key to the path."""
    path = service._guard_path_argument([Path(item) for item in folders])
    line = f'powershell.exe -File "guard.ps1" -Action Begin -Path "{path}" -ParentPid 42 -Lock "C:\\w\\x.lock"'

    words = _arguments(line)

    assert words[words.index("-Path") + 1] == "|".join(str(Path(item)) for item in folders)
    assert words[words.index("-ParentPid") + 1] == "42" and words[-1] == "C:\\w\\x.lock"


def test_what_a_guard_excluded_is_remembered_until_it_is_seen_gone(tmp_path: Path) -> None:
    context = _context(tmp_path)

    service._guard_remember(context, [Path("E:\\Flash\\Builds"), Path("D:\\Out")], taken_back=False)
    service._guard_remember(context, [Path("e:\\flash\\builds")], taken_back=False)
    assert service._guard_pending(context) == ["D:\\Out", "e:\\flash\\builds"]

    service._guard_remember(context, [Path("D:\\OUT")], taken_back=True)
    assert service._guard_pending(context) == ["e:\\flash\\builds"]
    service._guard_remember(context, [Path("E:\\Flash\\Builds")], taken_back=True)
    assert service._guard_pending(context) == [] and not service._guard_pending_file(context).exists()


@pytest.mark.skipif(os.name != "nt", reason="Defender is a thing of Windows")
def test_the_safety_net_takes_back_what_an_earlier_run_left_and_does_not_call_a_failure_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, "defender_exclusion_remove")
    service._guard_remember(context, [Path("E:\\Flash\\Builds")], taken_back=False)
    answers = ["FAILED\tthe exclusion was not removed: E:\\Flash\\Builds (access denied)", "REMOVED\tall of them"]
    asked: list[str] = []

    def elevated(_script: Path, arguments: list[str]) -> int:
        asked.append(arguments[arguments.index("-Path") + 1].strip('"'))
        Path(arguments[arguments.index("-Result") + 1].strip('"')).write_text(answers.pop(0), encoding="utf-8")
        return 42

    monkeypatch.setattr(service, "_defender_active", lambda _context: True)
    monkeypatch.setattr(service, "_run_elevated_powershell", elevated)

    with pytest.raises(RuntimeError, match="was not removed"):
        service.defender_exclusion_remove(context)
    assert service._guard_pending(context) == ["E:\\Flash\\Builds"]

    result = service.defender_exclusion_remove(context)

    assert result["removed"] is True and service._guard_pending(context) == []
    for path in asked:
        named = path.split("|")
        assert "E:\\Flash\\Builds" in named and str(service._output_root(context)) in named


def test_the_library_alone_asks_for_one_guard_however_many_browsers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import contextlib

    context = _context(tmp_path, "update_libraries_selected", browsers=["brave", "yandex"], guard_defender=True)
    for browser_id in ("brave", "yandex"):
        spec = browser_registry.browser(browser_id)
        _pe(context.paths.input / spec.folder / "App" / spec.executable, b"browser")
    opened: list[list[str]] = []
    asked: list[str] = []

    @contextlib.contextmanager
    def guard(_context: JobContext, paths: object):
        opened.append([str(item) for item in paths])
        yield

    def no_library(_context: JobContext, _engine: str, arch: str) -> None:
        asked.append(arch)
        raise RuntimeError("stop here")

    monkeypatch.setattr(service, "_defender_guard", guard)
    monkeypatch.setattr(service, "_download_wrapper", no_library)

    with pytest.raises(RuntimeError, match="stop here"):
        service.update_libraries_selected(context)

    assert asked == ["x64", "x64"]
    assert opened == [[str(service._output_root(context)), str(service._input_root(context))]]


# --- STOP inside the long steps -------------------------------------------------------


def _stopping_after(context: JobContext, calls: int) -> JobContext:
    """The same context with a stop that comes at the given asking."""
    import dataclasses

    seen = {"count": 0}

    def stop() -> bool:
        seen["count"] += 1
        return seen["count"] > calls

    return dataclasses.replace(context, cancel_callback=stop)


def test_a_stop_ends_seven_zip_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import time

    context = _stopping_after(_context(tmp_path), 0)
    monkeypatch.setattr(service, "_require_7zip", lambda _context: Path(sys.executable))
    began = time.monotonic()

    with pytest.raises(service.OperationCancelled, match="the build was packed"):
        service._run_7z(context, ["-c", "import time; time.sleep(120)"], doing="the build was packed")

    assert time.monotonic() - began < 20


def test_an_archive_is_whole_or_not_there(tmp_path: Path) -> None:
    """It was written under its final name, the one before it deleted first."""
    context = _context(tmp_path)
    build = tmp_path / "work" / "Brave Portable"
    for index in range(6):
        _put(build / "App" / f"file{index}.bin", b"x" * 100)
    archive = service._portable_root(context) / "Brave Portable.zip"
    _put(archive, "the archive of the run before")

    with pytest.raises(service.OperationCancelled):
        service._zip_dir(_stopping_after(context, 3), build, archive)

    assert archive.read_bytes() == b"the archive of the run before"
    assert [item.name for item in archive.parent.iterdir() if item.name.endswith(".part")] == []

    service._zip_dir(context, build, archive)
    with zipfile.ZipFile(archive) as packed:
        assert len([name for name in packed.namelist() if name.endswith(".bin")]) == 6


def test_a_stop_while_a_build_is_published_leaves_no_half_of_it(tmp_path: Path) -> None:
    context = _context(tmp_path)
    work = tmp_path / "work" / "Brave Portable"
    for index in range(6):
        _put(work / "App" / f"file{index}.bin")

    with pytest.raises(service.OperationCancelled):
        service._publish(_stopping_after(context, 2), work, "Brave Portable")

    assert not (service._portable_root(context) / "Brave Portable").exists()


# --- what a run that was ended hard leaves behind ---------------------------------------


def test_the_work_folders_of_a_run_that_did_not_finish_are_cleared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / spec.folder, spec)
    dead_new = _put(home / "App.incoming-111" / spec.executable, "never put in").parent
    dead_old = _put(home / "App.replaced-111" / spec.executable, "never removed").parent
    alive = _put(home / "App.incoming-222" / spec.executable, "another run is at work").parent
    monkeypatch.setattr(service, "_process_alive", lambda pid: pid == 222)

    service._clear_leftovers(context, spec, home)

    assert not dead_new.exists() and not dead_old.exists() and alive.is_dir()
    assert (home / "App" / spec.executable).is_file()


def test_a_build_caught_between_the_two_renames_gets_its_browser_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / spec.folder, spec)
    (home / "App").rename(home / "App.replaced-111")
    _put(home / "App.incoming-111" / spec.executable, "never put in")
    monkeypatch.setattr(service, "_process_alive", lambda _pid: False)

    service._clear_leftovers(context, spec, home)

    assert (home / "App" / spec.executable).read_bytes().endswith(b"old browser")
    assert _leftovers(home) == [] and "[RECOVER] Brave" in _log(context)


# --- Check ----------------------------------------------------------------------------


def test_one_vendor_that_does_not_answer_does_not_end_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A network that fails is an `OSError`, and Check caught `RuntimeError` alone: a traceback instead of the rows."""
    from urllib.error import URLError

    context = _context(tmp_path, "check_updates", browsers=["brave", "yandex"])

    def source(_context: JobContext, spec: browser_registry.BrowserSpec) -> tuple[str, str, str]:
        if spec.id == "brave":
            raise URLError("no route to api.github.com")
        return "25.8.1.865", "https://example.test/Yandex.exe", "Yandex-25.8.1.865.exe"

    monkeypatch.setattr(service, "_browser_source", source)
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_arguments: "2.0")

    result = service.check_updates(context)

    rows = {row["browser"]: row for row in result["browsers"]}
    assert "no route" in rows["Brave"]["error"] and rows["Yandex Browser"]["published"] == "25.8.1.865"


# --- rearranging a build from elsewhere, and the way back -------------------------------


def test_a_rearranging_that_breaks_puts_the_profile_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A profile left under the name of a work folder is passed by every later search."""
    home = tmp_path / "PortableApps Chrome"
    _put(home / "App" / "Chrome-bin" / "chrome.exe", "MZ")
    _profile(home / "Data" / "profile", "keep me")
    _put(home / "Data" / "settings" / "launcher.ini", "[x]")
    [layout] = adoption.builds_in(tmp_path, "chrome.exe")
    assert layout.profile_dir == home / "Data" / "profile"

    def held(*_arguments: object) -> str:
        raise PermissionError(13, "a file in it is held")

    monkeypatch.setattr(adoption, "_set_aside", held)

    with pytest.raises(OSError):
        adoption.normalize(layout, lambda _line: None, shutil.rmtree, ["chrome.exe"])

    assert (home / "Data" / "profile" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "keep me"
    assert _leftovers(home) == []


@pytest.mark.skipif(os.name != "nt", reason="a file held open cannot be renamed on Windows")
def test_a_flat_build_whose_browser_is_held_is_left_as_it_was(tmp_path: Path) -> None:
    home = tmp_path / "Chrome flat"
    _put(home / "chrome.exe", "MZ")
    _put(home / "chrome_proxy.exe", "MZ")
    _put(home / "145.0.7632.110" / "chrome.dll")
    _profile(home / "Data", "keep me")
    [layout] = adoption.builds_in(tmp_path, "chrome.exe")
    names = ["chrome.exe", "chrome_proxy.exe", "145.0.7632.110"]

    with (home / "chrome.exe").open("rb"), pytest.raises(OSError):
        adoption.normalize(layout, lambda _line: None, shutil.rmtree, names)

    assert all((home / name).exists() for name in names)
    assert _leftovers(home) == [] and not (home / "App").exists()
    assert (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "keep me"


# --- one search for every operation that works on a build already there ------------------


def _library_offline(context: JobContext, monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = context.paths.workspace / "proxy-library.zip"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wrapper, "w") as zipped:
        zipped.write(_pe(context.paths.workspace / "wrapper.dll", b"new wrapper"), libraries.PROXY_DLLS["x64"])
    monkeypatch.setattr(service, "_download_wrapper", lambda *_arguments: (wrapper, "2.0.0.0"))


def test_the_library_alone_finds_a_build_where_update_finds_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One folder deeper, under a name of its own: Check listed it, Update updated it, and "Update DLL only" said there was none."""
    context = _context(tmp_path, "update_libraries_selected")
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / "Portable" / "Brave of mine", spec)
    _library_offline(context, monkeypatch)

    result = service.update_libraries_selected(context)

    assert [row["build"] for row in result["updated"]] == [str(home)]
    assert (home / "App" / "version.dll").read_bytes().endswith(b"new wrapper")
    assert (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "my bookmarks"


def test_the_library_alone_takes_every_build_of_a_ticked_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_libraries_selected")
    spec = browser_registry.browser("brave")
    homes = [_our_build(context.paths.input / name, spec) for name in ("Brave at home", "Brave at work")]
    _library_offline(context, monkeypatch)

    result = service.update_libraries_selected(context)

    assert sorted(row["build"] for row in result["updated"]) == sorted(str(home) for home in homes)


def test_the_library_alone_leaves_a_build_from_elsewhere_to_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Its browser is not in `App` yet: Update makes the standard layout of it, the library alone does not."""
    context = _context(tmp_path, "update_libraries_selected")
    spec = browser_registry.browser("brave")
    home = context.paths.input / "Brave from elsewhere"
    _pe(home / "Browser" / spec.executable, b"old browser")
    _profile(home / "Data", "theirs")
    _library_offline(context, monkeypatch)

    with pytest.raises(RuntimeError, match="No build in the standard layout: Brave from elsewhere - run Update first"):
        service.update_libraries_selected(context)

    assert not (home / "App").exists() and not (home / "Browser" / "version.dll").exists()
    assert "not in App. Update brings the build to the standard layout" in _log(context)


def test_certificates_are_added_to_a_build_found_at_any_depth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "stage_certificates_selected", certificate_browsers=["brave"])
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / "Portable" / "Brave of mine", spec)
    _serving(monkeypatch, _certificates(monkeypatch))

    result = service.stage_certificates_selected(context)

    assert [row["build"] for row in result["staged"]] == [str(home)]
    assert service._certificates_in(home)


def test_a_7z_of_an_updated_build_carries_no_profile_either(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same build through the real 7-Zip of the program: what goes in is named, the rest stays out."""
    import subprocess

    seven_zip = Path(__file__).resolve().parents[1] / "tools" / "7zip" / "bin" / "7za.exe"
    if not seven_zip.is_file():
        pytest.skip("the portable 7-Zip is not in the project: step 06 of builder_main.cmd")
    context = _context(tmp_path, "update_selected", archive_format="7z")
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / "Brave of mine", spec, bookmarks="mine alone")
    _put(home / f"{spec.folder}.cmd", "@echo off\r\n")
    _put(home / service.BUILD_STAMP_FILE, "{}")
    _put(home / "my-notes.txt", "mine")
    monkeypatch.setattr(service, "_require_7zip", lambda _context: seven_zip)

    archive = service._archive_build(context, home, "Brave of mine", service._handover_parts(spec, home))

    listed = subprocess.run([str(seven_zip), "l", "-slt", str(archive)], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", check=True).stdout
    paths = sorted(line[len("Path = "):] for line in listed.splitlines() if line.startswith("Path = Brave of mine"))
    assert "Brave of mine\\App\\brave.exe" in paths and f"Brave of mine\\{spec.folder}.cmd" in paths
    assert [path for path in paths if "\\Data" in path or "\\Cache" in path] == ["Brave of mine\\Cache", "Brave of mine\\Data"]
    assert not [path for path in paths if "Bookmarks" in path or "my-notes" in path]
    assert archive.name == "Brave of mine.7z" and not list(archive.parent.glob("*.part"))
    assert not list(service._tmp_dir(context).glob("handover-*"))


# --- what a tool of Windows says is read in the encoding it says it in ---------------------


def test_a_line_of_a_windows_tool_is_read_as_it_was_written() -> None:
    """certutil writes in the ANSI code page; read as UTF-8 its lines were rows of replacement marks."""
    said = 'Сертификат "Russian Trusted Root CA" добавлен в хранилище.'

    assert service._windows_text(said.encode("cp1251")) == said
    assert service._windows_text(said.encode("utf-8")) == said
    assert service._windows_text(b"CertUtil: -addstore command completed successfully.") == "CertUtil: -addstore command completed successfully."
    assert service._windows_text(said) == said and service._windows_text(None) == ""
    # A script may change the code page half way: every line is read on its own, empty ones are dropped.
    mixed = said.encode("cp1251") + b"\r\n\r\n" + "Готово".encode("utf-8") + b"\r\n"
    assert service._windows_lines(mixed) == [said, "Готово"]


@pytest.mark.skipif(os.name != "nt", reason="the code pages of Windows")
def test_what_certutil_says_reaches_the_journal_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    _serving(monkeypatch, _certificates(monkeypatch))
    present: set[str] = set()
    said = ['Root "Доверенные корневые центры сертификации"', "Подпись соответствует открытому ключу",
            'Сертификат "Russian Trusted Root CA" добавлен в хранилище.', "CertUtil: -addstore — команда успешно выполнена."]

    class _Done:
        returncode, stderr = 0, b""
        stdout = "\r\n".join(said).encode("cp1251")

    def certutil(command: list[str], **options: object) -> _Done:
        assert "text" not in options and "encoding" not in options, "the output of certutil is taken as bytes"
        if "-addstore" in command:
            present.add(service._certificate_thumbprint(Path(command[-1])))
        return _Done()

    monkeypatch.setattr(service.subprocess, "run", certutil)
    monkeypatch.setattr(service, "_certificate_present", lambda thumbprint, _store: thumbprint in present)

    service.install_certificates(context)

    log = _log(context)
    assert all(line in log for line in said) and "�" not in log


def test_seven_zip_names_a_file_with_cyrillic_readably(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """By itself 7-Zip names a file in the OEM code page of the console."""
    seven_zip = Path(__file__).resolve().parents[1] / "tools" / "7zip" / "bin" / "7za.exe"
    if not seven_zip.is_file():
        pytest.skip("the portable 7-Zip is not in the project: step 06 of builder_main.cmd")
    context = _context(tmp_path)
    monkeypatch.setattr(service, "_require_7zip", lambda _context: seven_zip)

    with pytest.raises(RuntimeError, match="7-Zip failed"):
        service._run_7z(context, ["l", str(tmp_path / "Нет такого архива.zip")])

    log = _log(context)
    assert "Нет такого архива.zip" in log and "�" not in log
