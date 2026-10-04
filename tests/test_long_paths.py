"""A Target that is not a short one must not break the build.

Windows stops ordinary paths at 260 characters, and a browser installer nests
its payload deep enough to cross that as soon as the Target is long. 7-Zip
writes such files regardless; what has to hold is everything done to them after.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
import os
import struct
import zipfile

import pytest

from system_core.core.jobs import JobContext
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import browser_registry
from system_core.services import browsers_portable_service as service
from system_core.services import portable_libraries as libraries


pytestmark = pytest.mark.skipif(os.name != "nt", reason="the 260-character limit is a Windows matter")

PROJECT_7ZIP = Path(__file__).resolve().parent.parent / "tools" / "7zip" / "bin" / "7za.exe"


def _context(root: Path, **parameters: object) -> JobContext:
    paths = get_project_paths(root)
    ensure_project_dirs(paths)
    return JobContext(
        paths=paths,
        operation=Operation(id="test", title="Test", description="", service="test:test", parameters=dict(parameters)),
        log_file=paths.logs / "test.log",
        report_dir=paths.report,
    )


def _log(context: JobContext) -> str:
    return context.log_file.read_text(encoding="utf-8") if context.log_file.exists() else ""


def _write(path: Path, data: bytes = b"x") -> Path:
    """Write a file wherever it lies; the tests themselves must not trip on the limit."""
    target = service._long(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return path


@pytest.fixture
def deep(tmp_path: Path) -> Iterator[Path]:
    """A folder whose own path is already past the limit."""
    top = tmp_path / ("a" * 60)
    folder = top / ("b" * 60) / ("c" * 60) / ("d" * 60)
    service._long(folder).mkdir(parents=True)
    assert len(str(folder)) > service.MAX_PLAIN_PATH
    yield folder
    # pytest clears tmp_path the ordinary way, which cannot reach this far.
    service._remove_tree(top)


@pytest.fixture
def roomy(tmp_path: Path) -> Iterator[Path]:
    """A project folder that is itself within the limit and leaves little room under it."""
    root = tmp_path / ("r" * max(1, 180 - len(str(tmp_path)) - 1))
    root.mkdir()
    assert len(str(root)) < service.MAX_PLAIN_PATH
    yield root
    service._remove_tree(root)


# --- the two spellings ----------------------------------------------------------


def test_the_long_form_and_the_plain_one_name_the_same_path() -> None:
    plain = Path(r"C:\Builds\Chrome")

    assert str(service._long(plain)) == "\\\\?\\C:\\Builds\\Chrome"
    assert service._plain(service._long(plain)) == plain
    assert service._long(service._long(plain)) == service._long(plain)
    assert service._plain(plain) == plain


def test_a_network_share_gets_the_long_form_of_a_share() -> None:
    share = Path(r"\\server\share\Chrome")

    assert str(service._long(share)) == "\\\\?\\UNC\\server\\share\\Chrome"
    assert service._plain(service._long(share)) == share


def test_a_relative_path_is_made_absolute_first() -> None:
    assert str(service._long(Path("output"))) == "\\\\?\\" + os.path.abspath("output")


def test_the_work_folder_is_handed_out_in_the_long_form(tmp_path: Path) -> None:
    context = _context(tmp_path)

    work = service._tmp_dir(context)

    assert str(work).startswith("\\\\?\\") and work.is_dir()
    assert service._plain(work) == service._portable_root(context) / "_tmp"


# --- working past the limit -----------------------------------------------------


def test_a_tree_past_the_limit_is_copied_and_removed(deep: Path) -> None:
    _write(deep / "payload" / "chrome.exe", b"browser")
    _write(deep / "payload" / "154.0.8037.98" / "Locales" / "ru.pak", b"locale")

    service._copy_tree_contents(deep / "payload", deep / "App")

    assert service._long(deep / "App" / "chrome.exe").read_bytes() == b"browser"
    assert service._long(deep / "App" / "154.0.8037.98" / "Locales" / "ru.pak").read_bytes() == b"locale"

    service._remove_tree(deep / "App")

    assert not service._long(deep / "App").exists()
    assert service._long(deep / "payload" / "chrome.exe").is_file()


def test_a_folder_past_the_limit_is_emptied_and_made_again(deep: Path) -> None:
    _write(deep / "unpacked" / "old.bin")

    service._reset_dir(deep / "unpacked")

    assert service._long(deep / "unpacked").is_dir()
    assert not service._long(deep / "unpacked" / "old.bin").exists()


def test_removing_what_is_not_there_is_not_an_error(deep: Path) -> None:
    service._remove_tree(deep / "never made")


@pytest.mark.skipif(not PROJECT_7ZIP.is_file(), reason="the project's portable 7-Zip is not installed")
def test_what_7zip_unpacks_past_the_limit_is_found_afterwards(
    deep: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this started with: `Chrome.7z was not found` in a file 7-Zip had just written."""
    context = _context(tmp_path)
    monkeypatch.setattr(service, "_seven_zip_path", lambda _context: PROJECT_7ZIP)
    archive = tmp_path / "installer.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("bin/Offline/{8A69D345-D564-463C-AFF1-A69D9E530F96}/Chrome.7z", b"payload")

    service._extract_archive(context, archive, deep / "chrome_installer")

    found = service._find_file(service._long(deep / "chrome_installer"), "Chrome.7z")
    assert found is not None and found.read_bytes() == b"payload"
    assert len(str(service._plain(found))) > service.MAX_PLAIN_PATH


def _zip_bytes(folder: Path, name: str, members: dict[str, bytes]) -> bytes:
    archive = folder / name
    with zipfile.ZipFile(archive, "w") as zipped:
        for member, data in members.items():
            zipped.writestr(member, data)
    return archive.read_bytes()


@pytest.mark.skipif(not PROJECT_7ZIP.is_file(), reason="the project's portable 7-Zip is not installed")
def test_a_nested_installer_is_unpacked_under_a_long_target(
    roomy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chrome's own nesting, level for level, under a Target that leaves no room for it."""
    context = _context(roomy)
    monkeypatch.setattr(service, "_seven_zip_path", lambda _context: PROJECT_7ZIP)
    spec = browser_registry.browser("chrome")
    guid = "{8A69D345-D564-463C-AFF1-A69D9E530F96}"
    payload = _zip_bytes(tmp_path, "payload.zip", {
        "Chrome-bin/chrome.exe": b"browser",
        "Chrome-bin/154.0.8037.98/Locales/ru.pak": b"locale",
    })
    inner = _zip_bytes(tmp_path, "inner.zip", {"Chrome.7z": payload})
    installer = tmp_path / "ChromeStandaloneSetup64.exe"
    installer.write_bytes(_zip_bytes(tmp_path, "outer.zip", {
        f"bin/Offline/{guid}/{guid}/154.0.8037.98_chrome_installer.exe": inner,
    }))
    nested = service._portable_root(context) / "_tmp" / "chrome_installer" / "bin" / "Offline" / guid / guid
    assert len(str(nested / "154.0.8037.98_chrome_installer.exe")) > service.MAX_PLAIN_PATH

    browser = service._unpack_browser(context, spec, installer)

    assert browser.name == "Chrome-bin"
    assert (browser / "chrome.exe").read_bytes() == b"browser"
    assert (browser / "154.0.8037.98" / "Locales" / "ru.pak").read_bytes() == b"locale"


def test_a_build_is_published_and_zipped_from_a_deep_work_folder(roomy: Path) -> None:
    context = _context(roomy)
    name = "Google Chrome Portable"
    work = service._tmp_dir(context) / name
    long_name = "resource-" + "n" * 70 + ".pak"
    _write(work / "App" / "chrome.exe", b"browser")
    _write(work / "App" / "154.0.8037.98" / "Locales" / long_name, b"locale")
    assert len(str(service._plain(work / "App" / "154.0.8037.98" / "Locales" / long_name))) > service.MAX_PLAIN_PATH

    published = service._publish(context, work, name)
    zipped = service._zip_dir(context, work, service._portable_root(context) / f"{name}.zip")

    assert published == service._portable_root(context) / name
    assert service._long(published / "App" / "154.0.8037.98" / "Locales" / long_name).read_bytes() == b"locale"
    with zipfile.ZipFile(zipped) as archive:
        assert f"{name}/App/154.0.8037.98/Locales/{long_name}" in archive.namelist()
    assert "\\\\?\\" not in _log(context)


def _pe(path: Path) -> Path:
    """The smallest file the architecture check reads: a 64-bit PE header and nothing else."""
    blob = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", blob, 60, 64)
    blob += b"PE\0\0" + struct.pack("<H", 0x8664)
    return _write(path, bytes(blob))


def test_a_build_lying_past_the_limit_is_updated_in_place(
    deep: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its launcher and its note sit past the limit as well, not only the browser."""
    context = _context(tmp_path, guard_defender=False)
    spec = browser_registry.browser("chrome")
    build = deep / spec.folder
    _write(build / "App" / spec.executable, b"old browser")
    _write(build / "Data" / "Local State", b"{}")
    payload = context.paths.workspace / "payload"
    new_browser = _pe(payload / spec.executable).read_bytes()
    installer = _write(context.paths.workspace / "setup.exe", b"fixture")
    wrapper = context.paths.workspace / "proxy-library.zip"
    with zipfile.ZipFile(wrapper, "w") as zipped:
        zipped.write(_pe(context.paths.workspace / "version.dll"), libraries.PROXY_DLLS["x64"])
    monkeypatch.setattr(
        service, "_download",
        lambda _context, url, _target, _label, **_options: service.DownloadedAsset("fixture", url, installer, "", 7),
    )
    monkeypatch.setattr(service, "_browser_source", lambda _context, _spec: ("154.0.8037.98", "https://example.test/s.exe", "s.exe"))
    monkeypatch.setattr(service, "_unpack_browser", lambda *_arguments: payload)
    monkeypatch.setattr(service, "_file_version", lambda _path: "")

    result = service._build_one(
        context, spec, engine="proxy_library", plus_archive=wrapper, plus_version="2.0",
        wipe_registry=False, with_certificates=False, package_archive=False, keep_data_from=build,
    )

    assert result["artifact"] == str(build) and result["deep_paths"] > 0
    assert service._long(build / "App" / spec.executable).read_bytes() == new_browser
    assert service._long(build / "Data" / "Local State").read_bytes() == b"{}"
    assert service._long(build / f"{spec.folder}.cmd").is_file()
    assert service._build_stamp(build)["mode"] == "update"
    assert "\\\\?\\" not in _log(context)


def test_versions_are_read_from_a_build_past_the_limit(deep: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = browser_registry.browser("chrome")
    build = deep / spec.folder
    _write(build / "App" / spec.executable)
    reachable: list[bool] = []
    monkeypatch.setattr(service, "_file_version", lambda path: reachable.append(path.is_file()) or "154.0.8037.98")

    assert service.build_versions(spec, build)[0] == "154.0.8037.98"
    assert reachable[0] is True


# --- what is said about it ------------------------------------------------------


def test_a_leftover_in_the_work_folder_does_not_fail_the_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)

    def in_use(_path: Path) -> None:
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(service, "_remove_tree", in_use)

    service._discard_tmp(context)

    log = _log(context)
    assert "[WARN] The work folder could not be cleared" in log
    assert str(service._portable_root(context) / "_tmp") in log and "\\\\?\\" not in log


def test_a_browser_past_the_limit_is_reported_with_its_count(tmp_path: Path) -> None:
    context = _context(tmp_path)
    build = tmp_path / "Google Chrome Portable"
    app = build / "App"
    room = service.MAX_PLAIN_PATH - len(str(app)) - 1
    _write(app / "chrome.exe")
    _write(app / ("e" * room))
    _write(app / ("f" * (room + 1)))
    assert len(str(app / ("e" * room))) == service.MAX_PLAIN_PATH
    try:
        deep = service._warn_deep_build(context, "Google Chrome", build)
    finally:
        service._remove_tree(app)

    log = _log(context)
    assert deep == 1
    assert "[WARN] Google Chrome: 1 of the 3 files of the browser" in log
    assert f"the longest path is {service.MAX_PLAIN_PATH + 1}" in log
    assert str(build) in log and "\\\\?\\" not in log


def test_a_browser_within_the_limit_is_not_mentioned(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _write(tmp_path / "Brave Portable" / "App" / "brave.exe")

    assert service._warn_deep_build(context, "Brave", tmp_path / "Brave Portable") == 0
    assert "[WARN]" not in _log(context)
