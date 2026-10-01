"""Regressions for the operations migrated from the single-browser tools."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import ctypes
import json
import os
import struct
import subprocess
import zipfile

import pytest

from system_core.core.jobs import JobContext, hidden_subprocess_kwargs
from system_core.core.manifest import Operation, load_manifest
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import browser_registry as registry
from system_core.services import browsers_portable_service as service
from system_core.services import portable_libraries as libraries


def context(root: Path, **parameters) -> JobContext:
    paths = get_project_paths(root)
    ensure_project_dirs(paths)
    return JobContext(paths, Operation("test", "Test", "", "test:test", parameters=parameters),
                      paths.logs / "test.log", paths.report)


def pe(path: Path, arch: str = "x64", tail: bytes = b"") -> Path:
    blob = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", blob, 60, 64)
    blob += b"PE\0\0" + struct.pack("<H", {"x86": 0x14C, "x64": 0x8664, "arm64": 0xAA64}[arch])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob + tail)
    return path


INI_NAME = {"proxy_library": "version.ini", "chrome_plus": "chrome++.ini", "vivaldi_plus": "config.ini"}


def existing(ctx: JobContext, browser_id="chrome", arch="x64", engine="proxy_library") -> Path:
    spec = registry.browser(browser_id)
    build = ctx.paths.input / spec.folder
    pe(build / "App" / spec.executable, arch, b"browser-original")
    pe(build / "App" / "version.dll", arch, b"dll-original")
    (build / "App" / "resources.pak").write_bytes(b"browser-resource")
    (build / "App" / "portable-library.json").write_text(json.dumps({"engine": engine}))
    (build / "App" / INI_NAME[engine]).write_bytes(b"user INI settings")
    for name in ("Data", "Cache"):
        (build / name).mkdir()
        (build / name / "keep.bin").write_bytes(b"user data")
    (build / f"{spec.folder}.cmd").write_bytes(b"@echo off\r\nREM user launcher\r\n")
    (build / service.BUILD_STAMP_FILE).write_text(json.dumps({
        "source_url": "https://example.test/old.exe", "certificates_staged": True,
    }))
    return build


def archive(root: Path, engine: str, arch="x64") -> Path:
    dll = pe(root / f"new-{arch}.dll", arch, b"dll-new")
    result = root / f"{engine}-{arch}.zip"
    with zipfile.ZipFile(result, "w") as zipped:
        name = libraries.PROXY_DLLS[arch] if engine == "proxy_library" else "version.dll"
        zipped.write(dll, name)
        if engine == "chrome_plus":
            zipped.writestr("chrome++.ini", b"\xff\xfe" + "[general]\nlaunch_on_exit=\n".encode("utf-16-le"))
    return result


def snapshot(build: Path, exclude=()) -> dict:
    return {str(p.relative_to(build)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in build.rglob("*") if p.is_file() and str(p.relative_to(build)) not in exclude}


def wrapper_fixture(ctx, monkeypatch, engine, arch="x64"):
    source = archive(ctx.paths.workspace, engine, arch)
    calls = []
    monkeypatch.setattr(service, "_download_wrapper", lambda _c, _e, _a: (calls.append((_e, _a)) or source, "2.0.0"))
    monkeypatch.setattr(service, "_file_version", lambda p: "2.0.0" if p.name == "version.dll" else "123.0.1")
    def no_browser(*_args, **_kwargs):
        raise AssertionError("DLL-only update must not resolve or download a browser")
    monkeypatch.setattr(service, "published_source", no_browser)
    monkeypatch.setattr(service, "_download", no_browser)
    monkeypatch.setattr(service, "_require_7zip", no_browser)
    return calls


@pytest.mark.parametrize("engine", list(INI_NAME))
@pytest.mark.parametrize("arch", ["x86", "x64"])
def test_dll_update_preserves_browser_profile_ini_and_launcher(tmp_path, monkeypatch, engine, arch):
    ctx = context(tmp_path, browsers=["chrome"], portable_engine=engine, guard_defender=False)
    build = existing(ctx, arch=arch, engine=engine)
    changed = {"App\\version.dll", "App\\portable-library.json", service.BUILD_STAMP_FILE}
    before = snapshot(build, changed)
    calls = wrapper_fixture(ctx, monkeypatch, engine, arch)
    result = service.update_libraries_selected(ctx)
    assert snapshot(build, changed) == before
    assert (build / "App" / "version.dll").read_bytes().endswith(b"dll-new")
    assert calls == [(engine, arch)]
    assert result["updated"][0]["version"] == "123.0.1"
    assert json.loads((build / service.BUILD_STAMP_FILE).read_text())["source_url"].endswith("old.exe")
    assert not list(build.glob(".audion-wrapper-update-*"))
    assert not (ctx.paths.output / "Portable" / "_tmp").exists()


@pytest.mark.parametrize("old,new", [(a, b) for a in INI_NAME for b in INI_NAME if a != b])
def test_switch_library_removes_previous_owned_ini_and_creates_defaults(tmp_path, monkeypatch, old, new):
    ctx = context(tmp_path, browsers=["chrome"], portable_engine=new, guard_defender=False)
    build = existing(ctx, engine=old)
    before = snapshot(build, {"App\\version.dll", "App\\portable-library.json", f"App\\{INI_NAME[old]}",
                              f"Google Chrome Portable.cmd", service.BUILD_STAMP_FILE})
    wrapper_fixture(ctx, monkeypatch, new)
    service.update_libraries_selected(ctx)
    assert not (build / "App" / INI_NAME[old]).exists()
    assert (build / "App" / INI_NAME[new]).is_file()
    assert all(snapshot(build)[name] == value for name, value in before.items())
    assert libraries.installed_engine(build) == new
    if new == "proxy_library":
        assert (build / "App" / "version.ini").read_bytes() == libraries.PROXY_INI.replace("\n", "\r\n").encode("ascii")


def test_dll_update_refuses_forced_architecture_before_download(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["chrome"], chrome_plus_arch="x86", guard_defender=False)
    build = existing(ctx)
    before = snapshot(build)
    calls = wrapper_fixture(ctx, monkeypatch, "proxy_library")
    with pytest.raises(RuntimeError, match="must match"):
        service.update_libraries_selected(ctx)
    assert calls == []
    assert snapshot(build) == before


@pytest.mark.parametrize("locked_name", ["version.dll", service.BUILD_STAMP_FILE])
def test_partial_dll_update_rolls_back_all_committed_files(tmp_path, monkeypatch, locked_name):
    ctx = context(tmp_path, browsers=["chrome"], guard_defender=False)
    build = existing(ctx)
    before = snapshot(build)
    wrapper_fixture(ctx, monkeypatch, "proxy_library")
    original_replace = Path.replace
    def locked_replace(source, target):
        if source.name.endswith(".new") and Path(target).name == locked_name:
            raise PermissionError("file is in use")
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", locked_replace)
    with pytest.raises(RuntimeError, match="rolled back"):
        service.update_libraries_selected(ctx)
    assert snapshot(build) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing")
def test_real_windows_dll_file_lock_leaves_the_build_intact(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["chrome"], guard_defender=False)
    build = existing(ctx)
    before = snapshot(build)
    wrapper_fixture(ctx, monkeypatch, "proxy_library")
    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                      ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    handle = create(str(build / "App" / "version.dll"), 0x80000000, 1, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(RuntimeError, match="rolled back"):
            service.update_libraries_selected(ctx)
    finally:
        ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.CloseHandle(handle)
    assert snapshot(build) == before


def test_a_failed_browser_does_not_stop_the_dll_batch(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["yandex", "chrome"], guard_defender=False)
    bad = existing(ctx, "yandex", "arm64")
    good = existing(ctx)
    before = snapshot(bad)
    wrapper_fixture(ctx, monkeypatch, "proxy_library")
    result = service.update_libraries_selected(ctx)
    assert len(result["failed"]) == 1 and result["updated"][0]["id"] == "chrome"
    assert snapshot(bad) == before
    assert (good / "App" / "version.dll").read_bytes().endswith(b"dll-new")


def test_custom_chrome_does_not_use_the_official_version_api(tmp_path, monkeypatch):
    ctx = context(tmp_path, chrome_download_url="https://example.test/Chrome32.exe")
    monkeypatch.setattr(service, "_chrome_api_version", lambda: pytest.fail("wrong version source"))
    version, url, filename = service._browser_source(ctx, registry.browser("chrome"))
    assert version == "" and url == ctx.operation.parameters["chrome_download_url"]
    assert "custom" in filename


def test_custom_yandex_follows_redirects_with_windows_user_agent(tmp_path, monkeypatch):
    ctx = context(tmp_path, yandex_download_url="https://example.test/full.exe")
    spec = registry.browser("yandex")
    seen = []
    monkeypatch.setattr(service, "_yandex_redirect", lambda url, agent, **_kwargs:
                        (seen.append((url, agent)) or "26.10.1", "https://cdn.example.test/26.10.1.exe"))
    assert service._browser_source(ctx, spec)[:2] == ("26.10.1", "https://cdn.example.test/26.10.1.exe")
    assert seen == [("https://example.test/full.exe", spec.user_agent)]


def test_custom_yandex_accepts_a_renamed_full_installer(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def geturl(self):
            return "https://mirror.example.test/browser-copy.exe"
    requests = []
    monkeypatch.setattr(service, "urlopen", lambda request, **_k: (requests.append(request) or Response()))
    ctx = context(tmp_path, yandex_download_url="https://mirror.example.test/latest")
    version, resolved, _filename = service._browser_source(ctx, registry.browser("yandex"))
    assert version == "" and resolved.endswith("browser-copy.exe")
    assert requests[0].get_method() == "HEAD"
    assert requests[0].get_header("User-agent") == registry.WINDOWS_USER_AGENT
    with pytest.raises(RuntimeError, match="Windows installer"):
        service._yandex_redirect("https://browser.yandex.ru/download?full=1", registry.WINDOWS_USER_AGENT)


@pytest.mark.parametrize("url", ["file:///C:/setup.exe", "https:///missing-host", "not a URL"])
def test_invalid_installer_url_is_refused(tmp_path, url):
    with pytest.raises(RuntimeError, match="HTTP or HTTPS"):
        service._browser_source(context(tmp_path, chrome_download_url=url), registry.browser("chrome"))


def test_a_custom_installer_is_fetched_even_when_the_cached_name_exists(tmp_path, monkeypatch):
    class Response(BytesIO):
        headers = {"Content-Length": "9"}
    ctx = context(tmp_path)
    target = ctx.paths.workspace / "custom.exe"
    target.write_bytes(b"old-copy")
    monkeypatch.setattr(service, "urlopen", lambda *_a, **_k: Response(b"new-copy!"))
    result = service._download(ctx, "https://example.test/setup.exe", target, "custom", use_cache=False)
    assert result.path.read_bytes() == b"new-copy!"
    assert not target.with_name(target.name + ".part").exists()


@pytest.mark.parametrize("disable", [True, False])
@pytest.mark.parametrize("update", [True, False])
def test_yandex_updater_option_reaches_real_build_and_update(tmp_path, monkeypatch, disable, update):
    ctx = context(tmp_path, disable_yandex_updater=disable, guard_defender=False,
                  yandex_download_url="https://example.test/full.exe")
    spec = registry.browser("yandex")
    old = existing(ctx, "yandex") if update else None
    before = snapshot(old, {str(p.relative_to(old)) for p in (old / "App").rglob("*") if p.is_file()} |
                      {spec.folder + ".cmd", service.BUILD_STAMP_FILE}) if old else {}
    payload = ctx.paths.workspace / "payload"
    pe(payload / spec.executable, tail=b"new-browser")
    updater = payload / "26.10" / "service_update.exe"
    updater.parent.mkdir()
    updater.write_bytes(b"updater")
    (payload / "browser.dll").write_bytes(b"browser")
    installer = ctx.paths.workspace / "setup.exe"
    installer.write_bytes(b"fixture")
    seen_cache = []
    def download(_ctx, url, target, _label, **kwargs):
        seen_cache.append(kwargs["use_cache"])
        return service.DownloadedAsset("fixture", url, installer, "", installer.stat().st_size)
    monkeypatch.setattr(service, "_download", download)
    monkeypatch.setattr(service, "_yandex_redirect", lambda *_a, **_k: ("26.10", "https://example.test/full.exe"))
    monkeypatch.setattr(service, "_unpack_browser", lambda *_a: payload)
    monkeypatch.setattr(service, "_file_version", lambda _p: "")
    result = service._build_one(ctx, spec, engine="proxy_library", plus_archive=archive(ctx.paths.workspace, "proxy_library"),
                                plus_version="2.0", wipe_registry=False, with_certificates=False,
                                package_archive=False, keep_data_from=old)
    build = Path(result["artifact"])
    assert (build / "App" / "26.10" / "service_update.exe").exists() is (not disable)
    assert (build / "App" / "browser.dll").read_bytes() == b"browser"
    assert bool(result["removed_updaters"]) is disable
    assert seen_cache == [False]
    if old:
        assert all(snapshot(old)[name] == value for name, value in before.items())


def test_yandex_updater_is_disabled_even_when_browser_and_library_are_current(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["yandex"], portable_engine="chrome_plus", guard_defender=False)
    build = existing(ctx, "yandex", engine="chrome_plus")
    updater = build / "App" / "service_update.exe"
    updater.write_bytes(b"updater")
    monkeypatch.setattr(service, "_require_7zip", lambda _c: None)
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_a: "2.0")
    monkeypatch.setattr(service, "published_source", lambda _s: ("123.0", "", ""))
    monkeypatch.setattr(service, "build_versions", lambda *_a: ("123.0", "2.0"))
    result = service.update_selected(ctx)
    assert not updater.exists()
    assert result["updated"] == [] and result["skipped"][0]["removed_updaters"]


def test_updater_removal_does_not_touch_other_browsers_or_profile_files(tmp_path):
    ctx = context(tmp_path)
    chrome = existing(ctx)
    yandex = existing(ctx, "yandex")
    for build in (chrome, yandex):
        (build / "App" / "SERVICE_UPDATE.EXE").write_bytes(b"updater")
        (build / "Data" / "service_update.exe").write_bytes(b"user file")
    assert service._disable_yandex_updater(ctx, registry.browser("chrome"), chrome) == []
    assert service._disable_yandex_updater(ctx, registry.browser("yandex"), yandex)
    assert (chrome / "App" / "SERVICE_UPDATE.EXE").exists()
    assert (yandex / "Data" / "service_update.exe").exists()


def test_custom_yandex_is_not_skipped_when_the_redirect_version_is_current(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["yandex"], portable_engine="chrome_plus", guard_defender=False,
                  yandex_download_url="https://example.test/full.exe")
    existing(ctx, "yandex", engine="chrome_plus")
    monkeypatch.setattr(service, "_require_7zip", lambda _c: None)
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_a: "2.0")
    monkeypatch.setattr(service, "_yandex_redirect", lambda *_a, **_k: ("123.0", "https://example.test/full.exe"))
    monkeypatch.setattr(service, "build_versions", lambda *_a: ("123.0", "2.0"))
    seen = []
    monkeypatch.setattr(service, "_build_one", lambda _c, spec, **kwargs:
                        (seen.append(spec.id) or {"id": spec.id}))
    result = service.update_selected(ctx)
    assert seen == ["yandex"] and result["skipped"] == []


def test_custom_chrome_check_reports_unknown_instead_of_up_to_date(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["chrome"], guard_defender=False,
                  chrome_download_url="https://example.test/full.exe")
    existing(ctx)
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_a: "2.0")
    monkeypatch.setattr(service, "build_versions", lambda *_a: ("123.0", "2.0"))
    result = service.check_updates(ctx)
    assert result["browsers"][0]["published"] == "" and result["browsers"][0]["update"] is None
    assert "version unknown" in ctx.log_file.read_text(encoding="utf-8")


def test_invalid_custom_update_fails_without_changing_the_build(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["chrome"], guard_defender=False, chrome_download_url="file:///setup.exe")
    build = existing(ctx)
    before = snapshot(build)
    monkeypatch.setattr(service, "_require_7zip", lambda _c: None)
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_a: "2.0")
    monkeypatch.setattr(service, "_file_version", lambda _p: "123.0")
    with pytest.raises(RuntimeError, match="HTTP or HTTPS"):
        service.update_selected(ctx)
    assert snapshot(build) == before


def test_certificate_staging_adds_files_without_changing_windows_or_the_build(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["brave"], certificate_browsers=["chrome"])
    build = existing(ctx)
    before = snapshot(build)
    def download(_ctx, url, target, label):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"certificate fixture")
        return service.DownloadedAsset(label, url, target, "", target.stat().st_size)
    monkeypatch.setattr(service, "_download", download)
    monkeypatch.setattr(service.subprocess, "run", lambda *_a, **_k: pytest.fail("certificate staging must not run certutil"))
    result = service.stage_certificates_selected(ctx)
    assert result["staged"][0]["build"] == str(build)
    assert all(snapshot(build)[name] == value for name, value in before.items())
    certificates = list((build / "Certificates").iterdir())
    assert len(certificates) == 4
    for command in (p for p in certificates if p.suffix == ".cmd"):
        raw = command.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\n" not in raw.replace(b"\r\n", b"")


@pytest.mark.parametrize("operation", [service.update_libraries_selected, service.stage_certificates_selected])
def test_missing_build_is_reported_as_a_failure(tmp_path, operation):
    with pytest.raises(RuntimeError, match="No existing builds"):
        operation(context(tmp_path, browsers=["chrome"], guard_defender=False))


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
def test_certificate_and_updater_paths_do_not_follow_junctions(tmp_path, monkeypatch):
    ctx = context(tmp_path, browsers=["chrome"])
    chrome = existing(ctx)
    yandex = existing(ctx, "yandex")
    outside = ctx.paths.workspace / "outside"
    outside.mkdir()
    victim = outside / "service_update.exe"
    victim.write_bytes(b"keep")
    links = [chrome / "Certificates", yandex / "App" / "linked"]
    try:
        for link in links:
            script = "New-Item -ItemType Junction -Path '{}' -Value '{}'".format(
                str(link).replace("'", "''"), str(outside).replace("'", "''"))
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                           check=True, capture_output=True, **hidden_subprocess_kwargs())
        monkeypatch.setattr(service, "_download", lambda *_a, **_k: pytest.fail("linked destination"))
        with pytest.raises(RuntimeError, match="Linked build path"):
            service.stage_certificates_selected(ctx)
        service._disable_yandex_updater(ctx, registry.browser("yandex"), yandex)
        assert victim.read_bytes() == b"keep"
    finally:
        for link in links:
            if link.exists():
                link.rmdir()


def test_migrated_operations_and_parameters_are_in_the_real_manifest():
    manifest = load_manifest(Path(__file__).resolve().parents[1] / "config" / "tool_manifest.yaml")
    groups = {g.id: g for g in manifest.operation_groups}
    assert "browsers_library_update" in [c.id for c in groups["update"].children]
    assert "certificates_stage" in [c.id for c in groups["certificates"].children]
    for group_id, command_id in [("install", "browsers_build"), ("update", "browsers_update")]:
        node = next(c for c in groups[group_id].children if c.id == command_id)
        fields = {f["id"]: f for f in node.fields}
        assert fields["disable_yandex_updater"]["default"] is True
        for key in ("chrome_download_url", "yandex_download_url"):
            assert fields[key]["section"] == "advanced" and fields[key]["default"] == ""
            assert node.to_operation({key: "https://example.test/setup.exe"}).parameters[key].endswith("setup.exe")
