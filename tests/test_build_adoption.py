"""A build brought in from elsewhere is found by its executable and its profile.

Every layout below is one that exists in the wild. The profile carries a marker
file so each test can say the one thing that matters: it is still there.
"""

from __future__ import annotations

from pathlib import Path
import ctypes
import os

import pytest

from system_core.core.jobs import JobContext
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import browser_registry, build_adoption as adoption
from system_core.services import browsers_portable_service as service


def _touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _profile(folder: Path, marker: str = "keep me") -> Path:
    _touch(folder / "Local State", "{}")
    _touch(folder / "Default" / "Preferences", "{}")
    _touch(folder / "Default" / "Bookmarks", marker)
    return folder


def _browser(folder: Path, executable: str = "chrome.exe") -> Path:
    _touch(folder / executable, "MZ")
    _touch(folder / "145.0.7632.110" / "chrome.dll", "x")
    _touch(folder / "version.dll", "wrapper")
    return folder


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)


# What a freshly unpacked browser has beside its executable, as the service names it.
FRESH_BROWSER = ("chrome.exe", "chrome_proxy.exe", "154.0.8037.98")


def _normalize(layout: adoption.Layout) -> list[str]:
    log: list[str] = []
    adoption.normalize(layout, log.append, _remove_tree, FRESH_BROWSER)
    return log


def _bookmarks(home: Path) -> str:
    return (home / "Data" / "Default" / "Bookmarks").read_text(encoding="utf-8")


def _owner_build(home: Path) -> Path:
    """The layout that started this: the browser in `Chrome`, not in `App`."""
    _browser(home / "Chrome")
    _touch(home / "Chrome" / "GChromeLauncher.bat", "start chrome")
    _profile(home / "Data")
    _touch(home / "Cache" / "Default" / "data_0", "c")
    _touch(home / "desktop.ini", "[.ShellClassInfo]")
    return home


# --- recognising ---------------------------------------------------------------


def test_browser_in_a_differently_named_folder_is_found(tmp_path: Path) -> None:
    home = _owner_build(tmp_path / "input" / "Google Chrome Portable")

    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    assert layout.home == home
    assert layout.browser_dir == home / "Chrome"
    assert layout.profile_dir == home / "Data"
    assert not layout.standard


def test_our_own_layout_is_standard(tmp_path: Path) -> None:
    home = tmp_path / "input" / "Google Chrome Portable"
    _browser(home / "App")
    _profile(home / "Data")

    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    assert layout.home == home
    assert layout.standard


def test_portableapps_layout_takes_the_real_profile_not_the_template(tmp_path: Path) -> None:
    home = tmp_path / "input" / "GoogleChromePortable"
    _browser(home / "App" / "Chrome-bin")
    _profile(home / "App" / "DefaultData" / "profile", marker="template")
    _profile(home / "Data" / "profile")
    _touch(home / "Data" / "settings" / "GoogleChromePortableSettings.ini", "x")

    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    assert layout.home == home
    assert layout.browser_dir == home / "App" / "Chrome-bin"
    assert layout.profile_dir == home / "Data" / "profile"


def test_flat_build_with_the_profile_inside(tmp_path: Path) -> None:
    home = tmp_path / "input" / "chrome-win"
    _browser(home)
    _profile(home / "User Data")

    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    assert layout.home == home
    assert layout.browser_dir == home
    assert layout.profile_dir == home / "User Data"


def test_build_that_was_never_started_has_no_profile(tmp_path: Path) -> None:
    home = tmp_path / "input" / "Fresh"
    _browser(home / "Chrome")

    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    assert layout.home == home
    assert layout.profile_dir is None


def test_two_builds_side_by_side_stay_two_builds(tmp_path: Path) -> None:
    scope = tmp_path / "input"
    first = _owner_build(scope / "A")
    second = scope / "B"
    _browser(second / "App")
    _profile(second / "Data")

    homes = {layout.home for layout in adoption.builds_in(scope, "chrome.exe")}

    assert homes == {first, second}


def test_a_neighbours_profile_is_not_borrowed(tmp_path: Path) -> None:
    """A build with no profile must not climb to the container for someone else's."""
    scope = tmp_path / "input"
    fresh = scope / "Fresh"
    _browser(fresh / "Chrome")
    _browser(scope / "Other" / "App")
    _profile(scope / "Other" / "Data")

    by_home = {layout.home: layout for layout in adoption.builds_in(scope, "chrome.exe")}

    assert set(by_home) == {fresh, scope / "Other"}
    assert by_home[fresh].profile_dir is None


def test_build_one_folder_deeper_is_still_found(tmp_path: Path) -> None:
    scope = tmp_path / "input"
    home = _owner_build(scope / "Old stuff" / "Chrome 145")

    [layout] = adoption.builds_in(scope, "chrome.exe")

    assert layout.home == home


def test_a_container_is_never_taken_for_a_build(tmp_path: Path) -> None:
    scope = tmp_path / "input"
    _browser(scope / "Chrome")
    _profile(scope / "Data")

    [layout] = adoption.builds_in(scope, "chrome.exe", stop=[scope])

    assert layout.home == scope / "Chrome"


def test_an_executable_inside_a_profile_is_not_a_browser(tmp_path: Path) -> None:
    scope = tmp_path / "input"
    home = scope / "Build"
    _profile(home / "Data")
    _touch(home / "Data" / "chrome.exe", "MZ")

    assert adoption.builds_in(scope, "chrome.exe") == []


# --- rearranging ----------------------------------------------------------------


def test_owner_layout_becomes_app_data_cache(tmp_path: Path) -> None:
    home = _owner_build(tmp_path / "input" / "Google Chrome Portable")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    _normalize(layout)

    assert (home / "App" / "chrome.exe").is_file()
    assert not (home / "Chrome").exists()
    assert _bookmarks(home) == "keep me"
    assert (home / "Cache" / "Default" / "data_0").is_file()
    assert not (home / "desktop.ini").exists()


def test_portableapps_profile_moves_up_into_data(tmp_path: Path) -> None:
    home = tmp_path / "input" / "GoogleChromePortable"
    _browser(home / "App" / "Chrome-bin")
    _profile(home / "Data" / "profile")
    _touch(home / "Data" / "settings" / "GoogleChromePortableSettings.ini", "x")
    _touch(home / "GoogleChromePortable.exe", "MZ")
    _touch(home / "Other" / "Source" / "Readme.txt", "notes")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    _normalize(layout)

    assert (home / "App" / "Chrome-bin" / "chrome.exe").is_file()
    assert _bookmarks(home) == "keep me"
    assert not (home / "Data" / "settings").exists()
    assert not (home / "GoogleChromePortable.exe").exists()
    # Not ours to judge: an unknown folder is left where it was.
    assert (home / "Other" / "Source" / "Readme.txt").is_file()


def test_flat_build_is_gathered_into_app(tmp_path: Path) -> None:
    home = tmp_path / "input" / "chrome-win"
    _browser(home)
    _profile(home / "User Data")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    _normalize(layout)

    assert (home / "App" / "chrome.exe").is_file()
    assert (home / "App" / "145.0.7632.110" / "chrome.dll").is_file()
    assert not (home / "chrome.exe").exists()
    assert _bookmarks(home) == "keep me"


def test_a_foreign_data_folder_gives_way_to_the_profile(tmp_path: Path) -> None:
    home = tmp_path / "input" / "Build"
    _browser(home / "Chrome")
    _profile(home / "Profile")
    _touch(home / "Data" / "launcher.cfg", "junk")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    _normalize(layout)

    assert _bookmarks(home) == "keep me"
    assert not (home / "Data" / "launcher.cfg").exists()
    assert not (home / "Profile").exists()


def test_a_second_profile_is_set_aside_not_deleted(tmp_path: Path) -> None:
    home = tmp_path / "input" / "GoogleChromePortable"
    _browser(home / "App" / "Chrome-bin")
    _profile(home / "Data" / "profile")
    _profile(home / "Data" / "work", marker="second profile")
    [layout] = adoption.builds_in(tmp_path / "input", "chrome.exe")

    _normalize(layout)

    assert _bookmarks(home) == "keep me"
    [aside] = [item for item in home.iterdir() if item.name.startswith("Data.foreign-")]
    assert (aside / "work" / "Default" / "Bookmarks").read_text(encoding="utf-8") == "second profile"


def test_running_browser_is_reported_before_anything_moves(tmp_path: Path) -> None:
    executable = _touch(tmp_path / "chrome.exe", "MZ")
    assert not adoption.in_use(executable)

    if os.name != "nt":
        pytest.skip("the sharing rule being tested is a Windows one")
    # Hold the file the way a running image is held: no write access for anyone else.
    handle = ctypes.windll.kernel32.CreateFileW(str(executable), 0x80000000, 0x00000001, None, 3, 0, None)
    assert handle not in (0, -1)
    try:
        assert adoption.in_use(executable)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


# --- choosing the build in the service ---------------------------------------------


def _context(tmp_path: Path, **parameters: object) -> JobContext:
    paths = get_project_paths(tmp_path)
    ensure_project_dirs(paths)
    return JobContext(
        paths=paths,
        operation=Operation(
            id="test",
            title="Test",
            description="",
            service="system_core.services.browsers_portable_service:update_selected",
            parameters=dict(parameters),
        ),
        log_file=paths.logs / "test.log",
        report_dir=paths.report,
    )


def _says(monkeypatch: pytest.MonkeyPatch, product: str) -> None:
    monkeypatch.setattr(adoption, "file_string", lambda _path, _name: product)


def test_google_chrome_is_taken_under_any_folder_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    home = _owner_build(context.paths.input / "My old Chrome")
    _says(monkeypatch, "Google Chrome")
    spec = browser_registry.browser("chrome")

    layout = service._adoptable_build(context, spec)

    assert layout is not None and layout.home == home
    assert not service._is_standard_build(context, spec, layout)
    assert service._adopted_home(context, spec, layout) == context.paths.input / "Google Chrome Portable"


def test_our_own_build_is_taken_without_asking_the_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A standard build under its own name was always enough, and still is."""
    context = _context(tmp_path)
    spec = browser_registry.browser("chrome")
    home = context.paths.input / spec.folder
    _browser(home / "App")
    _profile(home / "Data")
    _says(monkeypatch, "")

    layout = service._adoptable_build(context, spec)

    assert layout is not None and layout.home == home
    assert service._is_standard_build(context, spec, layout)


def test_a_silent_executable_is_trusted_only_under_the_standard_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("chrome")
    _says(monkeypatch, "")
    _owner_build(context.paths.input / "Some folder")
    assert service._adoptable_build(context, spec) is None

    named = _owner_build(context.paths.input / spec.folder)
    layout = service._adoptable_build(context, spec)
    assert layout is not None and layout.home == named


def test_chromium_is_not_updated_as_google_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`chrome.exe` is three browsers here; the file name must not decide."""
    context = _context(tmp_path)
    _owner_build(context.paths.input / "Google Chrome Portable")
    _says(monkeypatch, "Chromium")

    assert service._adoptable_build(context, browser_registry.browser("chrome")) is None


def test_chromium_builds_are_recognised_by_folder_name_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    _owner_build(context.paths.input / "Some Chromium")
    named = _owner_build(context.paths.input / "Chromium-Gost Portable")
    _says(monkeypatch, "Chromium")
    spec = browser_registry.browser("chromium_gost")

    layout = service._adoptable_build(context, spec)

    assert layout is not None and layout.home == named


def test_google_chrome_is_not_taken_for_chromium_gost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    _owner_build(context.paths.input / "Chromium-Gost Portable")
    _says(monkeypatch, "Google Chrome")

    assert service._adoptable_build(context, browser_registry.browser("chromium_gost")) is None


def test_the_build_brought_in_wins_over_the_published_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("chrome")
    published = service._portable_root(context) / spec.folder
    _browser(published / "App")
    _profile(published / "Data")
    brought = _owner_build(context.paths.input / "Chrome from the flash drive")
    _says(monkeypatch, "Google Chrome")

    layout = service._adoptable_build(context, spec)

    assert layout is not None and layout.home == brought


def test_published_build_is_still_found_when_nothing_was_brought(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("chrome")
    published = service._portable_root(context) / spec.folder
    _browser(published / "App")
    _profile(published / "Data")
    _says(monkeypatch, "Google Chrome")

    layout = service._adoptable_build(context, spec)

    assert layout is not None and layout.home == published
    assert service._is_standard_build(context, spec, layout)


def test_name_clash_is_reported_before_the_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("chrome")
    _owner_build(context.paths.input / "My old Chrome")
    (context.paths.input / spec.folder).mkdir()
    _says(monkeypatch, "Google Chrome")
    layout = service._adoptable_build(context, spec)

    assert layout is not None
    assert "already exists" in service._adoption_blocker(context, spec, layout)


def test_adopted_build_takes_the_standard_folder_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("chrome")
    _owner_build(context.paths.input / "My old Chrome")
    _says(monkeypatch, "Google Chrome")
    layout = service._adoptable_build(context, spec)
    assert layout is not None and service._adoption_blocker(context, spec, layout) == ""

    home = service._adopt_build(context, spec, layout)

    assert home == context.paths.input / spec.folder
    assert (home / "App" / "chrome.exe").is_file()
    assert _bookmarks(home) == "keep me"
    assert not (context.paths.input / "My old Chrome").exists()


def _says_by_folder(monkeypatch: pytest.MonkeyPatch, products: dict[str, str], default: str = "") -> None:
    """Each build's executable names its own product, as real ones do."""

    def product(path: Path, _name: str) -> str:
        return next((value for folder, value in products.items() if folder in Path(path).parts), default)

    monkeypatch.setattr(adoption, "file_string", product)


def _foreign_build(home: Path, executable: str, branch: str = "Browser") -> Path:
    _browser(home / branch, executable)
    _profile(home / "Data")
    return home


# --- several builds at once, and no browser ticked ----------------------------------


def test_nothing_ticked_means_every_browser(tmp_path: Path) -> None:
    specs, automatic = service._requested_specs(_context(tmp_path))
    assert automatic and [spec.id for spec in specs] == [spec.id for spec in browser_registry.BROWSERS]

    specs, automatic = service._requested_specs(_context(tmp_path, browsers=["brave"]))
    assert not automatic and [spec.id for spec in specs] == ["brave"]


def test_different_browsers_side_by_side_are_all_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    chrome = _owner_build(context.paths.input / "Chrome from work")
    yandex = _foreign_build(context.paths.input / "Yandex old", "browser.exe")
    brave = _foreign_build(context.paths.input / "Brave Portable", "brave.exe", branch="App")
    _says_by_folder(monkeypatch, {"Chrome from work": "Google Chrome"})
    specs, _automatic = service._requested_specs(context)

    found, unclaimed = service._find_builds(context, specs)

    assert {item.spec.id: item.home for item in found} == {"chrome": chrome, "yandex": yandex, "brave": brave}
    assert unclaimed == []
    assert all(item.alone for item in found)
    targets = {item.spec.id: service._adopted_home(context, item.spec, item.layout, item.alone).name for item in found}
    assert targets == {
        "chrome": "Google Chrome Portable",
        "yandex": "Yandex Browser Portable",
        "brave": "Brave Portable",
    }


def test_two_builds_of_one_browser_are_both_updated_and_keep_their_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    spec = browser_registry.browser("chrome")
    work = _owner_build(context.paths.input / "Chrome work")
    private = _owner_build(context.paths.input / "Chrome private")
    _says(monkeypatch, "Google Chrome")

    found, _unclaimed = service._find_builds(context, [spec])

    assert [item.home for item in found] == [private, work]
    assert not any(item.alone for item in found)
    assert [item.label for item in found] == ["Google Chrome (Chrome private)", "Google Chrome (Chrome work)"]
    for item in found:
        assert service._adoption_blocker(context, spec, item.layout, item.alone) == ""
        assert service._adopt_build(context, spec, item.layout, item.alone) == item.home
    assert _bookmarks(work) == "keep me" and _bookmarks(private) == "keep me"

    # Rearranged and still under their own names: nothing is left to do to them.
    again, _unclaimed = service._find_builds(context, [spec])
    assert [item.home for item in again] == [private, work]
    assert all(service._is_standard_build(context, spec, item.layout, item.alone) for item in again)


def test_a_build_in_the_source_hides_the_published_one_of_that_browser_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    brought = _owner_build(context.paths.input / "Chrome from work")
    for browser_id in ("chrome", "brave"):
        spec = browser_registry.browser(browser_id)
        published = service._portable_root(context) / spec.folder
        _browser(published / "App", spec.executable)
        _profile(published / "Data")
    _says_by_folder(monkeypatch, {"Chrome from work": "Google Chrome"})
    specs, _automatic = service._requested_specs(context)

    found, _unclaimed = service._find_builds(context, specs)

    by_id = {item.spec.id: item for item in found}
    assert by_id["chrome"].home == brought and not by_id["chrome"].published
    assert by_id["brave"].published


def test_chromium_under_any_name_needs_a_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    home = _owner_build(context.paths.input / "My Chromium")
    _says(monkeypatch, "Chromium")
    everything, _automatic = service._requested_specs(context)

    found, unclaimed = service._find_builds(context, everything)
    assert found == [] and [layout.home for layout in unclaimed] == [home]
    service._report_unclaimed(context, everything, unclaimed)
    log = context.log_file.read_text(encoding="utf-8")
    assert "My Chromium" in log and "Tick Chromium-Gost or Ungoogled Chromium" in log

    gost = browser_registry.browser("chromium_gost")
    found, unclaimed = service._find_builds(context, [gost], ["chromium_gost"])
    assert [item.home for item in found] == [home] and unclaimed == []


def test_two_chromium_ticks_cannot_share_an_unnamed_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    _owner_build(context.paths.input / "My Chromium")
    _says(monkeypatch, "Chromium")
    ticked = ["chromium_gost", "ungoogled_chromium"]

    found, unclaimed = service._find_builds(context, [browser_registry.browser(item) for item in ticked], ticked)

    assert found == [] and len(unclaimed) == 1


def test_a_tick_does_not_take_a_folder_named_after_another_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    _owner_build(context.paths.input / "Chromium-Gost Portable")
    _says(monkeypatch, "Chromium")
    ungoogled = browser_registry.browser("ungoogled_chromium")

    found, _unclaimed = service._find_builds(context, [ungoogled], ["ungoogled_chromium"])

    assert found == []


def _prepare_update(context: JobContext, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Run `update_selected` with the download and the assembly stubbed out."""
    calls: list[dict[str, object]] = []

    def build_one(_context: JobContext, spec: browser_registry.BrowserSpec, **arguments: object) -> dict[str, object]:
        adopt = arguments.get("adopt")
        calls.append({"id": spec.id, "home": arguments["keep_data_from"], "alone": getattr(adopt, "alone", None)})
        return {"browser": spec.name, "id": spec.id, "version": "200.0.0.1"}

    monkeypatch.setattr(service, "_require_7zip", lambda _context: None)
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_arguments: "2.0")
    monkeypatch.setattr(service, "_browser_source", lambda _context, _spec: ("200.0.0.1", "https://example.test", "x"))
    monkeypatch.setattr(service, "_file_version", lambda _path: "100.0.0.1")
    monkeypatch.setattr(service, "_build_one", build_one)
    return calls


def test_update_with_nothing_ticked_takes_every_build_in_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, guard_defender=False)
    chrome = _owner_build(context.paths.input / "Chrome from work")
    yandex = _foreign_build(context.paths.input / "Yandex old", "browser.exe")
    _says_by_folder(monkeypatch, {"Chrome from work": "Google Chrome"})
    calls = _prepare_update(context, monkeypatch)

    result = service.update_selected(context)

    assert [(call["id"], call["home"]) for call in calls] == [("chrome", chrome), ("yandex", yandex)]
    assert len(result["updated"]) == 2 and result["failed"] == [] and result["skipped"] == []


def test_update_with_nothing_ticked_and_nothing_found_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, guard_defender=False)
    _prepare_update(context, monkeypatch)

    with pytest.raises(RuntimeError, match="No portable build was found"):
        service.update_selected(context)


def test_a_ticked_browser_with_no_build_is_skipped_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, guard_defender=False, browsers=["brave"])
    calls = _prepare_update(context, monkeypatch)

    result = service.update_selected(context)

    assert calls == []
    assert result["skipped"] == [{"browser": "Brave", "reason": "no build"}]


def test_a_tick_narrows_the_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, guard_defender=False, browsers=["yandex"])
    _owner_build(context.paths.input / "Chrome from work")
    yandex = _foreign_build(context.paths.input / "Yandex old", "browser.exe")
    _says_by_folder(monkeypatch, {"Chrome from work": "Google Chrome"})
    calls = _prepare_update(context, monkeypatch)

    service.update_selected(context)

    assert [(call["id"], call["home"]) for call in calls] == [("yandex", yandex)]


def test_check_lists_each_build_of_the_same_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path)
    _owner_build(context.paths.input / "Chrome work")
    _owner_build(context.paths.input / "Chrome private")
    _says(monkeypatch, "Google Chrome")
    _prepare_update(context, monkeypatch)

    result = service.check_updates(context)

    assert [Path(row["path"]).name for row in result["browsers"]] == ["Chrome private", "Chrome work"]
    assert all(row["foreign_layout"] and row["update"] for row in result["browsers"])
    log = context.log_file.read_text(encoding="utf-8")
    assert "[Google Chrome (Chrome private)]" in log and "[Google Chrome (Chrome work)]" in log


def test_check_with_an_empty_source_shows_what_every_browser_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    _prepare_update(context, monkeypatch)

    result = service.check_updates(context)

    assert [row["browser"] for row in result["browsers"]] == [spec.name for spec in browser_registry.BROWSERS]
    assert all(row["published"] == "200.0.0.1" and row["path"] == "" for row in result["browsers"])


def test_the_source_folder_itself_is_not_renamed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pointing the Source straight at a build must not leave it pointing at nothing."""
    picked = _owner_build(tmp_path / "elsewhere" / "Chrome on the flash drive")
    context = _context(tmp_path, input_path=str(picked))
    spec = browser_registry.browser("chrome")
    _says(monkeypatch, "Google Chrome")

    layout = service._adoptable_build(context, spec)
    assert layout is not None and layout.home == picked
    home = service._adopt_build(context, spec, layout)

    assert home == picked
    assert (picked / "App" / "chrome.exe").is_file()
    assert _bookmarks(picked) == "keep me"
