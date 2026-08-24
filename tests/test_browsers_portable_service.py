from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import struct

import pytest

from system_core.core.jobs import JobContext
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import browser_registry, browsers_portable_service as service


UTF16_INI = (
    "; Chrome++ configuration\n"
    "[general]\n"
    "data_dir=%app%\\..\\Data\n"
    "command_line=\n"
    "launch_on_exit=\n"
)

MACHINE_BY_ARCH = {"x86": 0x014C, "x64": 0x8664, "arm64": 0xAA64}


def _fake_pe(path: Path, arch: str) -> Path:
    """The smallest file `pe_architecture` can read: a PE header and nothing else."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = bytearray(b"MZ" + b"\x00" * 0x3E)
    pe_offset = 0x40
    struct.pack_into("<I", blob, 0x3C, pe_offset)
    blob += b"PE\x00\x00" + struct.pack("<H", MACHINE_BY_ARCH[arch])
    path.write_bytes(bytes(blob))
    return path


def _context(tmp_path: Path, **parameters: object) -> JobContext:
    paths = get_project_paths(tmp_path)
    ensure_project_dirs(paths)
    return JobContext(
        paths=paths,
        operation=Operation(
            id="test",
            title="Test",
            description="",
            service="system_core.services.browsers_portable_service:build_selected",
            parameters=dict(parameters),
        ),
        log_file=paths.logs / "test.log",
        report_dir=paths.report,
    )


def _chrome_plus_tree(context: JobContext) -> None:
    """A pre-extracted Chrome++ archive, one App per architecture."""
    root = service._tmp_dir(context) / "chrome_plus"
    for arch in MACHINE_BY_ARCH:
        app = root / arch / "App"
        _fake_pe(app / "version.dll", arch)
        (app / "chrome++.ini").write_bytes(b"\xff\xfe" + UTF16_INI.encode("utf-16-le"))


def _build_with_browser(tmp_path: Path, spec: browser_registry.BrowserSpec, arch: str) -> Path:
    build = tmp_path / spec.folder
    _fake_pe(build / "App" / spec.executable, arch)
    return build


def test_pe_architecture_reads_the_header() -> None:
    assert service.pe_architecture(_fake_pe(Path.cwd() / "_x64.tmp", "x64")) == "x64"
    (Path.cwd() / "_x64.tmp").unlink()


def _thirty_two_bit_spec() -> browser_registry.BrowserSpec:
    """A 32-bit browser, the case that cost a silently non-portable build."""
    return browser_registry.BrowserSpec(
        id="probe",
        name="Probe Browser",
        folder="Probe Portable",
        executable="probe.exe",
        kind="archive",
        version_source="github_tag",
        why="test fixture",
        repo="example/probe",
        asset=r"probe\.zip",
    )


def test_a_32_bit_browser_gets_the_32_bit_wrapper(tmp_path: Path) -> None:
    """A 32-bit browser with the x64 wrapper starts fine and is not portable."""
    context = _context(tmp_path)
    _chrome_plus_tree(context)
    spec = _thirty_two_bit_spec()
    build = _build_with_browser(tmp_path, spec, "x86")

    arch = service._place_chrome_plus(context, spec, build, tmp_path / "unused.7z")

    assert arch == "x86"
    assert service.pe_architecture(build / "App" / "version.dll") == "x86"


def test_a_forced_mismatch_is_refused_instead_of_shipped(tmp_path: Path) -> None:
    context = _context(tmp_path, chrome_plus_arch="x64")
    _chrome_plus_tree(context)
    spec = _thirty_two_bit_spec()
    build = _build_with_browser(tmp_path, spec, "x86")

    with pytest.raises(RuntimeError, match="must match"):
        service._place_chrome_plus(context, spec, build, tmp_path / "unused.7z")


def test_a_64_bit_browser_keeps_the_64_bit_wrapper(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _chrome_plus_tree(context)
    spec = browser_registry.browser("brave")
    build = _build_with_browser(tmp_path, spec, "x64")

    assert service._place_chrome_plus(context, spec, build, tmp_path / "unused.7z") == "x64"


def test_update_replaces_app_where_the_build_already_lives(tmp_path: Path) -> None:
    """Update belongs in the picked folder: the Target is for new builds only."""
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    build = tmp_path / "on-a-flash-drive" / spec.folder
    (build / "App" / "old-version").mkdir(parents=True)
    (build / "Data").mkdir(parents=True)
    (build / "Data" / "profile-marker.txt").write_text("keep me", encoding="utf-8")
    staged = tmp_path / "staged-app"
    staged.mkdir()
    (staged / spec.executable).write_bytes(b"MZ")

    service._replace_app_in_place(context, spec, build, staged)

    assert (build / "App" / spec.executable).exists()
    assert not (build / "App" / "old-version").exists()
    assert (build / "Data" / "profile-marker.txt").read_text(encoding="utf-8") == "keep me"
    assert not list(build.glob("App.replaced-*"))


def test_a_locked_build_says_what_to_do(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files are held open while the browser runs; that must read as advice."""
    context = _context(tmp_path)
    spec = browser_registry.browser("brave")
    build = tmp_path / spec.folder
    (build / "App").mkdir(parents=True)
    staged = tmp_path / "staged-app"
    staged.mkdir()

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("used by another process")

    monkeypatch.setattr(Path, "rename", refuse)

    with pytest.raises(RuntimeError, match="Close the browser"):
        service._replace_app_in_place(context, spec, build, staged)


def test_configure_ini_keeps_the_utf16_file_readable(tmp_path: Path) -> None:
    """Chrome++ ships the ini as UTF-16 LE; rewriting it as UTF-8 kills it silently."""
    context = _context(tmp_path)
    _chrome_plus_tree(context)
    spec = browser_registry.browser("yandex")
    build = _build_with_browser(tmp_path, spec, "x64")
    service._place_chrome_plus(context, spec, build, tmp_path / "unused.7z")

    service._configure_chrome_plus_ini(context, spec, build, wipe_registry=True)

    raw = (build / "App" / "chrome++.ini").read_bytes()
    assert raw.startswith(b"\xff\xfe")
    text = raw[2:].decode("utf-16-le")
    assert f'launch_on_exit=reg delete "{spec.registry_branch}" /f;' in text
    assert "data_dir=%app%\\..\\Data" in text


def test_versions_are_compared_the_way_each_vendor_numbers_them() -> None:
    # Chrome++ tags 1.18.2 and ships a file reporting 1.18.2.0.
    assert service.same_version("1.18.2", "1.18.2.0")
    # Brave tags 1.93.134 and ships brave.exe calling itself 151.1.93.134.
    assert service.same_version("1.93.134", "151.1.93.134", "tail")
    assert not service.same_version("1.93.134", "151.1.93.134")
    assert not service.same_version("1.93.135", "151.1.93.134", "tail")
    # Ungoogled tags 151.0.7922.108-1.1 for a build reporting 151.0.7922.108.
    assert service.same_version("151.0.7922.108-1.1", "151.0.7922.108")
    assert not service.same_version("151.0.7922.108", "")


def test_every_registry_entry_is_complete() -> None:
    for spec in browser_registry.BROWSERS:
        assert spec.kind in {"installer", "archive"}, spec.id
        assert spec.version_source in {"github_tag", "chrome_api", "yandex_redirect"}, spec.id
        assert spec.executable.endswith(".exe"), spec.id
        assert spec.folder.endswith("Portable"), spec.id
        assert spec.why, spec.id
        if spec.version_source == "github_tag":
            assert spec.repo and spec.asset, spec.id
        else:
            assert spec.url, spec.id
        if spec.kind == "installer":
            assert spec.payload_archive, spec.id


def test_browsers_with_their_own_updating_portable_build_stay_out() -> None:
    """The rule that decides the roster, kept where it cannot be forgotten."""
    assert set(browser_registry.EXCLUDED) == {
        "vivaldi",
        "cent",
        "opera",
        "thorium",
        "comet",
        "atlas",
        "duckduckgo",
    }
    assert not set(browser_registry.BROWSERS_BY_ID) & set(browser_registry.EXCLUDED)


def test_checkbox_options_come_from_the_registry() -> None:
    options = service.browser_options()

    assert [item["value"] for item in options] == [spec.id for spec in browser_registry.BROWSERS]
    assert all(item["label"] for item in options)


def test_github_assets_fall_back_to_the_release_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API allows 60 anonymous calls an hour, and six browsers eat them fast."""
    page = '<a href="/deemru/chromium-gost/releases/download/150.0.7871.224/chromium-gost-150.0.7871.224-windows-amd64.zip"></a>'

    class _Response:
        def __init__(self, url: str, body: bytes) -> None:
            self._url = url
            self._body = body

        def geturl(self) -> str:
            return self._url

        def read(self) -> bytes:
            return self._body

    @contextmanager
    def fake_urlopen(request, timeout=0):  # noqa: ANN001 - mirrors urlopen's shape
        url = request.full_url
        if "api.github.com" in url:
            raise RuntimeError("rate limit exceeded")
        if url.endswith("/releases/latest"):
            yield _Response("https://github.com/deemru/chromium-gost/releases/tag/150.0.7871.224", b"")
            return
        yield _Response(url, page.encode("utf-8"))

    monkeypatch.setattr(service, "urlopen", fake_urlopen)

    version, url, name = service.published_source(browser_registry.browser("chromium_gost"))

    assert version == "150.0.7871.224"
    assert name == "chromium-gost-150.0.7871.224-windows-amd64.zip"
    assert url.startswith("https://github.com/deemru/chromium-gost/releases/download/")


def test_certificate_wrappers_install_for_the_user_and_remove_by_thumbprint(tmp_path: Path) -> None:
    target = tmp_path / "Certificates"
    target.mkdir()

    service._write_certificate_wrappers(_context(tmp_path), target)

    install = (target / "Install-Russian-Trusted-CA.cmd").read_bytes()
    remove = (target / "Uninstall-Russian-Trusted-CA.cmd").read_bytes()
    assert b"-addstore -user" in install
    assert b"-addstore -enterprise" not in install
    assert not install.startswith(b"\xef\xbb\xbf")
    assert install.count(b"\n") == install.count(b"\r\n")
    for item in service.RUSSIAN_TRUSTED_CERTIFICATES:
        assert str(item["thumbprint"]).encode() in remove


def test_unknown_browser_names_what_is_known() -> None:
    with pytest.raises(RuntimeError, match="chromium_gost"):
        browser_registry.browser("vivaldi")


def test_read_guard_result_parses_status_and_detail(tmp_path: Path) -> None:
    """Guard status lines are STATUS then a tab then detail; the reader renders them."""
    result = tmp_path / "guard.result"
    result.write_text("ADDED\tE:/out", encoding="utf-8")
    assert service._read_guard_result(result) == "ADDED: E:/out"

    result.write_text("REMOVED", encoding="utf-8")
    assert service._read_guard_result(result) == "REMOVED"

    assert service._read_guard_result(tmp_path / "missing") == ""


def test_guard_path_argument_joins_with_a_pipe(tmp_path: Path) -> None:
    """Folders travel through ShellExecute as one '|'-joined argument; spaces stay."""
    joined = service._guard_path_argument([Path(r"E:\out"), Path(r"D:\build x\App")])
    assert joined == r"E:\out|D:\build x\App"
    assert joined.split("|") == [r"E:\out", r"D:\build x\App"]


def test_defender_guard_is_a_noop_when_defender_is_inactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No Defender means no elevation: the guard must never reach for UAC."""
    context = _context(tmp_path)
    monkeypatch.setattr(service, "_defender_active", lambda _ctx: False)

    def fail_if_elevated(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("_defender_guard tried to elevate while Defender was inactive")

    monkeypatch.setattr(service, "_run_elevated_powershell", fail_if_elevated)

    entered = False
    with service._defender_guard(context, context.paths.output):
        entered = True
    assert entered
    assert not (context.paths.workspace / service._DEFENDER_GUARD_DIRNAME).exists()


def test_defender_guard_survives_a_declined_uac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A declined UAC (ShellExecute code 5) must not abort the build; it proceeds unguarded."""
    context = _context(tmp_path)
    monkeypatch.setattr(service, "_defender_active", lambda _ctx: True)
    monkeypatch.setattr(service, "_run_elevated_powershell", lambda _script, _args: 5)

    entered = False
    with service._defender_guard(context, [context.paths.output, context.paths.input]):
        entered = True
    assert entered

    leftovers = list((context.paths.workspace / service._DEFENDER_GUARD_DIRNAME).glob("*"))
    assert leftovers == []


PROXY_RELEASE_LIST = """
<a href="/project/neyrostalker/proksi-biblioteka/release/4310a30c-b4d1-4d6a-9e68-c80f0ad6d70b">Версия 1.0.7.4</a>
<a href="/project/neyrostalker/proksi-biblioteka/release/b7cf84bf-ab86-49a2-a9de-6ae518544a9b">Версия 1.0.6.8</a>
"""

PROXY_RELEASE_PAGE = """
<a href="/project/neyrostalker/proksi-biblioteka/release/4310a30c-b4d1-4d6a-9e68-c80f0ad6d70b/0659ba34-21de-4673-8fb3-a5cb3f185f20/download">Прокси библиотека.zip</a>
"""


def _proxy_tree(context: JobContext) -> None:
    """A pre-extracted proxy library archive: one dll per architecture."""
    root = service._tmp_dir(context) / "proxy_library" / "Bin"
    _fake_pe(root / "version x32.dll", "x86")
    _fake_pe(root / "version x64.dll", "x64")


def test_proxy_library_release_reads_the_gitflic_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitFlic's API needs a token, so the public pages carry the answer."""
    pages = {
        "/project/neyrostalker/proksi-biblioteka/release?sort=TIME&direction=DESC": PROXY_RELEASE_LIST,
        "/project/neyrostalker/proksi-biblioteka/release/4310a30c-b4d1-4d6a-9e68-c80f0ad6d70b": PROXY_RELEASE_PAGE,
    }
    monkeypatch.setattr(service, "_gitflic_page", lambda path: pages[path])

    version, url = service.proxy_library_release()

    assert version == "1.0.7.4"
    assert url.endswith("/0659ba34-21de-4673-8fb3-a5cb3f185f20/download")


def test_proxy_library_falls_back_to_the_tag_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A release without an attachment still has the repository archive."""
    pages = {
        "/project/neyrostalker/proksi-biblioteka/release?sort=TIME&direction=DESC": PROXY_RELEASE_LIST,
        "/project/neyrostalker/proksi-biblioteka/release/4310a30c-b4d1-4d6a-9e68-c80f0ad6d70b": "<p>no files</p>",
    }
    monkeypatch.setattr(service, "_gitflic_page", lambda path: pages[path])

    version, url = service.proxy_library_release()

    assert version == "1.0.7.4"
    assert url.endswith("downloadAll?branch=1.0.7.4&format=zip")


def test_the_proxy_library_matches_the_browser_architecture(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _proxy_tree(context)
    spec = _thirty_two_bit_spec()
    build = _build_with_browser(tmp_path, spec, "x86")

    arch = service._place_proxy_library(context, spec, build, tmp_path / "unused.zip", block_registry=True)

    assert arch == "x86"
    assert service.pe_architecture(build / "App" / "version.dll") == "x86"
    assert "REGOFF=1" in (build / "App" / "version.ini").read_text(encoding="ascii")


def test_the_proxy_library_refuses_arm64_instead_of_shipping_it(tmp_path: Path) -> None:
    """The library is x86/x64 only; Chrome++ is the one that covers ARM64."""
    context = _context(tmp_path)
    _proxy_tree(context)
    spec = browser_registry.browser("brave")
    build = _build_with_browser(tmp_path, spec, "arm64")

    with pytest.raises(RuntimeError, match="x86 and x64 only"):
        service._place_proxy_library(context, spec, build, tmp_path / "unused.zip", block_registry=False)


def test_registry_writes_stay_allowed_when_the_box_is_clear(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _proxy_tree(context)
    spec = browser_registry.browser("brave")
    build = _build_with_browser(tmp_path, spec, "x64")

    service._place_proxy_library(context, spec, build, tmp_path / "unused.zip", block_registry=False)

    assert "REGOFF=0" in (build / "App" / "version.ini").read_text(encoding="ascii")


def test_the_registry_wipe_stands_down_when_the_browser_is_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The branch belongs to the browser: wiping it would rob an installed copy."""
    context = _context(tmp_path)
    _chrome_plus_tree(context)
    spec = browser_registry.browser("brave")
    build = _build_with_browser(tmp_path, spec, "x64")
    monkeypatch.setattr(
        service,
        "installed_browser_path",
        lambda _spec: r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    )
    service._place_chrome_plus(context, spec, build, tmp_path / "unused.7z")

    service._configure_chrome_plus_ini(context, spec, build, wipe_registry=True)

    text, _newline, _bom = service._read_ini(build / "App" / "chrome++.ini")
    assert "launch_on_exit=\n" in text or text.rstrip().endswith("launch_on_exit=")
