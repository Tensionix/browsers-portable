"""Official file downloads, including interruptions and an unversioned installer."""

from io import BytesIO
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
import hashlib
import json
import shutil
import struct
import zipfile

import pytest

from system_core.core.manifest import load_manifest
from system_core.core.paths import get_project_paths
from system_core.services import ready_portable_service as service


def context(root: Path, **parameters):
    logs = []
    progress = []
    return SimpleNamespace(
        paths=get_project_paths(root), operation=SimpleNamespace(parameters=parameters),
        report_dir=root / "report" / "run", log=logs.append, progress=progress.append,
        cancelled=lambda: False, logs=logs, values=progress,
    )


def executable(payload=b"original upstream bytes"):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    return bytes(data) + payload


def portable_zip():
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("LibreWolf/librewolf.exe", executable())
        archive.writestr("LibreWolf Portable.exe", executable(b"original launcher"))
    return stream.getvalue()


def release(browser="cent"):
    spec = service.READY_BY_ID[browser]
    filename = {"cent": "centbrowser_5.2.1168.83_x64_portable.exe",
                "librewolf": "librewolf-156.0.1-1-windows-x86_64-portable.zip",
                "vivaldi": "Vivaldi.8.2.4133.80.x64.exe",
                "duckduckgo": "DuckDuckGo.Installer.exe"}[browser]
    return service.ReadyRelease(browser, "current" if browser == "duckduckgo" else "1.2.3",
                                "x64", filename, "https://" + spec.download_host + "/" + filename,
                                spec.source_page, spec.package_kind)


class Response(BytesIO):
    def __init__(self, data, expected=None, on_read=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if expected is None else expected)}
        self.on_read = on_read

    def read(self, size=-1):
        result = super().read(size)
        if self.on_read:
            self.on_read()
        return result


CENT_PAGE = """
<a href='https://static.centbrowser.com/win_stable/5.2.1168.70/centbrowser_5.2.1168.70_x64_portable.exe'>old</a>
<a href='https://static.centbrowser.com/win_stable/5.2.1168.83/centbrowser_5.2.1168.83_x64_portable.exe'>x64</a>
<a href='https://static.centbrowser.com/win_stable/5.2.1168.83/centbrowser_5.2.1168.83_portable.exe'>x86</a>
<a href='https://static.centbrowser.com/win_stable/99.0/centbrowser_99.0_x64.exe'>not portable</a>
<a href='https://mirror.example/win_stable/99.0/centbrowser_99.0_x64_portable.exe'>third party</a>
<a href='http://static.centbrowser.com/win_stable/99.0/centbrowser_99.0_x64_portable.exe'>http</a>
<a href='https://static.centbrowser.com/win_beta/99.0/centbrowser_99.0_x64_portable.exe'>beta</a>
"""
VIVALDI_PAGE = """
<a href='https://downloads.vivaldi.com/stable/Vivaldi.5.6.2867.62.x64.exe'>old Windows</a>
<a href='https://downloads.vivaldi.com/stable/Vivaldi.8.2.4133.80.exe'>x86</a>
<a href='https://downloads.vivaldi.com/stable/Vivaldi.8.2.4133.80.arm64.exe'>ARM64</a>
<a href='https://downloads.vivaldi.com/stable/Vivaldi.8.2.4133.80.x64.exe'>x64</a>
<a href='https://downloads.vivaldi.com/snapshot/Vivaldi.99.0.x64.exe'>snapshot</a>
"""
LIBREWOLF_PAGE = """
<a href='https://dl.librewolf.net/librewolf/156.0.1-1/librewolf-156.0.1-1-windows-x86_64-portable.zip'>x64</a>
<a href='https://dl.librewolf.net/librewolf/156.0.1-1/librewolf-156.0.1-1-windows-arm64-portable.zip'>ARM64</a>
<a href='https://dl.librewolf.net/librewolf/999.0/librewolf-999.0-windows-x86_64-setup.exe'>installer</a>
"""


@pytest.mark.parametrize("browser,arch,page,filename", [
    ("cent", "x64", CENT_PAGE, "centbrowser_5.2.1168.83_x64_portable.exe"),
    ("cent", "x86", CENT_PAGE, "centbrowser_5.2.1168.83_portable.exe"),
    ("vivaldi", "x64", VIVALDI_PAGE, "Vivaldi.8.2.4133.80.x64.exe"),
    ("vivaldi", "x86", VIVALDI_PAGE, "Vivaldi.8.2.4133.80.exe"),
    ("vivaldi", "arm64", VIVALDI_PAGE, "Vivaldi.8.2.4133.80.arm64.exe"),
    ("librewolf", "x64", LIBREWOLF_PAGE, "librewolf-156.0.1-1-windows-x86_64-portable.zip"),
    ("librewolf", "arm64", LIBREWOLF_PAGE, "librewolf-156.0.1-1-windows-arm64-portable.zip"),
])
def test_current_official_file_matches_architecture(browser, arch, page, filename):
    found = service.parse_release(browser, arch, page)
    assert found.filename == filename
    assert found.architecture == arch


@pytest.mark.parametrize("browser,arch", [("cent", "arm64"), ("librewolf", "x86"),
                                         ("duckduckgo", "x86"), ("duckduckgo", "arm64")])
def test_unsupported_architecture_does_not_fetch_a_wrong_package(monkeypatch, browser, arch):
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: pytest.fail("network must not be called"))
    with pytest.raises(RuntimeError, match="no official"):
        service.resolve_release(browser, arch)


def test_missing_vendor_link_fails_instead_of_using_a_repack():
    with pytest.raises(RuntimeError, match="not found"):
        service.parse_release("cent", "x64", "<a href='https://mirror.example/centbrowser_99.0_x64_portable.exe'>mirror</a>")


def test_duck_uses_current_official_installer_without_a_head_request(monkeypatch):
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: pytest.fail("resolver does not need HEAD"))
    found = service.resolve_release("duckduckgo", "x64")
    assert found.url == service.DUCK_INSTALLER
    assert found.filename == "DuckDuckGo.Installer.exe"
    assert found.package_kind == "installer"


@pytest.mark.parametrize("browser", ["cent", "vivaldi", "librewolf"])
def test_download_keeps_exact_vendor_bytes_and_name_and_reuses_checked_copy(tmp_path, monkeypatch, browser):
    job = context(tmp_path)
    item = release(browser)
    data = portable_zip() if browser == "librewolf" else executable()
    requests = []
    monkeypatch.setattr(service, "urlopen", lambda request, **_kwargs: requests.append(request) or Response(data))
    downloaded = service.download_release(job, item, tmp_path / "downloads", 0, 1)
    target = Path(downloaded["artifact"])
    assert target.name == item.filename
    assert target.read_bytes() == data
    assert downloaded["sha256"] == hashlib.sha256(data).hexdigest()
    assert sorted(path.name for path in target.parent.iterdir()) == [item.filename]
    record = next((job.paths.report / "official_download_cache").glob("*.json"))
    assert json.loads(record.read_text())["sha256"] == downloaded["sha256"]
    cached = service.download_release(job, item, target.parent, 0, 1)
    assert cached["cached"] is True
    assert len(requests) == 1
    assert job.values[-1] == 1


def test_unversioned_duck_installer_is_compared_by_content_on_every_run(tmp_path, monkeypatch):
    job = context(tmp_path)
    item = release("duckduckgo")
    original = executable(b"version A")
    current = executable(b"version B")
    responses = iter([original, original, current, current])
    requests = []
    monkeypatch.setattr(service, "urlopen", lambda request, **_kwargs: requests.append(request) or Response(next(responses)))
    first = service.download_release(job, item, tmp_path / "downloads", 0, 1)
    target = Path(first["artifact"])
    before = target.stat().st_mtime_ns
    second = service.download_release(job, item, target.parent, 0, 1)
    assert second["cached"] is True
    assert target.stat().st_mtime_ns == before
    third = service.download_release(job, item, target.parent, 0, 1)
    assert third["cached"] is False
    assert third["artifact"] == first["artifact"]
    assert target.read_bytes() == current
    assert third["sha256"] != first["sha256"]
    fourth = service.download_release(job, item, target.parent, 0, 1)
    assert fourth["cached"] is True
    assert len(requests) == 4
    assert sorted(path.name for path in target.parent.iterdir()) == [item.filename]


def test_redownload_after_output_cleanup_does_not_require_deleting_reports(tmp_path, monkeypatch):
    job = context(tmp_path)
    item = release()
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(executable()))
    first = service.download_release(job, item, tmp_path / "downloads", 0, 1)
    Path(first["artifact"]).unlink()
    again = service.download_release(job, item, tmp_path / "downloads", 0, 1)
    assert again["cached"] is False
    assert Path(again["artifact"]).read_bytes() == executable()


def test_project_local_download_cache_survives_moving_the_portable_project(tmp_path, monkeypatch):
    original_root = tmp_path / "original"
    job = context(original_root)
    item = release()
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(executable()))
    service.download_release(job, item, original_root / "output", 0, 1)
    moved_root = tmp_path / "moved"
    shutil.copytree(original_root, moved_root)
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: pytest.fail("verified file should be reused after moving"))
    again = service.download_release(context(moved_root), item, moved_root / "output", 0, 1)
    assert again["cached"] is True
    assert Path(again["artifact"]).read_bytes() == executable()


@pytest.mark.parametrize("data,expected", [(b"", 0), (executable(), 9999),
                                         (b"<html>server error</html>", None), (b"MZ", 2)])
def test_incomplete_or_non_executable_response_is_not_published(tmp_path, monkeypatch, data, expected):
    job = context(tmp_path)
    folder = tmp_path / "downloads"
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(data, expected))
    with pytest.raises(RuntimeError):
        service.download_release(job, release(), folder, 0, 1)
    assert list(folder.iterdir()) == []


def test_portable_zip_without_browser_is_not_published(tmp_path, monkeypatch):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("readme.txt", "not a browser package")
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(stream.getvalue()))
    with pytest.raises(RuntimeError, match="no librewolf.exe"):
        service.download_release(context(tmp_path), release("librewolf"), tmp_path / "downloads", 0, 1)
    assert list((tmp_path / "downloads").iterdir()) == []


def test_failed_duck_refresh_preserves_previous_download(tmp_path, monkeypatch):
    job = context(tmp_path)
    data = executable()
    item = release("duckduckgo")
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(data))
    first = service.download_release(job, item, tmp_path / "downloads", 0, 1)
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(b"MZ", 9000))
    with pytest.raises(RuntimeError, match="Incomplete"):
        service.download_release(job, item, tmp_path / "downloads", 0, 1)
    assert Path(first["artifact"]).read_bytes() == data
    assert not list((tmp_path / "downloads").glob("*.part"))


def test_user_file_and_changed_cached_file_are_preserved(tmp_path, monkeypatch):
    job = context(tmp_path)
    item = release()
    folder = tmp_path / "downloads"
    folder.mkdir()
    target = folder / item.filename
    target.write_bytes(b"user file")
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: pytest.fail("must preserve the user's file"))
    with pytest.raises(RuntimeError, match="kept unchanged"):
        service.download_release(job, item, folder, 0, 1)
    assert target.read_bytes() == b"user file"
    # Removal here is only a disposable fixture's known file.
    target.unlink()
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(executable()))
    service.download_release(job, item, folder, 0, 1)
    target.write_bytes(b"user changed the download")
    with pytest.raises(RuntimeError, match="kept unchanged"):
        service.download_release(job, item, folder, 0, 1)
    assert target.read_bytes() == b"user changed the download"


def test_cancellation_discards_only_the_incomplete_file(tmp_path, monkeypatch):
    job = context(tmp_path)
    folder = tmp_path / "downloads"
    folder.mkdir()
    unrelated = folder / "keep.txt"
    unrelated.write_text("keep")
    cancelled = [False]
    job.cancelled = lambda: cancelled[0]
    monkeypatch.setattr(service, "urlopen", lambda *_args, **_kwargs: Response(executable(), on_read=lambda: cancelled.__setitem__(0, True)))
    with pytest.raises(service.DownloadCancelled):
        service.download_release(job, release(), folder, 0, 1)
    assert list(folder.iterdir()) == [unrelated]


def test_batch_continues_after_source_failure_and_never_installs(tmp_path, monkeypatch):
    job = context(tmp_path, ready_browsers=["cent", "librewolf", "vivaldi", "duckduckgo"],
                  ready_arch="x64", output_path="custom-target")
    profile = tmp_path / "Data" / "profile.txt"
    profile.parent.mkdir()
    profile.write_text("existing profile")
    def resolve(browser, _arch):
        if browser == "cent":
            raise URLError("source unavailable")
        return release(browser)
    monkeypatch.setattr(service, "resolve_release", resolve)
    monkeypatch.setattr(service, "urlopen", lambda request, **_kwargs: Response(portable_zip() if request.full_url.endswith(".zip") else executable()))
    result = service.download_selected(job)
    assert len(result["failed"]) == 1
    assert len(result["downloaded"]) == 3
    assert result["output"] == str(tmp_path / "custom-target" / "Browser Downloads")
    assert profile.read_text() == "existing profile"
    assert not list((tmp_path / "custom-target").rglob("version.dll"))
    assert not list((tmp_path / "custom-target").rglob("librewolf.exe"))
    report = json.loads((job.report_dir / "ready_portable_downloads.json").read_text())
    assert report["downloaded"] == result["downloaded"]


def test_interrupted_chunked_response_does_not_stop_the_other_downloads(tmp_path, monkeypatch):
    job = context(tmp_path, ready_browsers=["cent", "vivaldi"], ready_arch="x64")
    monkeypatch.setattr(service, "resolve_release", lambda browser, _arch: release(browser))
    def interrupted():
        raise IncompleteRead(b"truncated body", 100)
    monkeypatch.setattr(service, "urlopen", lambda request, **_kwargs: Response(executable(), on_read=interrupted)
                        if "centbrowser" in request.full_url else Response(executable()))
    result = service.download_selected(job)
    assert [row["browser"] for row in result["downloaded"]] == ["vivaldi"]
    assert [row["browser"] for row in result["failed"]] == ["Cent Browser"]
    assert not list(Path(result["output"]).rglob("*.part"))


def test_manifest_keeps_downloads_separate_from_dll_builds():
    root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(root / "config" / "tool_manifest.yaml")
    group = next(group for group in manifest.operation_groups if group.id == "browser_downloads")
    assert group.title_ru == "Скачивание"
    command, = group.children
    assert command.service == "system_core.services.ready_portable_service:download_selected"
    assert {field["id"] for field in command.fields} == {"ready_browsers", "ready_arch"}
    assert {item["value"] for item in service.browser_options()} == {"cent", "librewolf", "vivaldi", "duckduckgo"}
    duck = next(item for item in service.browser_options() if item["value"] == "duckduckgo")
    assert "установщик" in duck["label_ru"]
