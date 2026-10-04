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


def test_two_builds_of_one_browser_are_packed_into_two_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected", package_archive=True, archive_format="zip")
    spec = _offline(context, monkeypatch)
    for name in ("Work browser", "Private browser"):
        _our_build(context.paths.input / name, spec, bookmarks=name)

    result = service.update_selected(context)

    archives = [Path(row["artifact"]) for row in result["updated"]]
    assert sorted(item.name for item in archives) == ["Private browser.zip", "Work browser.zip"]
    assert {item.stem: _bookmarks_in(item) for item in archives} == {
        "Private browser": ["Private browser"],
        "Work browser": ["Work browser"],
    }


def test_builds_whose_folders_share_a_name_still_get_an_archive_each(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, "update_selected", package_archive=True, archive_format="zip")
    spec = _offline(context, monkeypatch)
    for shelf in ("At home", "At work"):
        _our_build(context.paths.input / shelf / "Brave", spec, bookmarks=shelf)

    result = service.update_selected(context)

    archives = [Path(row["artifact"]) for row in result["updated"]]
    assert sorted(item.name for item in archives) == ["Brave (2).zip", "Brave.zip"]
    assert sorted(_bookmarks_in(item)[0] for item in archives) == ["At home", "At work"]


def test_unique_names_ignore_letter_case() -> None:
    taken: set[str] = set()

    assert [service._unique_name(name, taken) for name in ("Brave", "brave", "BRAVE")] == ["Brave", "brave (2)", "BRAVE (3)"]


# --- the copy that stops halfway ------------------------------------------------------


def test_a_copy_that_stops_halfway_leaves_the_old_browser_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    home = _our_build(context.paths.input / spec.folder, spec)
    staged = _put(context.paths.workspace / "staged-App" / spec.executable, "new").parent

    def volume_fills_up(source: str, target: str) -> None:
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
