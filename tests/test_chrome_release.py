"""Which Chrome is the current one, and whether its installer has changed.

Google's list of versions and Google's installer are two separate things. The
list is asked for the version served to everyone, and the installer's ETag
settles what the list cannot.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.error import URLError
import json
import struct
import zipfile

import pytest

from system_core.core.jobs import JobContext
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import browser_registry
from system_core.services import browsers_portable_service as service
from system_core.services import portable_libraries as libraries


# What the release list answered on 4 October 2026, cut down to the fields read.
LIVE_RELEASES = [
    {"version": "155.0.8059.26", "fraction": 0.005},
    {"version": "154.0.8037.98", "fraction": 1},
    {"version": "154.0.8037.97", "fraction": 0.2475},
    {"version": "154.0.8037.95", "fraction": 0.2475},
    {"version": "154.0.8037.94", "fraction": 0.005},
    {"version": "154.0.8037.93", "fraction": 0.495},
]
ON_THE_LIST = "155.0.8059.26"
IN_THE_INSTALLER = "154.0.8037.98"
ETAG = "6e072b9"


class _Reply(BytesIO):
    """What `urlopen` hands back: a body to read and the headers beside it."""

    def __init__(self, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = headers or {}


def _json(payload: object) -> _Reply:
    return _Reply(json.dumps(payload).encode("utf-8"))


def _context(tmp_path: Path, **parameters: object) -> JobContext:
    paths = get_project_paths(tmp_path)
    ensure_project_dirs(paths)
    return JobContext(
        paths=paths,
        operation=Operation(id="test", title="Test", description="", service="test:test", parameters=dict(parameters)),
        log_file=paths.logs / "test.log",
        report_dir=paths.report,
    )


def _log(context: JobContext) -> str:
    return context.log_file.read_text(encoding="utf-8") if context.log_file.exists() else ""


# --- the version on the list ----------------------------------------------------


def test_the_version_served_to_everyone_wins_over_the_newest_number() -> None:
    assert service._fully_released(LIVE_RELEASES) == IN_THE_INSTALLER


def test_while_nothing_is_served_to_everyone_the_widest_release_is_taken() -> None:
    staged = [item for item in LIVE_RELEASES if item["fraction"] != 1]

    assert service._fully_released(staged) == "154.0.8037.93"


def test_of_two_releases_served_to_everyone_the_newer_is_taken() -> None:
    # `.120` is newer than `.98`, which a comparison of the strings gets wrong.
    releases = [{"version": "154.0.8037.98", "fraction": 1}, {"version": "154.0.8037.120", "fraction": 1}]

    assert service._fully_released(releases) == "154.0.8037.120"


def test_a_release_with_a_field_missing_does_not_break_the_choice() -> None:
    releases = [{"version": "", "fraction": 1}, {"version": "154.0.8037.98"}, {"version": "153.0.1.1", "fraction": "x"}]

    assert service._fully_released(releases) == "154.0.8037.98"
    assert service._fully_released([]) == ""


def test_the_version_is_read_from_the_release_list(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def urlopen(request, **_options):  # noqa: ANN001 - mirrors urlopen's shape
        asked.append(request.full_url)
        return _json({"releases": LIVE_RELEASES})

    monkeypatch.setattr(service, "urlopen", urlopen)

    assert service._chrome_api_version() == IN_THE_INSTALLER
    assert asked == [service.CHROME_RELEASES_API]


@pytest.mark.parametrize("answer", ["offline", "empty", "not json", "not an object"])
def test_a_release_list_that_says_nothing_falls_back_to_the_list_of_versions(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    replies = {"empty": _json({"releases": []}), "not json": _Reply(b"<html>"), "not an object": _json(["154.0.8037.98"])}

    def urlopen(request, **_options):  # noqa: ANN001 - mirrors urlopen's shape
        if request.full_url != service.CHROME_RELEASES_API:
            return _json({"versions": [{"version": ON_THE_LIST}]})
        if answer == "offline":
            raise URLError("no route to the release list")
        return replies[answer]

    monkeypatch.setattr(service, "urlopen", urlopen)

    assert service._chrome_api_version() == ON_THE_LIST


# --- the installer's ETag -------------------------------------------------------


def test_the_etag_is_asked_for_without_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []
    monkeypatch.setattr(
        service, "urlopen", lambda request, **_options: requests.append(request) or _Reply(headers={"ETag": f'W/"{ETAG}"'})
    )

    assert service._remote_etag("https://example.test/setup.exe", "agent") == ETAG
    assert requests[0].get_method() == "HEAD"
    # The compressed variant of the same file carries another ETag.
    assert requests[0].get_header("Accept-encoding") == "identity"


@pytest.mark.parametrize("failure", [URLError("offline"), ConnectionResetError("reset by the server"), TimeoutError()])
def test_no_answer_about_the_etag_is_an_empty_one(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    def refuse(*_arguments, **_options):
        raise failure

    monkeypatch.setattr(service, "urlopen", refuse)
    assert service._remote_etag("https://example.test/setup.exe") == ""

    monkeypatch.setattr(service, "urlopen", lambda *_arguments, **_options: _Reply())
    assert service._remote_etag("https://example.test/setup.exe") == ""


def _server(monkeypatch: pytest.MonkeyPatch, etag: str, body: bytes = b"new installer") -> list[str]:
    """A server holding one file under one ETag; returns the methods it is asked with."""
    methods: list[str] = []

    def urlopen(request, **_options):  # noqa: ANN001 - mirrors urlopen's shape
        methods.append(request.get_method())
        headers = {"ETag": f'"{etag}"'} if etag else {}
        if request.get_method() == "HEAD":
            return _Reply(headers=headers)
        return _Reply(body, {**headers, "Content-Length": str(len(body))})

    monkeypatch.setattr(service, "urlopen", urlopen)
    return methods


def _kept(context: JobContext, etag: str | None) -> Path:
    """An installer downloaded earlier, with the note of its ETag or without one."""
    target = context.paths.workspace / "ChromeStandaloneSetup64-154.0.8037.98.exe"
    target.write_bytes(b"kept installer")
    if etag is not None:
        service._etag_note(target).write_text(etag, encoding="utf-8")
    return target


def _fetch(context: JobContext, target: Path, **options: object) -> service.DownloadedAsset:
    return service._download(context, "https://example.test/setup.exe", target, "Chrome", **options)


def test_a_kept_installer_is_used_while_the_server_holds_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    target = _kept(context, ETAG)
    methods = _server(monkeypatch, ETAG)

    asset = _fetch(context, target, verify_cache=True)

    assert target.read_bytes() == b"kept installer"
    assert asset.etag == ETAG and methods == ["HEAD"]


@pytest.mark.parametrize("noted", ["an-older-etag", None])
def test_a_kept_installer_is_fetched_again_once_it_is_not_what_the_server_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noted: str | None
) -> None:
    """With no note at all the name is the only witness, and the name is the list's promise."""
    context = _context(tmp_path)
    target = _kept(context, noted)
    methods = _server(monkeypatch, ETAG)

    asset = _fetch(context, target, verify_cache=True)

    assert target.read_bytes() == b"new installer"
    assert asset.etag == ETAG and methods == ["HEAD", "GET"]
    assert service._kept_etag(target) == ETAG
    assert "is not confirmed as the file the server holds now" in _log(context)


def test_a_server_that_names_no_etag_leaves_the_kept_installer_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    target = _kept(context, None)
    methods = _server(monkeypatch, "")

    asset = _fetch(context, target, verify_cache=True)

    assert target.read_bytes() == b"kept installer"
    assert asset.etag == "" and methods == ["HEAD"]


def test_a_note_is_not_left_to_vouch_for_a_file_it_never_described(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    target = _kept(context, "an-older-etag")
    target.unlink()
    _server(monkeypatch, "")

    asset = _fetch(context, target, verify_cache=True)

    assert target.read_bytes() == b"new installer" and asset.etag == ""
    assert not service._etag_note(target).exists()


def test_other_downloads_ask_the_server_nothing_and_keep_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    target = _kept(context, None)
    methods = _server(monkeypatch, ETAG)

    assert _fetch(context, target).etag == "" and methods == []

    asset = _fetch(context, target, use_cache=False)

    assert target.read_bytes() == b"new installer" and methods == ["GET"]
    assert asset.etag == "" and not service._etag_note(target).exists()


def test_only_chrome_from_its_own_address_has_a_permanent_installer(tmp_path: Path) -> None:
    chrome = browser_registry.browser("chrome")

    assert service._permanent_installer(_context(tmp_path), chrome)
    assert not service._permanent_installer(_context(tmp_path, chrome_download_url="https://example.test/c.exe"), chrome)
    for other in ("yandex", "brave", "chromium_gost", "ungoogled_chromium"):
        assert not service._permanent_installer(_context(tmp_path), browser_registry.browser(other))


# --- the check and the update ---------------------------------------------------


def _chrome_build(
    context: JobContext,
    monkeypatch: pytest.MonkeyPatch,
    *,
    in_app: str = IN_THE_INSTALLER,
    note: dict[str, object] | None = None,
    served: str = ETAG,
) -> list[str]:
    """Our own Chrome build while the list names a newer version than the installer holds.

    Returns the addresses whose ETag was asked for.
    """
    spec = browser_registry.browser("chrome")
    build = context.paths.input / spec.folder
    (build / "App").mkdir(parents=True)
    (build / "App" / spec.executable).write_bytes(b"MZ")
    (build / "App" / "portable-library.json").write_text(json.dumps({"engine": "chrome_plus"}), encoding="utf-8")
    (build / "Data").mkdir()
    stamp = {"browser_version": IN_THE_INSTALLER, "installer_etag": ETAG} if note is None else note
    (build / service.BUILD_STAMP_FILE).write_text(json.dumps(stamp), encoding="utf-8")

    asked: list[str] = []
    monkeypatch.setattr(service, "_published_wrapper_version", lambda *_arguments: "2.0")
    monkeypatch.setattr(service, "_chrome_api_version", lambda: ON_THE_LIST)
    monkeypatch.setattr(service, "_file_version", lambda path: "2.0" if path.name == "version.dll" else in_app)
    monkeypatch.setattr(service, "_remote_etag", lambda url, _agent="": asked.append(url) or served)
    return asked


def _chrome(tmp_path: Path, **parameters: object) -> JobContext:
    return _context(tmp_path, browsers=["chrome"], portable_engine="chrome_plus", guard_defender=False, **parameters)


def test_a_newer_number_on_the_list_is_no_update_while_the_installer_is_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _chrome(tmp_path)
    asked = _chrome_build(context, monkeypatch)

    [row] = service.check_updates(context)["browsers"]

    assert row["published"] == ON_THE_LIST and row["build"] == IN_THE_INSTALLER
    assert row["update"] is False and row["installer_unchanged"] is True
    assert asked == [browser_registry.browser("chrome").url]
    assert f"-> up to date; the installer still carries {IN_THE_INSTALLER}" in _log(context)


def test_a_changed_installer_is_an_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _chrome(tmp_path)
    _chrome_build(context, monkeypatch, served="another-etag")

    [row] = service.check_updates(context)["browsers"]

    assert row["update"] is True and row["installer_unchanged"] is False
    assert "-> update available" in _log(context)


def test_a_server_that_does_not_answer_is_not_taken_for_an_unchanged_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _chrome(tmp_path)
    _chrome_build(context, monkeypatch, served="")

    [row] = service.check_updates(context)["browsers"]

    assert row["update"] is True and row["installer_unchanged"] is False


@pytest.mark.parametrize(
    "in_app,note",
    [
        # An App swapped by hand: the note describes a browser that is no longer there.
        ("150.0.0.1", {"browser_version": IN_THE_INSTALLER, "installer_etag": ETAG}),
        # A build made before the ETag was written down.
        (IN_THE_INSTALLER, {"browser_version": IN_THE_INSTALLER}),
        (IN_THE_INSTALLER, {}),
    ],
)
def test_a_note_that_cannot_vouch_for_the_build_leaves_the_version_to_decide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, in_app: str, note: dict[str, object]
) -> None:
    context = _chrome(tmp_path)
    asked = _chrome_build(context, monkeypatch, in_app=in_app, note=note)

    [row] = service.check_updates(context)["browsers"]

    assert row["update"] is True and row["installer_unchanged"] is False
    assert asked == []


def test_the_server_is_not_asked_when_the_versions_already_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _chrome(tmp_path)
    asked = _chrome_build(context, monkeypatch)
    monkeypatch.setattr(service, "_chrome_api_version", lambda: IN_THE_INSTALLER)

    [row] = service.check_updates(context)["browsers"]

    assert row["update"] is False and row["installer_unchanged"] is False
    assert asked == []


def test_update_skips_a_build_made_from_the_installer_still_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _chrome(tmp_path)
    _chrome_build(context, monkeypatch)
    monkeypatch.setattr(service, "_require_7zip", lambda _context: None)
    monkeypatch.setattr(service, "_build_one", lambda *_arguments, **_options: pytest.fail("nothing to download"))

    result = service.update_selected(context)

    assert result["updated"] == [] and result["failed"] == []
    assert result["skipped"][0]["reason"] == "already current"
    assert f"{ON_THE_LIST} is on the list, but the installer still carries {IN_THE_INSTALLER}" in _log(context)


def test_update_goes_ahead_once_the_installer_has_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _chrome(tmp_path)
    _chrome_build(context, monkeypatch, served="another-etag")
    monkeypatch.setattr(service, "_require_7zip", lambda _context: None)
    built: list[str] = []
    monkeypatch.setattr(
        service, "_build_one", lambda _context, spec, **_options: built.append(spec.id) or {"id": spec.id, "version": ON_THE_LIST}
    )

    result = service.update_selected(context)

    assert built == ["chrome"] and result["skipped"] == []


# --- what the build note keeps --------------------------------------------------


def _pe(path: Path) -> Path:
    blob = bytearray(b"MZ" + b"\0" * 62)
    struct.pack_into("<I", blob, 60, 64)
    blob += b"PE\0\0" + struct.pack("<H", 0x8664)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(blob))
    return path


@pytest.mark.parametrize("browser_id,permanent", [("chrome", True), ("brave", False)])
def test_the_build_note_keeps_the_etag_of_a_permanent_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser_id: str, permanent: bool
) -> None:
    context = _context(tmp_path, guard_defender=False)
    spec = browser_registry.browser(browser_id)
    payload = context.paths.workspace / "payload"
    _pe(payload / spec.executable)
    installer = context.paths.workspace / "setup.exe"
    installer.write_bytes(b"fixture")
    wrapper = context.paths.workspace / "proxy-library.zip"
    with zipfile.ZipFile(wrapper, "w") as zipped:
        zipped.write(_pe(context.paths.workspace / "version.dll"), libraries.PROXY_DLLS["x64"])
    verified: list[bool] = []

    def download(_context, url, _target, _label, **options):  # noqa: ANN001 - mirrors _download
        verified.append(options["verify_cache"])
        return service.DownloadedAsset("fixture", url, installer, "", 7, ETAG if options["verify_cache"] else "")

    monkeypatch.setattr(service, "_download", download)
    monkeypatch.setattr(service, "_browser_source", lambda _context, _spec: (IN_THE_INSTALLER, "https://example.test/s.exe", "s.exe"))
    monkeypatch.setattr(service, "_unpack_browser", lambda *_arguments: payload)
    monkeypatch.setattr(service, "_file_version", lambda _path: "")

    result = service._build_one(
        context, spec, engine="proxy_library", plus_archive=wrapper, plus_version="2.0",
        wipe_registry=False, with_certificates=False, package_archive=False,
    )

    assert verified == [permanent]
    assert service._build_stamp(Path(result["artifact"]))["installer_etag"] == (ETAG if permanent else "")
