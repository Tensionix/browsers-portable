"""One engine for the whole Chromium stack: download, unpack, publish, update.

Every browser in `browser_registry` is built the same way and differs only in
what the registry says about it:

    installer .exe -> payload .7z -> <browser folder>   ┐
    archive .zip   -> <browser folder>                  ┴-> <build>\\App\\

Portability uses the selected library (`version.dll` beside the browser executable), the same
piece for all of them, and the certificate block is shared too - files into the
build, trust as a separate act, and an equally separate way to take it back.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
import base64
import binascii
import contextlib
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile

from system_core.services import build_adoption as adoption
from system_core.services import portable_libraries as libraries
from system_core.services.long_paths import MAX_PLAIN_PATH, long_path as _long, plain_path as _plain

from system_core.core.jobs import (JobContext, OperationCancelled, TransferNote, hidden_subprocess_kwargs,
                                   utf8_subprocess_env)
from system_core.core.paths import expand_percent
from system_core.services.browser_registry import BROWSERS, BrowserSpec, browser


USER_AGENT = "Audion-Browsers-Portable"
# What makes a build portable, and the operator picks one:
#   chrome_plus    - Chrome++, the wrapper this program started with;
#   proxy_library  - the other proxy `version.dll`, published on GitFlic.
PORTABLE_ENGINES = libraries.ENGINES
DEFAULT_PORTABLE_ENGINE = "proxy_library"

# GitFlic's REST API needs a personal token, so the public pages are read
# instead: the release list carries the newest release id and its version, and
# the release page carries the single attachment.
PROXY_LIBRARY_HOST = "https://gitflic.ru"
PROXY_LIBRARY_PROJECT = "neyrostalker/proksi-biblioteka"
CHROME_VERSION_API = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/win64/channels/stable/versions?pageSize=1"
)
# The releases being served right now, each with the share of users it reaches.
CHROME_RELEASES_API = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/win64/channels/stable/versions/all/releases"
    "?filter=endtime=none&order_by=version%20desc"
)
BUILD_STAMP_FILE = "Portable-Build.json"
YANDEX_UPDATER_EXECUTABLE = "service_update.exe"

CERTIFICATES_DIRECTORY = "Certificates"
RUSSIAN_TRUSTED_CERTIFICATES = (
    {
        "name": "russian_trusted_root_ca.crt",
        "url": "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
        "store": "Root",
        "subject": "Russian Trusted Root CA",
        "thumbprint": "8FF915CCAB7BC16F8C5C8099D53E0E115B3AEC2F",
    },
    {
        "name": "russian_trusted_sub_ca.crt",
        "url": "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt",
        # The issuing certificate belongs in the intermediate store, not in Root.
        "store": "CA",
        "subject": "Russian Trusted Sub CA",
        "thumbprint": "335D43F53451B781535FF3882DF713D3C14F8A01",
    },
)


@dataclass(frozen=True)
class DownloadedAsset:
    name: str
    url: str
    path: Path
    sha256: str
    size: int
    # What the server called the file, kept only for an address whose file changes.
    etag: str = ""


def _check_cancelled(context: JobContext, before: str) -> None:
    """Stop here if Cancel was pressed; `before` says what was not done.

    The cancel travels as `OperationCancelled`, which is not a `RuntimeError`:
    the library download takes those for "the source is unavailable" and falls
    back to the local reserve, and a cancel must not be answered by carrying on.
    """
    if context.cancelled():
        raise OperationCancelled(f"Cancelled before {before}.")


def _param_text(context: JobContext, key: str, default: str = "") -> str:
    return str(context.operation.parameters.get(key, default) or "").strip()


def _param_bool(context: JobContext, key: str, default: bool = False) -> bool:
    value = context.operation.parameters.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if value is None:
        return default
    return bool(value)


def _param_list(context: JobContext, key: str) -> list[str]:
    value = context.operation.parameters.get(key, [])
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _resolve_project_path(context: JobContext, raw_path: str, default_name: str) -> Path:
    path_text = str(raw_path or "").strip().strip('"') or default_name
    path = Path(expand_percent(path_text)).expanduser()
    if not path.is_absolute():
        path = context.paths.root / path
    return path.resolve()


def _output_root(context: JobContext) -> Path:
    output = _resolve_project_path(context, _param_text(context, "output_path"), "output")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _input_root(context: JobContext) -> Path:
    return _resolve_project_path(context, _param_text(context, "input_path"), "input")


def _portable_root(context: JobContext) -> Path:
    portable_root = _output_root(context) / "Portable"
    portable_root.mkdir(parents=True, exist_ok=True)
    return portable_root


def _archives_dir(context: JobContext) -> Path:
    """Where downloads are kept, in the long form: a file name alone can cross the limit."""
    path = _long(_portable_root(context) / "_archives")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tmp_dir(context: JobContext) -> Path:
    """The work folder, in the long form: everything unpacked under it may run deep."""
    path = _long(_portable_root(context) / "_tmp")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remove_tree(path: Path) -> None:
    target = _long(path)
    if not target.exists():
        return

    def handle_remove_error(function: Any, item_path: str, _exc_info: Any) -> None:
        # Read-only files are the usual reason; anything already gone is fine.
        with contextlib.suppress(FileNotFoundError):
            os.chmod(item_path, 0o700)
            function(item_path)

    shutil.rmtree(target, onerror=handle_remove_error)


def _discard_tmp(context: JobContext) -> None:
    """Clear the work folder without letting a leftover fail the operation.

    The builds are already in place by the time this runs. A file that cannot
    be removed is worth a line in the log, not a red result that hides what the
    operation actually did - or the real error that came before it.
    """
    try:
        _remove_tree(_tmp_dir(context))
    except OSError as exc:
        context.log(f"[WARN] The work folder could not be cleared: {_plain(_tmp_dir(context))} ({exc})")


def _deep_files(folder: Path) -> tuple[int, int, int]:
    """`(past the limit, all, longest path)` over the files of a folder."""
    lengths = [len(str(_plain(item))) for item in _long(folder).rglob("*") if item.is_file()]
    return sum(1 for length in lengths if length > MAX_PLAIN_PATH), len(lengths), max(lengths, default=0)


def _warn_deep_build(context: JobContext, name: str, build: Path) -> int:
    """Say so when the browser ended up deeper than Windows opens by default.

    The build itself is put together at any depth. Whether it then starts is
    up to Windows, and that is worth knowing before the folder is handed over.
    """
    deep, total, longest = _deep_files(build / "App")
    if deep:
        context.log(
            f"[WARN] {name}: {deep} of the {total} files of the browser in {_plain(build)} lie past "
            f"{MAX_PLAIN_PATH} characters (the longest path is {longest}). Windows opens such files only "
            "where long paths are switched on, so the browser may not start from here. "
            "The build is whole: moved to a shorter path it starts."
        )
    return deep


def _reset_dir(path: Path) -> None:
    _remove_tree(path)
    _long(path).mkdir(parents=True, exist_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
    return cleaned or "download"


def _progress_between(context: JobContext, start: float | None, end: float | None, fraction: float) -> None:
    if start is None or end is None:
        return
    context.progress(start + (end - start) * max(0.0, min(1.0, fraction)))


def _clean_etag(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("W/"):
        text = text[2:]
    return text.strip('"')


def _remote_etag(url: str, user_agent: str = USER_AGENT) -> str:
    """ETag of the file behind an address, asked for without downloading it.

    Empty when the server names none or cannot be reached; the caller then has
    nothing to compare. The plain encoding is asked for on purpose: Google adds
    a suffix to the ETag of the compressed variant of the very same file.
    """
    request = Request(
        url,
        headers={"User-Agent": user_agent or USER_AGENT, "Accept-Encoding": "identity"},
        method="HEAD",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return _clean_etag(response.headers.get("ETag"))
    except Exception:  # noqa: BLE001 - not knowing is an answer here, never a failed operation
        return ""


def _etag_note(target: Path) -> Path:
    """Where the ETag of a kept download is written down, beside the file."""
    return target.with_name(target.name + ".etag")


def _kept_etag(target: Path) -> str:
    try:
        return _etag_note(target).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _forget_download(context: JobContext, path: Path) -> None:
    """A kept download that did not open: removed, with the note that vouched for it."""
    removed = False
    for item in (path, _etag_note(path)):
        with contextlib.suppress(OSError):
            if item.exists():
                item.unlink()
                removed = True
    if removed:
        context.log(f"[CACHE] {path.name} did not open and is not kept: the next run downloads it again.")


def _archive_names(spec: BrowserSpec) -> re.Pattern[str]:
    """The names the downloads of one browser are kept under, with the notes and drafts beside them."""
    own = {
        "github_tag": spec.asset,
        "chrome_api": r"ChromeStandaloneSetup64-.+\.exe",
        "yandex_redirect": r"Yandex-.+\.exe",
    }.get(spec.version_source) or r"(?!)"
    return re.compile(rf"(?:{own}|{re.escape(spec.id)}-custom-.+\.exe)(?:\.etag|\.part)?", re.IGNORECASE)


def _prune_archives(context: JobContext, spec: BrowserSpec, keep: Path) -> None:
    """Of the downloads of a browser only the one just used stays.

    Nothing ever cleared the folder: an installer per browser per version, a hundred megabytes
    and more each, for as long as the Target lived - on a flash drive too. A file that has
    opened makes every older one of the same browser useless.
    """
    pattern = _archive_names(spec)
    spared = {keep.name.casefold(), _etag_note(keep).name.casefold()}
    freed, count = 0, 0
    try:
        candidates = [item for item in keep.parent.iterdir() if item.is_file()]
    except OSError:
        return
    for item in candidates:
        if item.name.casefold() in spared or not pattern.fullmatch(item.name):
            continue
        with contextlib.suppress(OSError):
            size = item.stat().st_size
            item.unlink()
            freed, count = freed + size, count + 1
    if count:
        context.log(f"[CACHE] {spec.name}: {count} older download file(s) removed, {freed / 1024 / 1024:,.0f} MB.")


def _download(
    context: JobContext,
    url: str,
    target: Path,
    label: str,
    *,
    user_agent: str = USER_AGENT,
    progress_start: float | None = None,
    progress_end: float | None = None,
    use_cache: bool = True,
    verify_cache: bool = False,
) -> DownloadedAsset:
    """Fetch a file, or take the copy already kept under the same name.

    `verify_cache` is for an address that stays the same while the file behind
    it changes. The kept copy is then used only while the server still names
    the ETag it was fetched with, and the result carries that ETag.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if use_cache and target.exists() and target.stat().st_size > 0:
        kept = _kept_etag(target) if verify_cache else ""
        served = _remote_etag(url, user_agent) if verify_cache else ""
        # A server that names no ETag leaves nothing to compare, and the name decides.
        if not served or kept == served:
            size = target.stat().st_size
            digest = _sha256(target)
            context.log(f"[CACHE] {label}: {target.name} ({size:,} bytes)")
            _progress_between(context, progress_start, progress_end, 1.0)
            return DownloadedAsset(label, url, target, digest, size, kept)
        context.log(f"[CACHE] {label}: {target.name} is not confirmed as the file the server holds now; downloading it again.")

    context.log(f"[DOWNLOAD] {label}")
    context.log(f"[URL] {url}")
    request = Request(url, headers={"User-Agent": user_agent or USER_AGENT})
    part = target.with_name(target.name + ".part")
    if part.exists():
        part.unlink()
    etag = ""
    try:
        with urlopen(request, timeout=120) as response, part.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            if verify_cache:
                etag = _clean_etag(response.headers.get("ETag"))
            downloaded = 0
            note = TransferNote(context, f"{label}: ")
            while True:
                # A browser is hundreds of megabytes; Cancel must not wait for all of them.
                _check_cancelled(context, f"{label} was downloaded")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    _progress_between(context, progress_start, progress_end, downloaded / total)
                    note.at(downloaded, total)
            if total <= 0:
                _progress_between(context, progress_start, progress_end, 1.0)
            # A connection that closes early ends the reading without an error: what came is
            # counted against what the server said it would send. A file cut short was kept
            # under the name of the whole one, and every later run took it from there.
            if downloaded == 0 or (total > 0 and downloaded != total):
                raise RuntimeError(
                    f"Download failed: {url} (the connection closed at {downloaded:,} of "
                    f"{f'{total:,}' if total > 0 else 'an unknown number of'} bytes)"
                )
        part.replace(target)
    except OperationCancelled:
        part.unlink(missing_ok=True)
        raise
    except RuntimeError:
        part.unlink(missing_ok=True)
        raise
    except (OSError, HTTPException) as exc:
        # The network as it fails in life: a refused or reset connection, a timeout, a body
        # that stops in the middle (`IncompleteRead`), a disk that is full.
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed: {url} ({exc.__class__.__name__}: {exc})") from exc
    if verify_cache:
        # A stale note would vouch for a file it never described.
        if etag:
            _etag_note(target).write_text(etag, encoding="utf-8")
        else:
            _etag_note(target).unlink(missing_ok=True)
    size = target.stat().st_size
    digest = _sha256(target)
    context.log(f"[OK] {target.name} ({size:,} bytes)")
    context.log(f"[SHA256] {digest}")
    return DownloadedAsset(label, url, target, digest, size, etag)


# ---------------------------------------------------------------------------
# Where a release lives
# ---------------------------------------------------------------------------


def github_latest_assets(repo: str) -> tuple[str, list[tuple[str, str]]]:
    """Tag and `(name, url)` of every asset in the latest release.

    The API answers in one request but allows only 60 anonymous calls an hour,
    and that runs out quietly - especially here, where every browser costs one.
    The same list is on the release pages, where there is no quota at all, so the
    HTML is the fallback, and the download link comes from whichever worked.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    try:
        request = Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
        with urlopen(request, timeout=60) as response:
            release = json.loads(response.read().decode("utf-8"))
        assets = [
            (str(item.get("name") or ""), str(item.get("browser_download_url") or ""))
            for item in release.get("assets", [])
        ]
        if assets:
            return str(release.get("tag_name") or "latest"), assets
    except Exception:  # noqa: BLE001 - any network or quota trouble falls back
        pass

    plain = {"User-Agent": USER_AGENT}
    request = Request(f"https://github.com/{repo}/releases/latest", headers=plain)
    with urlopen(request, timeout=60) as response:
        tag = response.geturl().rstrip("/").rsplit("/", 1)[-1]
    request = Request(f"https://github.com/{repo}/releases/expanded_assets/{tag}", headers=plain)
    with urlopen(request, timeout=60) as response:
        page = response.read().decode("utf-8", "replace")
    assets = []
    for path in re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', page):
        name = path.rsplit("/", 1)[-1]
        url = "https://github.com" + path
        if (name, url) not in assets:
            assets.append((name, url))
    if not assets:
        raise RuntimeError(f"No release assets found for {repo}, neither through the API nor on the release page.")
    return tag, assets


def _gitflic_page(path: str) -> str:
    request = Request(f"{PROXY_LIBRARY_HOST}{path}", headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def proxy_library_release() -> tuple[str, str]:
    """Version and download URL of the newest Прокси библиотека release.

    The version is taken as the first four-part number on the release list,
    which is sorted newest first, rather than by the word next to it: the
    heading is written by hand and has been in Russian so far, but the number
    is what the release is actually named after.
    """
    listing = _gitflic_page(f"/project/{PROXY_LIBRARY_PROJECT}/release?sort=TIME&direction=DESC")
    releases = re.findall(rf"/project/{PROXY_LIBRARY_PROJECT}/release/([0-9a-f-]{{36}})", listing)
    versions = re.findall(r"\b(\d+\.\d+\.\d+\.\d+)\b", listing)
    if not releases:
        raise RuntimeError("No releases were found on the Прокси библиотека page.")
    version = versions[0] if versions else ""
    page = _gitflic_page(f"/project/{PROXY_LIBRARY_PROJECT}/release/{releases[0]}")
    match = re.search(rf'href="(/project/{PROXY_LIBRARY_PROJECT}/release/{releases[0]}/[0-9a-f-]{{36}}/download)"', page)
    if match:
        return version, f"{PROXY_LIBRARY_HOST}{match.group(1)}"
    # No attachment on that release: the repository archive of the same tag
    # carries the very same `Bin` folder.
    if not version:
        raise RuntimeError("The Прокси библиотека release has neither an attachment nor a version to fall back on.")
    return version, f"{PROXY_LIBRARY_HOST}/project/{PROXY_LIBRARY_PROJECT}/file/downloadAll?branch={version}&format=zip"


def _fully_released(releases: list[dict[str, Any]]) -> str:
    """The version everyone is served, out of the releases live right now.

    Google rolls a new stable out by degrees, and the newest number is not what
    the installer carries: on 4 October 2026 `155.0.8059.26` was the highest
    stable version and reached under 1% of users, while `154.0.8037.98` reached
    all of them - and that is what `ChromeStandaloneSetup64.exe` held. Taking the
    highest number announced an update the installer could not deliver, and the
    check went on announcing it after every update.

    So: the highest version served to everyone, and, while none is yet, the one
    served to the most.
    """
    live: list[tuple[float, tuple[int, ...], str]] = []
    for item in releases:
        version = str(item.get("version") or "")
        if not version:
            continue
        try:
            fraction = float(item.get("fraction", 0) or 0)
        except (TypeError, ValueError):
            fraction = 0.0
        live.append((min(fraction, 1.0), _version_tuple(version), version))
    return max(live)[2] if live else ""


def _chrome_api_version() -> str:
    request = Request(CHROME_RELEASES_API, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version = _fully_released(payload.get("releases") or [])
        if version:
            return version
    except Exception:  # noqa: BLE001 - whatever went wrong, the request below is the fallback
        pass
    # The release list said nothing usable: the plain list of versions still
    # names the stable line, which is better than no answer at all.
    request = Request(CHROME_VERSION_API, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    versions = payload.get("versions") or []
    return str(versions[0].get("version") or "") if versions else ""


def _yandex_redirect(url: str, user_agent: str, *, require_windows_name: bool = True) -> tuple[str, str]:
    """Version and file URL, read out of the redirect instead of the file."""
    request = Request(url, headers={"User-Agent": user_agent}, method="HEAD")
    with urlopen(request, timeout=60) as response:
        resolved = response.geturl()
    if require_windows_name and "Yandex.exe" not in resolved:
        raise RuntimeError(
            f"The download address did not resolve to a Windows installer: {resolved}. "
            "Yandex picks the platform by the user agent."
        )
    match = re.search(r"/browser/yandex/(\d+)_(\d+)_(\d+)_(\d+)(?:_\d+)?/", resolved)
    return (".".join(match.groups()) if match else ""), resolved


def published_source(spec: BrowserSpec) -> tuple[str, str, str]:
    """`(version, url, filename)` of what the vendor publishes right now."""
    if spec.version_source == "github_tag":
        tag, assets = github_latest_assets(spec.repo)
        pattern = re.compile(spec.asset, re.IGNORECASE)
        for name, url in assets:
            if pattern.fullmatch(name):
                return tag.lstrip("vM"), url, name
        listed = ", ".join(name for name, _url in assets)
        raise RuntimeError(f"{spec.name}: no asset matching {spec.asset!r} in release {tag}. Assets: {listed}")
    if spec.version_source == "chrome_api":
        version = _chrome_api_version()
        return version, spec.url, f"ChromeStandaloneSetup64-{version or 'latest'}.exe"
    if spec.version_source == "yandex_redirect":
        version, resolved = _yandex_redirect(spec.url, spec.user_agent or USER_AGENT)
        return version, resolved, f"Yandex-{version or 'latest'}.exe"
    raise RuntimeError(f"{spec.name}: unknown version source {spec.version_source!r}")


def _custom_installer_url(context: JobContext, spec: BrowserSpec) -> str:
    return _param_text(context, f"{spec.id}_download_url") if spec.id in {"chrome", "yandex"} else ""


def _permanent_installer(context: JobContext, spec: BrowserSpec) -> bool:
    """Whether the browser comes from one address whose file changes underneath.

    Chrome's does: `ChromeStandaloneSetup64.exe` is always the same link, and
    nothing about it names the version inside. The server's ETag is what tells
    one installer from the next.
    """
    return spec.version_source == "chrome_api" and not _custom_installer_url(context, spec)


def _browser_source(context: JobContext, spec: BrowserSpec) -> tuple[str, str, str]:
    """A supplied installer need not match the vendor's latest stable version."""
    url = _custom_installer_url(context, spec)
    if not url:
        return published_source(spec)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{spec.name}: installer URL must use HTTP or HTTPS.")
    version, resolved = _yandex_redirect(url, spec.user_agent or USER_AGENT, require_windows_name=False) if spec.id == "yandex" else ("", url)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    context.log(f"[SOURCE] {spec.name}: custom installer URL; version {version or 'unknown until unpacked'}")
    return version, resolved, f"{spec.id}-custom-{key}-{version or 'latest'}.exe"


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", str(text or ""))][:4]
    return tuple(parts + [0] * (4 - len(parts)))


def same_version(published: str, installed: str, match: str = "exact") -> bool:
    """Whether a build already carries what the vendor publishes.

    Two ways a vendor can number the same release, both real:

    - `exact` - `1.18.2` against a file reporting `1.18.2.0`. Padding to four
      parts settles it; a plain string comparison would announce an update
      forever.
    - `tail` - Brave tags `1.93.134` and ships a `brave.exe` calling itself
      `151.1.93.134`, where `151` is the Chromium major. The published number is
      the end of the reported one.
    """
    if not published or not installed:
        return False
    if match == "tail":
        published_parts = [int(part) for part in re.findall(r"\d+", published)]
        installed_parts = [int(part) for part in re.findall(r"\d+", installed)]
        if not published_parts or len(published_parts) > len(installed_parts):
            return False
        return installed_parts[-len(published_parts):] == published_parts
    return _version_tuple(published) == _version_tuple(installed)


def _file_version(path: Path) -> str:
    """FileVersion of a Windows binary, read through the version API."""
    if os.name != "nt" or not path.is_file():
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        version_api = ctypes.WinDLL("version.dll")
        size = version_api.GetFileVersionInfoSizeW(ctypes.c_wchar_p(str(path)), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not version_api.GetFileVersionInfoW(ctypes.c_wchar_p(str(path)), 0, size, buffer):
            return ""
        block = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version_api.VerQueryValueW(
            buffer, ctypes.c_wchar_p("\\"), ctypes.byref(block), ctypes.byref(length)
        ):
            return ""
        fixed = ctypes.cast(block, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        most, least = fixed[2], fixed[3]
        return f"{most >> 16}.{most & 0xFFFF}.{least >> 16}.{least & 0xFFFF}"
    except Exception:  # noqa: BLE001 - a version is a nicety, never a blocker
        return ""


# ---------------------------------------------------------------------------
# 7-Zip
# ---------------------------------------------------------------------------


def _windows_text(raw: bytes | str | None) -> str:
    """What a tool of Windows wrote into a pipe, as text.

    `certutil` writes its Russian lines in the ANSI code page of the system (cp1251 on a Russian
    Windows), whatever the console is set to. Read as UTF-8 with "replace" they came into the
    journal as rows of replacement marks - "the certificate was added to the store" could not
    be read at all (the owner, 13.10.2026, of the journal of an install: "a strange log").
    UTF-8 is tried first and strictly: a text in a one-byte code page is next to never valid
    UTF-8, and a tool that does write UTF-8 is read as it wrote.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if os.name == "nt" else "latin-1", errors="replace")


def _windows_lines(raw: bytes | str | None) -> list[str]:
    """The lines a tool wrote, each read on its own: a script may change the code page half way."""
    if isinstance(raw, str):
        return [line for line in raw.splitlines() if line.strip()]
    return [line for line in (_windows_text(item) for item in (raw or b"").splitlines()) if line.strip()]


def _seven_zip_path(context: JobContext) -> Path:
    return context.paths.root / "tools" / "7zip" / "bin" / "7za.exe"


def _seven_zip_version(context: JobContext) -> str:
    exe = _seven_zip_path(context)
    if not exe.exists():
        return ""
    result = subprocess.run(
        [str(exe), "i"],
        cwd=str(context.paths.root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "7-Zip" in line:
            return line.strip()
    return "7-Zip available"


def _require_7zip(context: JobContext) -> Path:
    exe = _seven_zip_path(context)
    version = _seven_zip_version(context)
    if not exe.exists() or not version:
        raise RuntimeError("Portable 7-Zip is not available. Run 'Check / install portable 7-Zip' first.")
    context.log(f"[7ZIP] {version}")
    return exe


# --- Defender exclusion guard -------------------------------------------------
#
# Chrome++'s unsigned `version.dll` is a routine Defender false positive. When
# real-time protection grabs it mid-build, the packaging step dies with a bare
# `OSError [Errno 22]`. To keep a build from failing on machines where Defender
# is active, the output folder is excluded from scanning for the duration of the
# build and put back afterwards. The exclusion is a real hole in the machine's
# protection, so it is opened only when Defender is actually running, only for as
# long as the build runs, and it is always paired with a way to take it back.

_DEFENDER_GUARD_DIRNAME = ".defender_guard"
_DEFENDER_GUARD_MAX_SECONDS = 3600


def _defender_guard_script(context: JobContext) -> Path:
    """The elevated half of the guard: a script that lies beside this service.

    Until 11.10.2026 it was looked for in `system_core\\powershell` - the folder of the
    portable PowerShell, which the cleanup of the project empties and git ignores. It was not
    there: Windows asked for UAC, PowerShell found no file, and after half a minute the build
    went on without an exclusion. Beside the service it is a source like the service itself.
    """
    return Path(__file__).with_name("defender_guard.ps1")


def _defender_guard_dir(context: JobContext) -> Path:
    path = context.paths.workspace / _DEFENDER_GUARD_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _defender_active(context: JobContext) -> bool:
    """True only when Windows Defender real-time protection is actually running.

    Read-only and unelevated, so it never triggers UAC by itself: a machine
    without Defender (or with it disabled) answers 'inactive' and the whole guard
    turns into a no-op, exactly the case on the owner's box.
    """
    if os.name != "nt":
        return False
    probe = (
        "$s = Get-Service WinDefend -ErrorAction SilentlyContinue; "
        "if (-not $s -or $s.Status -ne 'Running') { 'inactive'; exit 0 }; "
        "try { if ((Get-MpComputerStatus).RealTimeProtectionEnabled) { 'active' } else { 'inactive' } } "
        "catch { 'active' }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return bool(lines) and lines[-1].lower() == "active"


def _run_elevated_powershell(script: Path, arguments: list[str]) -> int:
    """Launch an elevated, hidden PowerShell through UAC; return ShellExecute code.

    >32 means the process started (the user accepted UAC); 5 (SE_ERR_ACCESSDENIED)
    means UAC was declined; anything else <=32 is another launch failure. It does
    not wait - the guard coordinates through files instead.
    """
    if os.name != "nt":
        return 0
    parts = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f'"{script}"', *arguments]
    params = " ".join(parts)
    SW_HIDE = 0
    return int(ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, None, SW_HIDE))


def _read_guard_result(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not raw:
        return ""
    status, _, detail = raw.partition("\t")
    return f"{status}: {detail}".strip() if detail else status


def _cleanup_guard_files(*paths: Path) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()


def _guard_path_argument(paths: list[Path]) -> str:
    """The folders as one argument, ready to stand between two quotation marks.

    Folders are joined with '|', which Windows paths can never contain, so a single quoted
    argument survives ShellExecute and the script splits it back. A backslash right before the
    closing quote would escape it - a guarded folder that is the root of a drive ends in one
    (`"D:\\out|E:\\"`), the arguments after it glued together, PowerShell refused them, and the
    person had answered UAC for nothing. The backslashes at the very end are doubled: two before
    a quote are read as one, and the quote closes.
    """
    joined = "|".join(str(path) for path in paths)
    return joined + "\\" * (len(joined) - len(joined.rstrip("\\")))


def _guard_pending_file(context: JobContext) -> Path:
    return _defender_guard_dir(context) / "pending.json"


def _guard_pending(context: JobContext) -> list[str]:
    """The folders a guard excluded and was not seen to take back, from earlier runs too."""
    try:
        saved = json.loads(_guard_pending_file(context).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(item) for item in saved if str(item).strip()] if isinstance(saved, list) else []


def _guard_remember(context: JobContext, folders: Iterable[Path | str], *, taken_back: bool) -> None:
    """Note what a guard excluded, and strike it out when the exclusion is seen to be gone.

    The safety net - "Defender: remove exclusion" - knew only the Source and the Target of the
    moment it was pressed. An exclusion left by a run on other folders stayed for good.
    """
    named = {os.path.normcase(str(item)): str(item) for item in _guard_pending(context)}
    for folder in folders:
        key = os.path.normcase(str(folder))
        if taken_back:
            named.pop(key, None)
        else:
            named[key] = str(folder)
    target = _guard_pending_file(context)
    with contextlib.suppress(OSError):
        if named:
            target.write_text(json.dumps(sorted(named.values()), ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            target.unlink(missing_ok=True)


@contextlib.contextmanager
def _defender_guard(context: JobContext, paths: Path | Iterable[Path]) -> Iterator[None]:
    """Exclude one or more folders from Defender for the block, then restore them."""
    targets = [paths] if isinstance(paths, (str, Path)) else list(paths)
    # Keep unique, existing-or-not folders in a stable order.
    seen: dict[str, Path] = {}
    for item in targets:
        resolved = Path(item)
        seen.setdefault(str(resolved), resolved)
    folders = list(seen.values())
    label = ", ".join(str(folder) for folder in folders)

    if not folders or not _defender_active(context):
        context.log("[DEFENDER] Real-time protection is not active; no exclusion needed.")
        yield
        return
    if not _defender_guard_script(context).is_file():
        # Nobody is asked for UAC on behalf of a script that is not there.
        context.log(f"[WARN] [DEFENDER] The guard script is missing ({_defender_guard_script(context)}); "
                    "building without an exclusion (it may fail on version.dll).")
        yield
        return

    guard_dir = _defender_guard_dir(context)
    uid = uuid.uuid4().hex
    lock = guard_dir / f"{uid}.lock"
    ready = guard_dir / f"{uid}.ready"
    result = guard_dir / f"{uid}.result"
    lock.write_text("lock", encoding="utf-8")
    excluded = False
    try:
        context.log(f"[DEFENDER] Requesting a temporary exclusion for {label} (Windows will ask for UAC).")
        code = _run_elevated_powershell(
            _defender_guard_script(context),
            [
                "-Action", "Begin",
                "-Path", f'"{_guard_path_argument(folders)}"',
                "-ParentPid", str(os.getpid()),
                "-Lock", f'"{lock}"',
                "-Ready", f'"{ready}"',
                "-Result", f'"{result}"',
                "-MaxSeconds", str(_DEFENDER_GUARD_MAX_SECONDS),
            ],
        )
        if code <= 32:
            reason = "UAC was declined" if code == 5 else f"elevation failed (code {code})"
            context.log(f"[DEFENDER] {reason}; building without an exclusion (it may fail on version.dll).")
        else:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if ready.exists():
                    excluded = True
                    break
                if result.exists():
                    break
                time.sleep(0.3)
            if excluded:
                _guard_remember(context, folders, taken_back=False)
                context.log(f"[DEFENDER] Temporary exclusion active for {label}.")
            else:
                status = _read_guard_result(result)
                context.log(
                    f"[DEFENDER] {status or 'Exclusion was not confirmed'}; "
                    "building without an exclusion (it may fail on version.dll)."
                )
        yield
    finally:
        # Releasing the lock is the signal for the elevated guard to remove the
        # exclusion; it also removes it on its own if this process has died.
        _cleanup_guard_files(lock)
        if excluded:
            deadline = time.monotonic() + 15
            removed = False
            while time.monotonic() < deadline:
                if _read_guard_result(result).startswith("REMOVED"):
                    removed = True
                    break
                time.sleep(0.3)
            if removed:
                _guard_remember(context, folders, taken_back=True)
            context.log(
                "[DEFENDER] Exclusion removed."
                if removed
                else "[DEFENDER] Removal not confirmed in time; if it lingers, run 'Defender: remove exclusion'."
            )
        _cleanup_guard_files(ready, result)


def defender_exclusion_remove(context: JobContext) -> dict[str, object]:
    """Manually take back the exclusions the build and update guards add.

    A safety net for the rare case where an operation was killed hard and the
    elevated guard never got to remove its exclusion. It clears the output folder
    and the input folder (what every guard excludes, since builds are updated in
    place wherever they live) and whatever an earlier run excluded and was not seen
    to take back - on other folders than those of today. Idempotent: removing a path
    that is not excluded is not an error. An exclusion that was not removed is not
    "done": the operation fails, and says why.
    """
    if os.name != "nt":
        raise RuntimeError("Defender exclusions are a Windows-only concern.")
    named = {os.path.normcase(str(item)): Path(item) for item in [_output_root(context), _input_root(context)]}
    for item in _guard_pending(context):
        named.setdefault(os.path.normcase(item), Path(item))
    folders = list(named.values())
    label = ", ".join(str(folder) for folder in folders)
    if not _defender_active(context):
        context.log("[DEFENDER] Real-time protection is not active; nothing to remove.")
        context.progress(1.0)
        return {"removed": False, "reason": "defender inactive", "paths": [str(f) for f in folders]}

    guard_dir = _defender_guard_dir(context)
    result = guard_dir / f"remove_{uuid.uuid4().hex}.result"
    context.log(f"[DEFENDER] Removing the exclusion for {label} (Windows will ask for UAC).")
    code = _run_elevated_powershell(
        _defender_guard_script(context),
        ["-Action", "Remove", "-Path", f'"{_guard_path_argument(folders)}"', "-Result", f'"{result}"'],
    )
    if code <= 32:
        _cleanup_guard_files(result)
        if code == 5:
            raise RuntimeError("UAC was declined; the exclusion was not removed.")
        raise RuntimeError(f"Could not elevate to remove the exclusion (code {code}).")

    deadline = time.monotonic() + 20
    status = ""
    while time.monotonic() < deadline:
        status = _read_guard_result(result)
        if status:
            break
        time.sleep(0.3)
    _cleanup_guard_files(result)
    context.log(f"[DEFENDER] {status or 'No confirmation received.'}")
    if not status.startswith("REMOVED"):
        raise RuntimeError(
            f"The exclusion was not removed: {status or 'no confirmation came in 20 seconds'}. "
            f"Asked for: {label}."
        )
    _guard_remember(context, folders, taken_back=True)
    context.progress(1.0)
    return {"removed": True, "paths": [str(f) for f in folders]}


def _run_7z(context: JobContext, args: list[str], *, cwd: Path | None = None, doing: str = "7-Zip finished") -> None:
    """Run 7-Zip to its end - or to a stop.

    Unpacking a browser onto a flash drive and packing a build with its profile take minutes,
    and STOP was not seen until they were over: the window waited its two minutes and ended the
    whole tree, the run was neither "stopped" nor finished, and the work folder stayed. The
    process is asked about every half a second now, and a stop ends it at once.
    """
    exe = _require_7zip(context)
    # Told to speak UTF-8: by itself 7-Zip names a file in the OEM code page of the console, and
    # a path with Cyrillic in a line of its errors came out unreadable.
    command = [str(exe), *args, "-sccUTF-8"]
    process = subprocess.Popen(
        command,
        # A process cannot start in a folder given in the long form.
        cwd=str(_plain(cwd) if cwd else context.paths.root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
    )
    deadline = time.monotonic() + 3600
    while True:
        try:
            _stdout, stderr = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            stopped, late = context.cancelled(), time.monotonic() > deadline
            if not stopped and not late:
                continue
            process.kill()
            with contextlib.suppress(Exception):
                process.communicate(timeout=30)
            if stopped:
                raise OperationCancelled(f"Cancelled before {doing}.") from None
            raise RuntimeError("7-Zip did not finish in an hour and was ended.") from None
    for line in (stderr or "").splitlines():
        if line.strip():
            context.log("[STDERR] " + line)
    if process.returncode != 0:
        raise RuntimeError(f"7-Zip failed with exit code {process.returncode}.")


def _extract_archive(context: JobContext, archive: Path, target: Path) -> None:
    context.log(f"[UNPACK] {archive.name} -> {target.name}")
    _reset_dir(target)
    # 7-Zip takes the long form for both the archive and the output folder.
    _run_7z(context, ["x", str(_long(archive)), f"-o{_long(target)}", "-y"], doing=f"{archive.name} was unpacked")


def _copy_seeing_stop(context: JobContext, doing: str) -> Callable[[str, str], Any]:
    """`shutil.copy2` that looks for a stop before every file: a copy of a browser is thousands of them."""
    def copy(source: str, destination: str, **options: Any) -> Any:
        _check_cancelled(context, doing)
        return shutil.copy2(source, destination, **options)
    return copy


def _copy_tree_contents(context: JobContext, source: Path, target: Path, doing: str) -> None:
    source, target = _long(source), _long(target)
    target.mkdir(parents=True, exist_ok=True)
    copy = _copy_seeing_stop(context, doing)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            if destination.exists():
                _remove_tree(destination)
            shutil.copytree(item, destination, copy_function=copy)
        else:
            copy(str(item), str(destination))


def _find_dir(root: Path, name: str) -> Path | None:
    lowered = name.lower()
    for item in root.rglob("*"):
        if item.is_dir() and item.name.lower() == lowered:
            return item
    return None


def _find_file(root: Path, name: str) -> Path | None:
    lowered = name.lower()
    for item in root.rglob("*"):
        if item.is_file() and item.name.lower() == lowered:
            return item
    return None


def _find_executable_dir(root: Path, executable: str) -> Path | None:
    """The folder that holds the browser, wherever the archive decided to put it."""
    found = _find_file(root, executable)
    return found.parent if found else None


# ---------------------------------------------------------------------------
# Unpacking a browser
# ---------------------------------------------------------------------------


def _find_payload_archive(context: JobContext, spec: BrowserSpec, root: Path, depth: int = 0) -> Path | None:
    """The 7z inside a setup .exe, however deep the vendor nested it this time."""
    if spec.payload_archive:
        direct = _find_file(root, spec.payload_archive)
        if direct:
            return direct
    if depth >= 4:
        return None

    candidates = [
        item
        for item in root.rglob("*")
        if item.is_file() and (item.name.lower().endswith(".7z") or item.name.lower().endswith("_installer.exe"))
    ]
    for index, candidate in enumerate(candidates, start=1):
        if spec.payload_archive and candidate.name.lower() == spec.payload_archive.lower():
            return candidate
        target = _tmp_dir(context) / f"{spec.id}_payload_{depth}_{index}"
        context.log(f"[PROBE] payload candidate: {candidate.name}")
        try:
            _extract_archive(context, candidate, target)
        except RuntimeError as exc:
            context.log(f"[SKIP] not a readable payload archive: {candidate.name} ({exc})")
            continue
        found = _find_payload_archive(context, spec, target, depth + 1)
        if found:
            return found
        if _find_executable_dir(target, spec.executable):
            return candidate
    return None


def _unpack_browser(context: JobContext, spec: BrowserSpec, downloaded: Path) -> Path:
    """The folder holding the browser, out of whatever the vendor shipped."""
    if spec.kind == "archive":
        extracted = _tmp_dir(context) / f"{spec.id}_archive"
        _extract_archive(context, downloaded, extracted)
        payload = _find_executable_dir(extracted, spec.executable)
        if not payload:
            raise RuntimeError(f"{spec.name}: {spec.executable} was not found inside {downloaded.name}.")
        return payload

    installer_extract = _tmp_dir(context) / f"{spec.id}_installer"
    _extract_archive(context, downloaded, installer_extract)
    payload_archive = _find_payload_archive(context, spec, installer_extract)
    if not payload_archive:
        raise RuntimeError(
            f"{spec.name}: {spec.payload_archive or 'the payload archive'} was not found inside {downloaded.name}."
        )
    context.log(f"[FOUND] payload: {payload_archive.name}")
    payload_dir = _tmp_dir(context) / f"{spec.id}_payload"
    _extract_archive(context, payload_archive, payload_dir)
    if spec.payload_directory:
        named = _find_dir(payload_dir, spec.payload_directory)
        if named:
            return named
    payload = _find_executable_dir(payload_dir, spec.executable)
    if not payload:
        raise RuntimeError(f"{spec.name}: {spec.executable} was not found after unpacking {payload_archive.name}.")
    return payload


# ---------------------------------------------------------------------------
# Assembling a build
# ---------------------------------------------------------------------------


PE_MACHINES = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}


def pe_architecture(path: Path) -> str:
    """`x86`, `x64` or `arm64`, read out of the PE header. `''` if unreadable.

    This is not a detail. Chrome++ ships one `version.dll` per architecture, and
    a 32-bit browser simply does not load a 64-bit one - Windows falls back to
    the system `version.dll` and the hijack silently does not happen. Thorium
    ships `WIN32_SSE2`, which really is 32-bit, so a build that took the default
    x64 wrapper looked fine and wrote its profile into `%LOCALAPPDATA%`.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0x3C)
            pe_offset = int.from_bytes(handle.read(4), "little")
            handle.seek(pe_offset + 4)
            machine = int.from_bytes(handle.read(2), "little")
    except OSError:
        return ""
    return PE_MACHINES.get(machine, "")


def _chrome_plus_arch(context: JobContext) -> str:
    """The architecture the operator forced, or `''` for "read it off the browser"."""
    value = _param_text(context, "chrome_plus_arch", "auto").lower()
    return value if value in {"x86", "x64", "arm64"} else ""


def _chrome_plus_arch_dir(context: JobContext, archive: Path, arch: str) -> Path:
    # By the digest of the archive: a folder of an earlier release left in the temporary files is not taken for this one.
    target = _tmp_dir(context) / "chrome_plus" / _sha256(archive)[:16]
    return libraries.prepare_chrome_archive(archive, arch, target)


def _place_chrome_plus(context: JobContext, spec: BrowserSpec, portable_dir: Path, plus_archive: Path) -> str:
    """Put the wrapper matching the browser's own architecture beside it."""
    app_dir = portable_dir / "App"
    app_dir.mkdir(parents=True, exist_ok=True)
    browser_exe = app_dir / spec.executable
    detected = pe_architecture(browser_exe)
    forced = _chrome_plus_arch(context)
    arch = forced or detected or "x64"
    if forced and detected and forced != detected:
        raise RuntimeError(f"{spec.name}: browser is {detected}; requested library is {forced}. They must match.")
    else:
        context.log(f"[ARCH] {spec.name}: {detected or 'unknown'} -> Chrome++ {arch}")

    app_template = _chrome_plus_arch_dir(context, plus_archive, arch) / "App"
    for name in ("version.dll", "chrome++.ini"):
        source = app_template / name
        if not source.is_file():
            raise RuntimeError(f"Chrome++ archive has no {name} for {arch}.")
        shutil.copy2(source, app_dir / name)
    # The one check that would have caught the Thorium build: a 32-bit browser
    # with a 64-bit wrapper starts perfectly and is simply not portable.
    placed = pe_architecture(app_dir / "version.dll")
    if detected and placed and detected != placed:
        raise RuntimeError(
            f"{spec.name}: the browser is {detected} and the Chrome++ wrapper is {placed}. "
            "They must match, or the build will not be portable."
        )
    libraries.record_engine(portable_dir, "chrome_plus")
    for folder_name in ("Data", "Cache"):
        (portable_dir / folder_name).mkdir(parents=True, exist_ok=True)
    return arch


def _read_ini(path: Path) -> tuple[str, str, bytes]:
    """Text, newline and byte-order mark, so a rewrite keeps the file's shape.

    Chrome++ ships `chrome++.ini` as UTF-16 LE with LF endings. Rewriting it as
    UTF-8 leaves Chrome++ unable to read it - and it fails silently, falling back
    to defaults that look exactly like a working configuration, because the
    default `data_dir` is the same `%app%\\..\\Data` the build wants anyway.
    """
    raw = path.read_bytes()
    for bom, encoding in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"), (b"\xef\xbb\xbf", "utf-8")):
        if raw.startswith(bom):
            text = raw[len(bom):].decode(encoding)
            return text, ("\r\n" if "\r\n" in text else "\n"), bom
    text = raw.decode("utf-8", "replace")
    return text, ("\r\n" if "\r\n" in text else "\n"), b""


def _write_ini(path: Path, text: str, bom: bytes) -> None:
    encoding = {b"\xff\xfe": "utf-16-le", b"\xfe\xff": "utf-16-be"}.get(bom, "utf-8")
    path.write_bytes(bom + text.encode(encoding))


def _registry_vendor(branch: str) -> str:
    """`Google` out of `HKCU\\Software\\Google\\Chrome` - who owns the branch."""
    parts = [part for part in branch.replace("/", "\\").split("\\") if part]
    return parts[2] if len(parts) > 2 else ""


def installed_browser_path(spec: BrowserSpec) -> str:
    """Where the same browser is installed on this machine, or `''`.

    `App Paths` is what every Windows installer fills in, and it is readable
    without elevation. A portable build never writes there, so a hit means a
    real installation. The executable name alone is not enough: Chromium-Gost
    and Ungoogled ship `chrome.exe` too, and the entry an installed Google
    Chrome leaves would otherwise be read as theirs - so the path has to name
    the vendor whose registry branch is at stake.
    """
    import winreg

    vendor = _registry_vendor(spec.registry_branch)
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(
                    root,
                    rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{spec.executable}",
                    0,
                    winreg.KEY_READ | view,
                ) as key:
                    value = str(winreg.QueryValueEx(key, "")[0] or "").strip('"')
            except OSError:
                continue
            if value and (not vendor or vendor.lower() in value.lower()):
                return value
    return ""


def _registry_wipe_allowed(context: JobContext, spec: BrowserSpec, wipe_registry: bool) -> bool:
    """Whether this build may take the browser's registry branch with it.

    The branch belongs to the browser, not to the build: an installed copy of
    the same browser keeps its own settings there. Wiping it on exit would take
    those along, so where such a copy exists the cleanup is dropped and said out
    loud rather than done quietly.
    """
    if not wipe_registry or not spec.registry_branch:
        return False
    installed = installed_browser_path(spec)
    if installed:
        context.log(
            f"[GUARD] {spec.name} is installed on this machine ({installed}). "
            f"{spec.registry_branch} is shared with it, so the build leaves the branch alone."
        )
        return False
    return True


def _configure_chrome_plus_ini(
    context: JobContext,
    spec: BrowserSpec,
    portable_dir: Path,
    *,
    wipe_registry: bool,
) -> None:
    ini_path = portable_dir / "App" / "chrome++.ini"
    if not ini_path.is_file():
        raise RuntimeError(f"chrome++.ini was not found: {ini_path}")
    text, newline, bom = _read_ini(ini_path)
    branch = spec.registry_branch
    exit_command = f'reg delete "{branch}" /f;' if _registry_wipe_allowed(context, spec, wipe_registry) else ""
    # A callable replacement, because the command is a registry path: `\S` in
    # `HKCU\Software` would otherwise be read as an escape in the template.
    replaced = re.sub(
        r"(?m)^launch_on_exit=.*$",
        lambda _match: f"launch_on_exit={exit_command}",
        text,
        count=1,
    )
    if replaced == text and "launch_on_exit=" not in text:
        replaced = text.rstrip("\r\n") + f"{newline}launch_on_exit={exit_command}{newline}"
    _write_ini(ini_path, replaced, bom)
    if exit_command:
        context.log(f"[INI] launch_on_exit wipes {branch} when the browser closes")
    else:
        context.log("[INI] launch_on_exit left empty: the registry branch is kept")


# The library's own defaults also mute Google traffic, block broadcasts and
# rewrite the user agent. This file keeps to portability alone, so the browser
# behaves the way its own settings say; the rest is a hand edit away, and every
# key is documented in the library's README.
PROXY_LIBRARY_INI = libraries.PROXY_INI


def _write_proxy_library_ini(context: JobContext, portable_dir: Path, *, block_registry: bool) -> None:
    text = PROXY_LIBRARY_INI
    (portable_dir / "App" / "version.ini").write_bytes(text.replace("\n", "\r\n").encode("ascii"))
    context.log("[INI] version.ini: all optional switches are 0; Data and Cache paths are kept")


def _place_proxy_library(context: JobContext, spec: BrowserSpec, portable_dir: Path, archive: Path,
                         *, block_registry: bool) -> str:
    app_dir = portable_dir / "App"
    detected = pe_architecture(app_dir / spec.executable)
    arch = _chrome_plus_arch(context) or detected or "x64"
    files = libraries.library_files(archive, "proxy_library", arch)
    if detected and detected != arch:
        raise RuntimeError(f"Browser is {detected}; proxy library is {arch}. They must match.")
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "version.dll").write_bytes(files["version.dll"])
    _write_proxy_library_ini(context, portable_dir, block_registry=False)
    libraries.record_engine(portable_dir, "proxy_library")
    for name in ("Data", "Cache"):
        (portable_dir / name).mkdir(parents=True, exist_ok=True)
    context.log(f"[COPY] Proxy library {arch} -> {_plain(app_dir)}")
    return arch


def _portable_engine(context: JobContext) -> str:
    value = _param_text(context, "portable_engine", DEFAULT_PORTABLE_ENGINE).strip().lower()
    return value if value in PORTABLE_ENGINES else DEFAULT_PORTABLE_ENGINE


def _download_wrapper(context: JobContext, engine: str, arch: str = "x64") -> tuple[Path, str]:
    if engine == "proxy_library":
        def lookup():
            version, url = proxy_library_release()
            return version, f"proxy-library-{version or 'latest'}.zip", url
    else:
        lookup = lambda: libraries.github_release(engine, arch, github_latest_assets)
    asset, version = libraries.download_library(context, engine, arch, lookup, _download, _archives_dir(context))
    return asset.path, version


def _place_wrapper(
    context: JobContext,
    spec: BrowserSpec,
    portable_dir: Path,
    *,
    engine: str,
    archive: Path | None,
    wipe_registry: bool,
) -> str:
    if engine == "vivaldi_plus":
        detected = pe_architecture(portable_dir / "App" / spec.executable)
        arch = _chrome_plus_arch(context) or detected or "x64"
        return libraries.install_vivaldi(context, portable_dir, archive, arch, spec.executable)
    if engine == "chrome_plus":
        arch = _place_chrome_plus(context, spec, portable_dir, archive)
        _configure_chrome_plus_ini(context, spec, portable_dir, wipe_registry=wipe_registry)
        return arch
    return _place_proxy_library(context, spec, portable_dir, archive, block_registry=wipe_registry)


def _keep_library_settings(
    context: JobContext,
    spec: BrowserSpec,
    build: Path,
    staged: Path,
    engine: str,
    wipe_registry: bool | None = None,
) -> bool:
    """The settings file of the library, as the person left it in the build, into the new one.

    A library is set up by its file beside the browser - `version.ini`, `chrome++.ini`,
    `config.ini` - and a person edits it: a switch turned on, a command line, a hotkey. "Update
    DLL only" kept that file and "Update" wrote a fresh one over it with the new `App`; the same
    build lost its settings or kept them by which button was pressed (the owner, 11.10.2026, of
    the two: "as you see right" - the file is the person's, so both keep it). Only the file of
    the same library is kept: another library reads another file, and gets its own defaults.

    With `wipe_registry` given, the one line of `chrome++.ini` the form answers for -
    `launch_on_exit` - is set anew by that flag; the rest of the file stays.
    """
    if libraries.installed_engine(build) != engine:
        return False
    name = libraries.CONFIG_FILES[engine]
    try:
        kept = _managed_build_file(build, f"App/{name}")
    except RuntimeError as exc:
        context.log(f"[SKIP] {exc}")
        return False
    if not kept.is_file():
        return False
    shutil.copy2(kept, staged / "App" / name)
    if engine == "chrome_plus" and wipe_registry is not None:
        _configure_chrome_plus_ini(context, spec, staged, wipe_registry=wipe_registry)
    context.log(f"[INI] {name}: kept as it was in the build")
    return True


def _write_launcher(context: JobContext, spec: BrowserSpec, portable_dir: Path) -> Path:
    """The `.cmd` that starts the build.

    The wrapper reads the profile paths itself, so the browser needs no
    switches here.
    """
    launcher = portable_dir / f"{spec.folder}.cmd"
    launcher.write_bytes(
        ("@echo off\r\n" f'start "" /D "%~dp0App" "%~dp0App\\{spec.executable}" %*\r\n').encode("utf-8")
    )
    return launcher


def build_versions(spec: BrowserSpec, portable_dir: Path) -> tuple[str, str]:
    """`(browser version, Chrome++ version)`, read from the build itself."""
    app_dir = _long(portable_dir / "App")
    version = _file_version(app_dir / spec.executable)
    if not version and app_dir.is_dir():
        folders = sorted(
            (item.name for item in app_dir.iterdir() if item.is_dir() and re.fullmatch(r"[\d.]+", item.name)),
            key=lambda name: [int(part) for part in name.split(".") if part.isdigit()],
        )
        version = folders[-1] if folders else ""
    return version, _file_version(app_dir / "version.dll")


def _write_build_stamp(context: JobContext, spec: BrowserSpec, portable_dir: Path, extra: dict[str, Any]) -> None:
    version, plus_version = build_versions(spec, portable_dir)
    payload = {
        "product": spec.folder,
        "browser": spec.name,
        "browser_version": version,
        "chrome_plus_version": plus_version,
        "built_by": "Audion Browsers Portable",
        **extra,
    }
    (portable_dir / BUILD_STAMP_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _build_stamp(build: Path) -> dict[str, Any]:
    """What the build note says; empty when there is none or it cannot be read."""
    try:
        saved = json.loads(_long(build / BUILD_STAMP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _installer_unchanged(
    context: JobContext, spec: BrowserSpec, build: Path, current: str, seen: dict[str, str]
) -> bool:
    """Whether the vendor still serves the very installer this build was made from.

    The list of versions and the installer are two separate things, and the
    list can name a version the installer does not carry yet. A build that
    still holds what came out of the file now on the server has nothing to gain
    from another update, whatever the list says.

    `seen` keeps the server's answer for the length of one operation.
    """
    if not _permanent_installer(context, spec):
        return False
    stamp = _build_stamp(build)
    made_from = str(stamp.get("installer_etag") or "")
    # The note must describe the browser that is there: an App swapped by hand
    # is no longer what the installer produced.
    if not made_from or not same_version(str(stamp.get("browser_version") or ""), current):
        return False
    if spec.url not in seen:
        seen[spec.url] = _remote_etag(spec.url, spec.user_agent or USER_AGENT)
    return seen[spec.url] == made_from


def _safe_output_child(context: JobContext, name: str) -> Path:
    portable = _portable_root(context).resolve()
    target = (portable / name).resolve()
    if target == portable or not target.is_relative_to(portable):
        raise RuntimeError(f"Refusing unsafe portable output path: {target}")
    return target


def _publish(context: JobContext, source_dir: Path, name: str) -> Path:
    """Put a new build into the Target - only where nothing is there yet.

    It used to clear the place first, and a second Build of the same browser
    took the profile of the first one with it. A place that is taken is not this
    function's to empty: the caller replaces App inside what is there instead.
    """
    target = _safe_output_child(context, name)
    if _long(target).exists():
        raise RuntimeError(f"{target} already exists; a new build is put only where the place is free.")
    try:
        shutil.copytree(_long(source_dir), _long(target),
                        copy_function=_copy_seeing_stop(context, f"{name} was put into {target.parent}"))
    except BaseException:
        # Only what this very call has put there: the place was free a moment ago. A stop in
        # the middle of the copy goes the same way - half a build is not left in the Target.
        with contextlib.suppress(OSError):
            _remove_tree(target)
        raise
    context.log(f"[OK] FOLDER: {target}")
    return target


def _archive_draft(archive_path: Path) -> Path:
    """Where an archive is written until it is whole: beside its place, under a name of its own."""
    return archive_path.with_name(archive_path.name + ".part")


# What a build is given to another person as: the folders a browser fills itself are there, and empty.
HANDOVER_EMPTY = ("Data", "Cache")


def _handover_parts(spec: BrowserSpec, build: Path) -> list[str]:
    """What of a build goes into an archive that is "ready to hand over": what the program put there.

    The browser, its launcher, the note of the versions inside, the certificate files. Not the
    profile, not the cache, not `Old files`, not whatever a person keeps beside them. The
    archive of an updated build used to be the whole folder - bookmarks, passwords and cookies
    with it - under a tooltip that said it could be handed over at once.
    """
    wanted = ("App", f"{spec.folder}.cmd", BUILD_STAMP_FILE, CERTIFICATES_DIRECTORY)
    return [name for name in wanted if (_long(build) / name).exists()]


def _zip_dir(context: JobContext, source_dir: Path, zip_path: Path, parts: list[str] | None = None) -> Path:
    """Pack a build into a ZIP that is either whole or not there.

    It was written under its final name, the one before it deleted first: a full Target, a
    locked file or a stop left a part of an archive that looked like a finished one, and the
    archive it replaced was already gone. It is written beside its place and takes it only
    when it is whole; a stop is looked for before every file.

    With `parts` only those entries of the folder are packed, and `Data` and `Cache` go in empty.
    """
    source_dir = _long(source_dir)
    draft = _archive_draft(zip_path)

    def items() -> Iterator[Path]:
        if parts is None:
            yield from source_dir.rglob("*")
            return
        for name in parts:
            top = source_dir / name
            yield top
            if top.is_dir():
                yield from top.rglob("*")

    try:
        with zipfile.ZipFile(draft, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for item in items():
                _check_cancelled(context, f"{zip_path.name} was packed")
                if item.is_dir():
                    archive.writestr(item.relative_to(source_dir.parent).as_posix().rstrip("/") + "/", b"")
                elif item.is_file():
                    archive.write(item, item.relative_to(source_dir.parent))
            if parts is not None:
                for name in HANDOVER_EMPTY:
                    archive.writestr(f"{source_dir.name}/{name}/", b"")
        draft.replace(zip_path)
    except BaseException:
        with contextlib.suppress(OSError):
            draft.unlink(missing_ok=True)
        raise
    context.log(f"[OK] ZIP: {zip_path} ({zip_path.stat().st_size:,} bytes)"
                + ("; the profile and the cache are not in it" if parts is not None else ""))
    return zip_path


def _archive_build(context: JobContext, source_dir: Path, base_name: str, parts: list[str] | None = None) -> Path:
    """Pack a build. `parts` names what of the folder goes in - an updated build is packed without its profile."""
    archive_format = _param_text(context, "archive_format", "zip").lower()
    if archive_format == "7z":
        archive_path = _portable_root(context) / f"{base_name}.7z"
        draft = _archive_draft(archive_path)
        skeleton = _tmp_dir(context) / f"handover-{uuid.uuid4().hex}"
        try:
            draft.unlink(missing_ok=True)
            # The kind is named, so the name of the draft does not have to end in ".7z".
            packed = [source_dir.name] if parts is None else [f"{source_dir.name}\\{name}" for name in parts]
            _run_7z(context, ["a", "-t7z", "-mx=9", str(draft), *packed], cwd=source_dir.parent,
                    doing=f"{archive_path.name} was packed")
            if parts is not None:
                # The folders the browser fills itself, empty: added from a skeleton of the build.
                for name in HANDOVER_EMPTY:
                    (skeleton / source_dir.name / name).mkdir(parents=True, exist_ok=True)
                _run_7z(context, ["a", "-t7z", str(draft), source_dir.name], cwd=skeleton,
                        doing=f"{archive_path.name} was packed")
            draft.replace(archive_path)
        except BaseException:
            with contextlib.suppress(OSError):
                draft.unlink(missing_ok=True)
            raise
        finally:
            with contextlib.suppress(OSError):
                _remove_tree(skeleton)
        context.log(f"[OK] 7Z: {archive_path} ({archive_path.stat().st_size:,} bytes)"
                    + ("; the profile and the cache are not in it" if parts is not None else ""))
        return archive_path
    return _zip_dir(context, source_dir, _portable_root(context) / f"{base_name}.zip", parts)


# ---------------------------------------------------------------------------
# Russian Trusted CA block
# ---------------------------------------------------------------------------


def _certificates_dir(context: JobContext, portable_dir: Path | None = None) -> Path:
    root = portable_dir if portable_dir is not None else _output_root(context)
    path = root / CERTIFICATES_DIRECTORY
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_certificate_wrappers(context: JobContext, target: Path) -> list[Path]:
    """Two .cmd files, because trusting a CA is an act, not a side effect.

    They say plainly where the certificate goes: into Windows, into this user's
    account - not into the browser folder they are shipped in. `-user` keeps it
    out of the machine store, so no administrator rights are needed and no other
    account is affected, and the second file takes it back by thumbprint.
    """
    install_lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal EnableExtensions",
        "echo Ministry of Digital Development certificates go into Windows, into this",
        "echo user account - not into the browser folder. Every Chromium browser on this",
        "echo account then trusts them. Administrator rights are not needed.",
        "echo Undo: Uninstall-Russian-Trusted-CA.cmd",
        "echo.",
    ]
    remove_lines = list(install_lines[:3]) + [
        "echo Removing the Ministry of Digital Development certificates from this Windows",
        "echo user account. Exactly these two, matched by thumbprint.",
        "echo.",
    ]
    for item in RUSSIAN_TRUSTED_CERTIFICATES:
        install_lines.append(f'certutil -addstore -user -f "{item["store"]}" "%~dp0{item["name"]}"')
        remove_lines.append(f'certutil -delstore -user "{item["store"]}" {item["thumbprint"]}')
    for lines in (install_lines, remove_lines):
        lines.extend(["", "echo.", "pause", "endlocal"])

    written: list[Path] = []
    for name, lines in (
        ("Install-Russian-Trusted-CA.cmd", install_lines),
        ("Uninstall-Russian-Trusted-CA.cmd", remove_lines),
    ):
        path = target / name
        path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
        written.append(path)
    context.log(f"[OK] certificate wrappers: {_plain(target)}")
    return written


def _certificate_thumbprint(path: Path) -> str:
    """The thumbprint Windows names a certificate by - SHA-1 of the certificate itself - read from its file.

    A `.crt` is the certificate as it is (DER) or its base64 between two marker lines (PEM).
    `''` for a file that is neither, and for one that holds more than a single certificate:
    `certutil -addstore` would add every one of them, and only the first would have been compared.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    head, tail = b"-----BEGIN CERTIFICATE-----", b"-----END CERTIFICATE-----"
    if head in raw:
        if raw.count(head) != 1 or raw.count(tail) != 1 or raw.split(tail, 1)[1].strip():
            return ""
        try:
            raw = base64.b64decode(b"".join(raw.split(head, 1)[1].split(tail, 1)[0].split()), validate=True)
        except (ValueError, binascii.Error):
            return ""
    if not raw.startswith(b"\x30"):
        return ""
    return hashlib.sha1(raw).hexdigest().upper()  # noqa: S324 - the name Windows gives a certificate, not a safeguard


def _certificate_file(context: JobContext, item: dict[str, str], folder: Path, reserve: Path | None = None) -> Path:
    """The file of a certificate in `folder`, proved to be the certificate it is named after.

    `reserve` is a folder that may hold the file already - the build that is being updated:
    a file proved there is copied, and the server is not asked again.


    The file was taken by its name: whatever lay in a shared Target as
    `russian_trusted_root_ca.crt` went into the user's Root store, and only afterwards was the
    expected thumbprint looked for - the operation failed, the foreign root stayed, and Revoke
    removes by the two known thumbprints alone. A download cut short was reused the same way,
    for good. Now the thumbprint is read from the file before anything is done with it; a file
    that is something else is moved aside under a name of its own - it may be somebody's - and
    the certificate is fetched anew.
    """
    wanted = str(item["thumbprint"]).upper()
    path = folder / str(item["name"])
    if path.is_file() and _certificate_thumbprint(path) != wanted:
        aside = path.with_name(f"{path.name}.not-{wanted[:8]}")
        number = 2
        while aside.exists():
            aside = path.with_name(f"{path.name}.not-{wanted[:8]} ({number})")
            number += 1
        path.rename(aside)
        context.log(f"[WARN] {path.name} in {_plain(folder)} is not {item['subject']}: set aside as {aside.name}.")
    kept = reserve / path.name if reserve is not None else None
    if not path.is_file() and kept is not None and kept.is_file() and _certificate_thumbprint(kept) == wanted:
        shutil.copy2(kept, path)
        context.log(f"[CACHE] {item['subject']}: {path.name} as it lies in the build")
    if not path.is_file():
        _download(context, str(item["url"]), path, str(item["subject"]), use_cache=False)
        found = _certificate_thumbprint(path)
        if found != wanted:
            path.unlink(missing_ok=True)
            raise RuntimeError(
                f"{item['url']} did not give {item['subject']}: the file has the thumbprint "
                f"{found or '(not a single certificate)'}, the certificate is {wanted}. Nothing was installed."
            )
    return path


def _certificates_in(build: Path) -> bool:
    """Whether the certificate files lie in the build."""
    folder = _long(build) / CERTIFICATES_DIRECTORY
    return all((folder / str(item["name"])).is_file() for item in RUSSIAN_TRUSTED_CERTIFICATES)


def _put_certificates(context: JobContext, spec: BrowserSpec, staged: Path, build: Path) -> None:
    """The certificate files, already fetched and proved, from the work folder into the build."""
    try:
        target = _long(build) / CERTIFICATES_DIRECTORY
        target.mkdir(exist_ok=True)
        for item in _long(staged).iterdir():
            if item.is_file():
                _managed_build_file(build, f"{CERTIFICATES_DIRECTORY}/{item.name}")
                shutil.copy2(item, target / item.name)
    except (OSError, RuntimeError) as exc:
        # The browser in the build is already the new one: this is a line of the log, not a failed update.
        context.log(f"[WARN] {spec.name}: the certificate files could not be put into the build ({exc}).")


def stage_certificates(context: JobContext, portable_dir: Path | None = None, reserve: Path | None = None) -> dict[str, object]:
    """Put the certificate files and their two wrappers into the build."""
    target = _certificates_dir(context, portable_dir)
    files = [str(_certificate_file(context, item, target, reserve)) for item in RUSSIAN_TRUSTED_CERTIFICATES]
    wrappers = _write_certificate_wrappers(context, target)
    return {"directory": str(target), "files": files, "wrappers": [str(path) for path in wrappers]}


def _certificate_present(thumbprint: str, store: str) -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["certutil", "-store", "-user", store, thumbprint],
        capture_output=True,
        timeout=60,
        **hidden_subprocess_kwargs(),
    )
    return result.returncode == 0


def certificates_state(context: JobContext) -> dict[str, object]:
    """Whether each certificate is currently trusted for this user."""
    state = {}
    for item in RUSSIAN_TRUSTED_CERTIFICATES:
        present = _certificate_present(str(item["thumbprint"]), str(item["store"]))
        state[str(item["subject"])] = present
        context.log(f"[CERT] {item['subject']} ({item['store']}): {'installed' if present else 'not installed'}")
    context.log("[INFO] Yandex Browser needs none of this: it trusts that CA out of the box.")
    context.progress(1.0)
    return {"certificates": state}


def install_certificates(context: JobContext) -> dict[str, object]:
    """Add the Russian Trusted CA files to the *current user's* store.

    Chromium consults the user's `Root` and `CA` stores alongside its own root
    store, so this is enough for a portable build - and it needs no elevation,
    touches no other account, and is undone by `remove_certificates`.
    """
    if os.name != "nt":
        raise RuntimeError("Certificate installation is a Windows-only operation.")
    target = _certificates_dir(context)
    results: list[dict[str, object]] = []
    # Every file is proved before the first of them reaches a store: a set installed by half is worse than none.
    paths = [_certificate_file(context, item, target) for item in RUSSIAN_TRUSTED_CERTIFICATES]
    for index, (item, path) in enumerate(zip(RUSSIAN_TRUSTED_CERTIFICATES, paths), start=1):
        before = _certificate_present(str(item["thumbprint"]), str(item["store"]))
        context.log(f"[RUN] certutil -addstore -user -f {item['store']} {path.name}")
        result = subprocess.run(
            ["certutil", "-addstore", "-user", "-f", str(item["store"]), str(path)],
            capture_output=True,
            timeout=120,
            env=utf8_subprocess_env(),
            **hidden_subprocess_kwargs(),
        )
        for line in _windows_lines(result.stdout):
            context.log(line)
        if not _certificate_present(str(item["thumbprint"]), str(item["store"])):
            raise RuntimeError(
                f"{item['subject']} did not reach the {item['store']} store "
                f"(certutil exit code {result.returncode})."
            )
        context.log(f"[OK] {item['subject']} is trusted for the current user.")
        results.append({"subject": item["subject"], "store": item["store"], "was_present": before})
        context.progress(index / len(RUSSIAN_TRUSTED_CERTIFICATES))
    _write_certificate_wrappers(context, target)
    return {"installed": results, "directory": str(target), "scope": "current user"}


def remove_certificates(context: JobContext) -> dict[str, object]:
    """Take the Russian Trusted CA files back out of the current user's store."""
    if os.name != "nt":
        raise RuntimeError("Certificate removal is a Windows-only operation.")
    results: list[dict[str, object]] = []
    for index, item in enumerate(RUSSIAN_TRUSTED_CERTIFICATES, start=1):
        if not _certificate_present(str(item["thumbprint"]), str(item["store"])):
            context.log(f"[SKIP] {item['subject']} is not in the {item['store']} store.")
            results.append({"subject": item["subject"], "removed": False, "reason": "not installed"})
            context.progress(index / len(RUSSIAN_TRUSTED_CERTIFICATES))
            continue
        context.log(f"[RUN] certutil -delstore -user {item['store']} {item['thumbprint']}")
        result = subprocess.run(
            ["certutil", "-delstore", "-user", str(item["store"]), str(item["thumbprint"])],
            capture_output=True,
            timeout=120,
            env=utf8_subprocess_env(),
            **hidden_subprocess_kwargs(),
        )
        for line in _windows_lines(result.stdout):
            context.log(line)
        if _certificate_present(str(item["thumbprint"]), str(item["store"])):
            raise RuntimeError(
                f"{item['subject']} is still in the {item['store']} store "
                f"(certutil exit code {result.returncode})."
            )
        context.log(f"[OK] {item['subject']} removed.")
        results.append({"subject": item["subject"], "removed": True})
        context.progress(index / len(RUSSIAN_TRUSTED_CERTIFICATES))
    return {"removed": results, "scope": "current user"}


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def install_portable_7zip(context: JobContext) -> dict[str, object]:
    before = _seven_zip_version(context)
    if before:
        context.log(f"[OK] Portable 7-Zip already available: {before}")
        return {"installed": False, "version": before, "path": str(_seven_zip_path(context))}

    script = context.paths.root / "install" / "Install-Portable-7Zip.cmd"
    if not script.exists():
        raise RuntimeError(f"Portable 7-Zip installer was not found: {script}")
    context.log(f"[RUN] {script} /NOPAUSE")
    result = subprocess.run(
        [str(script), "/NOPAUSE"],
        cwd=str(context.paths.root),
        capture_output=True,
        timeout=900,
        env=utf8_subprocess_env(),
        **hidden_subprocess_kwargs(),
    )
    for line in _windows_lines(result.stdout):
        context.log(line)
    if result.returncode != 0:
        raise RuntimeError(f"Portable 7-Zip install failed with exit code {result.returncode}.")
    version = _seven_zip_version(context)
    if not version:
        raise RuntimeError("Portable 7-Zip installer finished, but 7za.exe is not usable.")
    context.log(f"[OK] Portable 7-Zip installed: {version}")
    context.progress(1.0)
    return {"installed": True, "version": version, "path": str(_seven_zip_path(context))}


def _selected_specs(context: JobContext, key: str = "browsers") -> list[BrowserSpec]:
    selected = _param_list(context, key)
    if not selected:
        raise RuntimeError("Select at least one browser.")
    return [browser(item) for item in selected]


def _claims_executable(spec: BrowserSpec, executable: Path) -> bool | None:
    """Whether the executable found on disk is this browser.

    Three registry entries are called `chrome.exe`, so the file name settles
    nothing. `True` or `False` when the binary can be told apart by what it says
    about itself; `None` when it cannot - the name is shared and unverified, or
    the file says nothing at all - and only the folder name can decide.
    """
    product = adoption.file_string(executable, "ProductName").strip().lower()
    if spec.product_name:
        return (product == spec.product_name.lower()) if product else None
    # A file that names itself, and not by the name this browser's family goes by, is another
    # browser altogether: no folder name and no tick makes it this one.
    if spec.family_product and product and product != spec.family_product.lower():
        return False
    rivals = [
        item for item in BROWSERS
        if item.id != spec.id and item.executable.lower() == spec.executable.lower()
    ]
    if not rivals:
        return True
    if any(item.product_name and item.product_name.lower() == product for item in rivals):
        return False
    return None


@dataclass(frozen=True)
class FoundBuild:
    """One build to act on: which browser it is, and where it lies."""

    spec: BrowserSpec
    layout: adoption.Layout
    # The only build of this browser in the Source. Such a build takes the
    # standard folder name; several of one browser keep the names they came with,
    # because the names are how the person tells them apart.
    alone: bool = True
    # Found in the published Target, not in the Source.
    published: bool = False

    @property
    def home(self) -> Path:
        return self.layout.home

    @property
    def label(self) -> str:
        return self.spec.name if self.alone else f"{self.spec.name} ({self.layout.home.name})"


def _requested_specs(context: JobContext, key: str = "browsers") -> tuple[list[BrowserSpec], bool]:
    """The browsers ticked - or every browser, when nothing is.

    Nothing ticked means "whatever is in the Source": the builds say what they
    are themselves, so naming the browser first is one step too many. The flag
    tells the two cases apart.
    """
    selected = _param_list(context, key)
    if selected:
        return [browser(item) for item in selected], False
    return list(BROWSERS), True


def _build_containers(context: JobContext) -> list[Path]:
    """Folders that hold builds and can never be one themselves.

    The program, its own `input` and `output`, the Target and what is published in it. The
    Source is not one of them by being the Source: a person points it at the folder that holds
    the builds or straight at a build, and both are meant. The console door hands the Source
    over as `paths.input`, and while that stood in this list a build named as the Source was
    never a build - its browser branch was taken for one, without the profile beside it, and
    Update rearranged that branch and left the real profile outside.
    """
    root = context.paths.root
    return [root, root / "input", root / "output", _output_root(context), _portable_root(context)]


def _accepts_build(spec: BrowserSpec, layout: adoption.Layout, explicit: Iterable[str] = ()) -> bool:
    """Whether this folder is a build of this browser.

    A build in the standard layout under its own name is what this program
    made, and it is taken on that alone, as it always was. A build that arrived
    from elsewhere has to prove what it is: by what its executable says, then by
    the folder name, and last by the tick - when the person ticked exactly one
    browser that this executable could be.
    """
    home = layout.home
    named = home.name.lower() == spec.folder.lower()
    if layout.standard and named:
        return True
    executable = layout.browser_dir / spec.executable
    verdict = _claims_executable(spec, executable)
    if verdict is not None:
        return verdict
    if named:
        return True
    ticked = set(explicit)
    if spec.id not in ticked:
        return False
    if any(home.name.lower() == item.folder.lower() for item in BROWSERS if item.id != spec.id):
        return False
    rivals = [
        item for item in BROWSERS
        if item.id != spec.id
        and item.id in ticked
        and item.executable.lower() == spec.executable.lower()
        and _claims_executable(item, executable) is not False
    ]
    return not rivals


def _source_scan(context: JobContext, specs: list[BrowserSpec]) -> dict[str, list[adoption.Layout]]:
    """Every build under the Source, by executable name. One pass per name."""
    source = _input_root(context)
    scan: dict[str, list[adoption.Layout]] = {}
    if not source.is_dir():
        return scan
    containers = _build_containers(context)
    executables = [item.executable for item in BROWSERS]
    for spec in specs:
        key = spec.executable.lower()
        if key in scan:
            continue
        layouts: list[adoption.Layout] = []
        for layout in adoption.builds_in(source, spec.executable, executables, containers):
            home = layout.home
            if home.parent == home or any(adoption.same_path(home, item) for item in containers):
                context.log(
                    f"[SKIP] {spec.executable} lies loose in {home}. "
                    "Put the build into a folder of its own."
                )
                continue
            layouts.append(layout)
        scan[key] = layouts
    return scan


def _published_build(context: JobContext, spec: BrowserSpec) -> adoption.Layout | None:
    """What this program built itself, in the Target - the last resort, as before."""
    scope = _portable_root(context) / spec.folder
    if not scope.is_dir():
        return None
    executables = [item.executable for item in BROWSERS]
    for layout in adoption.builds_in(scope, spec.executable, executables, _build_containers(context)):
        if adoption.same_path(layout.home, scope) and _accepts_build(spec, layout):
            return layout
    return None


def _find_builds(
    context: JobContext,
    specs: list[BrowserSpec],
    explicit: Iterable[str] = (),
) -> tuple[list[FoundBuild], list[adoption.Layout]]:
    """Every build to act on, and the folders holding a browser nobody could name.

    People bring browsers here to have them updated, and they are rarely ones
    this program made: the browser sits in `Chrome\\` rather than `App\\`, the
    folder is called something else, and there may be several at once - each in
    a folder of its own. So builds are found by their executables, anywhere
    under the Source; the published Target is looked at only for a browser the
    Source has no build of, exactly as before.
    """
    ticked = list(explicit)
    scan = _source_scan(context, specs)
    taken: list[Path] = []
    found: list[FoundBuild] = []
    for spec in specs:
        mine = [
            layout for layout in scan.get(spec.executable.lower(), [])
            if not any(adoption.same_path(layout.home, item) for item in taken)
            and _accepts_build(spec, layout, ticked)
        ]
        mine.sort(key=lambda layout: (layout.home.name.lower() != spec.folder.lower(), layout.home.name.lower()))
        if mine:
            taken.extend(layout.home for layout in mine)
            found.extend(FoundBuild(spec, layout, alone=len(mine) == 1) for layout in mine)
            continue
        published = _published_build(context, spec)
        if published is not None:
            found.append(FoundBuild(spec, published, alone=True, published=True))
    unclaimed: list[adoption.Layout] = []
    for layouts in scan.values():
        for layout in layouts:
            known = [*taken, *(item.home for item in unclaimed)]
            if not any(adoption.same_path(layout.home, item) for item in known):
                unclaimed.append(layout)
    return found, unclaimed


def _report_unclaimed(
    context: JobContext,
    specs: list[BrowserSpec],
    unclaimed: list[adoption.Layout],
    explicit: Iterable[str] = (),
) -> None:
    """Say why a folder with a browser in it was left alone."""
    asked = {spec.id for spec in specs}
    ticked = set(explicit)
    for layout in unclaimed:
        home = layout.home
        if any(home.name.lower() == item.folder.lower() for item in BROWSERS if item.id not in asked):
            continue  # a build of a browser that was not asked for
        spec = next((item for item in specs if (layout.browser_dir / item.executable).is_file()), None)
        if spec is None:
            continue
        product = adoption.file_string(layout.browser_dir / spec.executable, "ProductName")
        candidates = [
            item for item in BROWSERS
            if item.executable.lower() == spec.executable.lower() and not item.product_name
        ]
        # A browser that names itself and is none of those this program builds: no tick makes it one.
        if product and not any(_claims_executable(item, layout.browser_dir / spec.executable) is not False for item in BROWSERS
                               if item.executable.lower() == spec.executable.lower()):
            context.log(
                f"[SKIP] {home.name}: {spec.executable} there is {product}, a browser this program does not build. "
                "It is left as it is."
            )
            continue
        names = [item.name for item in candidates if item.id not in ticked]
        hint = f" Tick {' or '.join(names)} to update it." if names else ""
        context.log(
            f"[SKIP] {home.name}: {spec.executable} there is {product or 'not identified'}, "
            f"and that does not say which browser it is.{hint}"
        )


def _adoptable_build(context: JobContext, spec: BrowserSpec) -> adoption.Layout | None:
    """The first build of this browser, in whatever layout it arrived."""
    found, _unclaimed = _find_builds(context, [spec])
    return found[0].layout if found else None


def _adopted_home(context: JobContext, spec: BrowserSpec, layout: adoption.Layout, alone: bool = True) -> Path:
    """Where the build ends up once it is in the standard layout.

    The folder takes the standard name, so the next update finds it without
    searching. Two cases keep the name they have: the Source itself - renaming
    the very folder the person pointed at would leave the Source pointing at
    nothing - and one of several builds of the same browser, which are told
    apart by exactly those names.
    """
    home = layout.home
    if not alone or adoption.same_path(home, _input_root(context)) or home.name.lower() == spec.folder.lower():
        return home
    return home.parent / spec.folder


def _is_standard_build(context: JobContext, spec: BrowserSpec, layout: adoption.Layout, alone: bool = True) -> bool:
    return layout.standard and adoption.same_path(_adopted_home(context, spec, layout, alone), layout.home)


def _adoption_blocker(context: JobContext, spec: BrowserSpec, layout: adoption.Layout, alone: bool = True) -> str:
    """Why this build cannot be rearranged right now - asked before any download."""
    if adoption.in_use(layout.browser_dir / spec.executable):
        return "the browser is running. Close it and run the update again"
    target = _adopted_home(context, spec, layout, alone)
    if not adoption.same_path(target, layout.home) and target.exists():
        return (
            f"the build would be renamed to '{target.name}', and that folder already exists "
            f"beside it ({target.parent}). Move one of them away first"
        )
    return ""


def _adopt_build(
    context: JobContext,
    spec: BrowserSpec,
    layout: adoption.Layout,
    alone: bool = True,
    browser_names: Iterable[str] | None = None,
) -> Path:
    """Bring a foreign build to the standard layout and return its folder.

    `browser_names` are the entries of the new browser. They tell the old
    browser from whatever else lies beside it; with none given, only the
    executable itself is known to be the browser's.
    """
    names = list(browser_names) if browser_names is not None else [spec.executable]
    try:
        adoption.normalize(layout, context.log, _remove_tree, names)
        target = _adopted_home(context, spec, layout, alone)
        if not adoption.same_path(target, layout.home):
            layout.home.rename(target)
            context.log(f"[ADOPT] folder: {layout.home.name} -> {target.name}")
        return target
    except OSError as exc:
        raise RuntimeError(
            f"{spec.name}: the build could not be rearranged because a file in it is in use. "
            f"Close the browser and run the update again. ({exc})"
        ) from exc


def _builds_of_ticked(
    context: JobContext, specs: list[BrowserSpec]
) -> tuple[list[FoundBuild], list[dict[str, object]]]:
    """The builds of the ticked browsers, and a line for each browser that has none.

    Every operation that works on a build already there finds it the same way - by the
    executable, anywhere under the Source, then in the Target. "Update DLL only" and "Add to
    build" used to look in the Source itself and right under it, by the standard folder name
    alone: the builds Check had just listed and Update had just updated - one folder deeper,
    or under a name of their own - were answered with "No existing builds found".
    """
    explicit = [spec.id for spec in specs]
    found, unclaimed = _find_builds(context, specs, explicit)
    _report_unclaimed(context, specs, unclaimed, explicit)
    skipped: list[dict[str, object]] = []
    present = {item.spec.id for item in found}
    for spec in specs:
        if spec.id not in present:
            context.log(f"[SKIP] {spec.name}: no existing build.")
            skipped.append({"browser": spec.name, "reason": "no build"})
    return found, skipped


def _published_wrapper_version(context: JobContext, engine: str) -> str:
    arch = _chrome_plus_arch(context) or "x64"
    lookup = proxy_library_release if engine == "proxy_library" else lambda: libraries.github_release(engine, arch, github_latest_assets)
    return libraries.published_version(context, engine, arch, lookup, _archives_dir(context))


def check_updates(context: JobContext) -> dict[str, object]:
    """What is published against what is on disk. Downloads nothing."""
    specs, automatic = _requested_specs(context)
    explicit = [] if automatic else [spec.id for spec in specs]
    engine = _portable_engine(context)
    plus_version = _published_wrapper_version(context, engine)
    found, unclaimed = _find_builds(context, specs, explicit)
    _report_unclaimed(context, specs, unclaimed, explicit)

    # One line per build. A ticked browser with no build still gets its line -
    # what was published is worth knowing on its own. With nothing ticked only
    # what is actually there is listed, and an empty Source falls back to what
    # every browser has published.
    targets: list[tuple[BrowserSpec, FoundBuild | None]] = []
    for spec in specs:
        builds = [item for item in found if item.spec.id == spec.id]
        if builds:
            targets.extend((spec, item) for item in builds)
        elif not automatic or not found:
            targets.append((spec, None))

    rows: list[dict[str, object]] = []
    sources: dict[str, tuple[str, str]] = {}
    etags: dict[str, str] = {}
    for index, (spec, item) in enumerate(targets, start=1):
        _check_cancelled(context, f"every build was checked ({index - 1} of {len(targets)} were)")
        label = item.label if item else spec.name
        if spec.id not in sources:
            try:
                sources[spec.id] = (_browser_source(context, spec)[0], "")
            except OperationCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - one vendor that does not answer must not take the other rows along
                # A network that fails is an `OSError` (`URLError`, a timeout), not a `RuntimeError`:
                # caught by its narrow name, one unreachable server ended the whole check with a traceback.
                sources[spec.id] = ("", f"{exc.__class__.__name__}: {exc}" if not isinstance(exc, RuntimeError) else str(exc))
        published, problem = sources[spec.id]
        if problem:
            context.log(f"[FAIL] {label}: {problem}")
            rows.append({"browser": spec.name, "error": problem})
            context.progress(index / len(targets))
            continue
        build = item.home if item else None
        # A build from elsewhere: the browser is not in `App`, so its version is
        # read where the executable actually is, and its wrapper is not ours.
        foreign = item is not None and not _is_standard_build(context, spec, item.layout, item.alone)
        if foreign:
            current, current_plus = _file_version(item.layout.browser_dir / spec.executable), ""
        else:
            current, current_plus = build_versions(spec, build) if build else ("", "")
        needs = bool(published) and not same_version(published, current, spec.version_match)
        # The list of versions can run ahead of the installer. A build made from
        # the installer still being served cannot be brought any further.
        ahead = bool(needs and build and not foreign and _installer_unchanged(context, spec, build, current, etags))
        needs = needs and not ahead
        plus_needs = foreign or (
            bool(plus_version)
            and (not same_version(plus_version, current_plus) or bool(build and libraries.needs_refresh(build, engine)))
        )
        state = "not built yet" if not build else ("version unknown" if not published else ("update available" if needs else "up to date"))
        context.log(
            f"[{label}] published {published or '?'} / build {current or '-'} -> {state}"
            + (f"; the installer still carries {current}" if ahead else "")
            + ("; wrapper update available" if build and plus_needs and not foreign else "")
            + ("; not in the standard layout - Update rearranges it and keeps the profile" if foreign else "")
        )
        rows.append(
            {
                "browser": spec.name,
                "published": published,
                "build": current,
                "wrapper": current_plus,
                "chrome_plus": current_plus,
                "update": needs if published else None,
                "installer_unchanged": ahead,
                "wrapper_update": plus_needs,
                "chrome_plus_update": plus_needs,
                "foreign_layout": foreign,
                "path": str(build) if build else "",
            }
        )
        context.progress(index / len(targets))
    return {
        "portable_engine": engine,
        "wrapper_published": plus_version,
        "chrome_plus_published": plus_version,
        "browsers": rows,
    }


def _replace_app_in_place(context: JobContext, spec: BrowserSpec, target: Path, staged_app: Path) -> None:
    """Swap `App` inside the build the person pointed at, keeping the rest.

    An update belongs where the build already lives: someone picks a folder as
    the Source, presses Update, and expects *that* folder to become current. A
    build published into the Target instead would leave the picked one untouched
    and old, with no hint why.

    `Data` and `Cache` are never moved - they simply stay where they are - and
    the new `App` is assembled in full before anything is touched, so a failed
    download cannot leave a half-replaced browser behind.

    It is done in two steps, because the build may lie on another volume than
    the work folder, and moving a folder across volumes is a copy that can stop
    halfway: the new App is first brought beside the old one, and only then do
    the two change places.
    """
    incoming = _bring_app_beside(context, spec, target, staged_app)
    _swap_app(context, spec, target, incoming.name)


def _bring_app_beside(context: JobContext, spec: BrowserSpec, home: Path, staged_app: Path) -> Path:
    """Put the new App onto the build's own volume, under a name of its own.

    The slow and fallible part of an update, and the build is not touched by
    it: when the copy fails, what is there is exactly what was there.
    """
    # The build may itself lie deep; everything in it is handled in the long form.
    home = _long(home)
    if adoption.in_use(home / "App" / spec.executable):
        raise RuntimeError(
            f"{spec.name}: the build is in use, so App cannot be replaced. "
            "Close the browser and run the update again."
        )
    _clear_leftovers(context, spec, home)
    incoming = home / f"App.incoming-{os.getpid()}"
    _remove_tree(incoming)
    try:
        # On another volume the move is a copy of thousands of files: a stop is looked for at each.
        shutil.move(str(_long(staged_app)), str(incoming),
                    copy_function=_copy_seeing_stop(context, f"the new {spec.name} was copied into {_plain(home)}"))
    except OSError as exc:
        with contextlib.suppress(OSError):
            _remove_tree(incoming)
        raise RuntimeError(
            f"{spec.name}: the new browser could not be copied into {_plain(home)}, "
            f"and the build is as it was. ({_copy_failure(exc)})"
        ) from exc
    except BaseException:
        # A stop in the middle of the copy: the build is as it was, and half a browser is not left in it.
        with contextlib.suppress(OSError):
            _remove_tree(incoming)
        raise
    return incoming


def _process_alive(pid: int) -> bool:
    if pid <= 0 or os.name != "nt":
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _clear_leftovers(context: JobContext, spec: BrowserSpec, home: Path) -> None:
    """What a run that was ended hard left in the build: a new browser never put in, an old one never removed.

    Each run names its work folders by its own process and cleared only those, so the folders
    of a run that was killed - hundreds of megabytes each - stayed in the person's build for
    good. A folder of a process that is no longer alive is nobody's work. A build caught
    between the two renames of a swap has no `App` at all: its old browser is given back first.
    """
    try:
        found = [item for item in home.iterdir() if item.is_dir()
                 and item.name.lower().startswith(("app.incoming-", "app.replaced-"))]
    except OSError:
        return
    stale = []
    for item in found:
        tail = item.name.rsplit("-", 1)[-1]
        if tail.isdigit() and int(tail) != os.getpid() and not _process_alive(int(tail)):
            stale.append(item)
    app = home / "App"
    for item in sorted(stale, key=lambda entry: entry.name.lower()):
        try:
            if not app.exists() and item.name.lower().startswith("app.replaced-"):
                item.rename(app)
                context.log(f"[RECOVER] {spec.name}: an update was cut short; the browser it had set aside is App again.")
                continue
            _remove_tree(item)
            context.log(f"[CLEAN] {spec.name}: {item.name} of a run that did not finish was removed from {_plain(home)}.")
        except OSError as exc:
            context.log(f"[WARN] {spec.name}: {item.name} of a run that did not finish could not be removed ({exc}).")


def _copy_failure(exc: OSError) -> str:
    """Why a copy failed, in a line: `shutil` reports a tree as a list of every file."""
    if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list):
        reasons: list[str] = []
        for entry in exc.args[0]:
            reason = str(entry[-1]) if isinstance(entry, tuple) and entry else str(entry)
            if reason not in reasons:
                reasons.append(reason)
        return "; ".join(reasons[:3])
    return str(exc)


def _swap_app(context: JobContext, spec: BrowserSpec, home: Path, incoming_name: str) -> None:
    """The old App out and the new one in: two renames on one volume."""
    home = _long(home)
    app_dir, incoming = home / "App", home / incoming_name
    retired = home / f"App.replaced-{os.getpid()}"
    if app_dir.exists():
        try:
            app_dir.rename(retired)
        except OSError as exc:
            with contextlib.suppress(OSError):
                _remove_tree(incoming)
            raise RuntimeError(
                f"{spec.name}: the build is in use, so App could not be replaced. "
                f"Close the browser and run the update again. ({exc})"
            ) from exc
    try:
        incoming.rename(app_dir)
    except OSError:
        if retired.exists():
            retired.rename(app_dir)
        with contextlib.suppress(OSError):
            _remove_tree(incoming)
        raise
    try:
        _remove_tree(retired)
    except OSError as exc:
        # The new browser is in place; an old file that would not go is no failure of the update.
        context.log(f"[WARN] {spec.name}: the old browser was left as {retired.name} in {_plain(home)} ({exc})")
    context.log(f"[UPDATE] App replaced in place: {_plain(home)}")


def _managed_build_file(build: Path, relative: str) -> Path:
    """Refuse linked files/directories before modifying a known build file."""
    target = build / relative
    for entry in (target, *target.parents):
        if entry.exists() or entry.is_symlink():
            attributes = getattr(entry.lstat(), "st_file_attributes", 0)
            if entry.is_symlink() or attributes & 0x400:
                raise RuntimeError(f"Linked build path cannot be changed: {entry}")
        if entry == build:
            break
    if not target.resolve().is_relative_to(build.resolve()):
        raise RuntimeError(f"Build file escapes its folder: {target}")
    if target.exists() and not target.is_file():
        raise RuntimeError(f"Expected a build file: {target}")
    return target


def _disable_yandex_updater(context: JobContext, spec: BrowserSpec, build: Path) -> list[str]:
    if spec.id != "yandex" or not _param_bool(context, "disable_yandex_updater", True):
        return []
    removed: list[str] = []
    for updater in (build / "App").rglob("*"):
        if updater.name.casefold() != YANDEX_UPDATER_EXECUTABLE or not updater.is_file():
            continue
        relative = str(updater.relative_to(build))
        try:
            _managed_build_file(build, relative)
        except RuntimeError as exc:
            context.log(f"[SKIP] {exc}")
            continue
        updater.unlink()
        removed.append(relative)
        context.log(f"[CLEAN] Yandex updater removed: {relative}")
    return removed


def _replace_wrapper_in_place(
    context: JobContext, spec: BrowserSpec, build: Path, staged: Path, engine: str,
) -> None:
    """Commit known wrapper files on the build's volume, with rollback on failure."""
    config = libraries.CONFIG_FILES[engine]
    desired = ["App/version.dll", f"App/{config}", "App/portable-library.json", f"{spec.folder}.cmd", BUILD_STAMP_FILE]
    obsolete = [f"App/{name}" for name in ("chrome++.ini", "version.ini") if name != config]
    if libraries.installed_engine(build) == "vivaldi_plus" and engine != "vivaldi_plus":
        obsolete.append("App/config.ini")
    changes: list[tuple[Path, Path | None]] = []
    for relative in desired + obsolete:
        target = _managed_build_file(build, relative)
        source = staged / relative if relative in desired else None
        if source is not None:
            if not source.is_file():
                raise RuntimeError(f"Staged wrapper file is missing: {source}")
            if target.is_file() and target.read_bytes() == source.read_bytes():
                continue
        elif not target.exists():
            continue
        changes.append((target, source))
    _check_cancelled(context, f"the library of {spec.name} was changed")
    transaction = build / f".audion-wrapper-update-{uuid.uuid4().hex}"
    transaction.mkdir()
    committed: list[tuple[Path, Path | None]] = []
    try:
        prepared: list[tuple[Path, Path | None, Path | None]] = []
        for index, (target, source) in enumerate(changes):
            backup = transaction / f"{index}.old" if target.exists() else None
            if backup is not None:
                shutil.copy2(target, backup)
            replacement = transaction / f"{index}.new" if source is not None else None
            if replacement is not None:
                shutil.copy2(source, replacement)
            prepared.append((target, replacement, backup))
        for target, replacement, backup in prepared:
            _managed_build_file(build, str(target.relative_to(build)))
            _check_cancelled(context, f"every library file of {spec.name} was replaced")
            if replacement is None:
                target.unlink()
            else:
                replacement.replace(target)
            committed.append((target, backup))
    except Exception as exc:
        rollback_errors: list[str] = []
        for target, backup in reversed(committed):
            try:
                _managed_build_file(build, str(target.relative_to(build)))
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    backup.replace(target)
            except (OSError, RuntimeError) as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise RuntimeError(f"{spec.name}: wrapper update failed; recovery files kept in {transaction}. {rollback_errors}") from exc
        _remove_tree(transaction)
        if isinstance(exc, OperationCancelled):
            # Stopped half way and put back whole: a stop, not a failure of this browser.
            raise OperationCancelled(f"{exc} {spec.name}: the library was put back as it was.") from None
        raise RuntimeError(f"{spec.name}: wrapper update failed and was rolled back. Close the browser and retry. ({exc})") from exc
    _remove_tree(transaction)
    context.log(f"[UPDATE] Wrapper replaced in place: {build}")


def update_libraries_selected(context: JobContext) -> dict[str, object]:
    """Update only the selected library; never resolve or download the browser."""
    specs = _selected_specs(context)
    engine = _portable_engine(context)
    updated: list[dict[str, object]] = []
    failed: list[dict[str, str]] = []
    archives: dict[str, tuple[Path, str]] = {}
    guard_asked = False
    guards = contextlib.ExitStack()
    found, skipped = _builds_of_ticked(context, specs)
    try:
        for index, item in enumerate(found, start=1):
            spec, build, label = item.spec, item.home, item.label
            try:
                _check_cancelled(context, f"the library of {label} was touched")
                if not item.layout.standard:
                    # The library lies beside the browser in `App`, and a build from elsewhere has
                    # no `App` yet: Update makes one of it, and keeps the profile while it does.
                    where = os.path.relpath(item.layout.browser_dir, build)
                    context.log(
                        f"[SKIP] {label}: the browser lies {'right in the folder' if where == '.' else f'in {where!r}'}, "
                        "not in App. Update brings the build to the standard layout; the library alone is replaced there."
                    )
                    skipped.append({"browser": spec.name, "reason": "not in the standard layout", "folder": build.name})
                    continue
                executable = _managed_build_file(build, f"App/{spec.executable}")
                detected, forced = pe_architecture(executable), _chrome_plus_arch(context)
                if not detected:
                    raise RuntimeError(f"{label}: cannot read the browser architecture.")
                if forced and forced != detected:
                    raise RuntimeError(f"{label}: browser is {detected}; requested library is {forced}. They must match.")
                libraries.require_architecture(engine, detected)
                old_version, old_wrapper = build_versions(spec, build)
                # One guard for the whole batch, opened when the first browser gets as far as needing it:
                # a guard per browser was a question of UAC per browser. It covers the Source and the
                # Target - every place a build can lie in - the very folders the safety net takes back.
                if not guard_asked:
                    guard_asked = True
                    if _param_bool(context, "guard_defender", True):
                        guards.enter_context(_defender_guard(context, [_output_root(context), _input_root(context)]))
                if detected not in archives:
                    archives[detected] = _download_wrapper(context, engine, detected)
                archive, release = archives[detected]
                staged = _tmp_dir(context) / f"wrapper-{uuid.uuid4().hex}"
                (staged / "App").mkdir(parents=True)
                shutil.copy2(executable, staged / "App" / spec.executable)
                _place_wrapper(context, spec, staged, engine=engine, archive=archive, wipe_registry=False)
                _keep_library_settings(context, spec, build, staged, engine)
                _write_launcher(context, spec, staged)
                old_launcher = _managed_build_file(build, f"{spec.folder}.cmd")
                if libraries.installed_engine(build) == engine and old_launcher.is_file():
                    shutil.copy2(old_launcher, staged / old_launcher.name)
                stamp = _managed_build_file(build, BUILD_STAMP_FILE)
                metadata: dict[str, Any] = {}
                if stamp.is_file():
                    try:
                        saved = json.loads(stamp.read_text(encoding="utf-8"))
                        metadata = saved if isinstance(saved, dict) else {}
                    except (OSError, ValueError):
                        context.log("[INFO] Previous build note could not be read.")
                _write_build_stamp(context, spec, staged, {
                    **metadata, "mode": "wrapper_update", "portable_engine": engine,
                    "browser_version": old_version, "wrapper_release": release,
                    "previous_wrapper_version": old_wrapper,
                })
                _replace_wrapper_in_place(context, spec, build, staged, engine)
                updated.append({"browser": spec.name, "id": spec.id, "build": str(build),
                                "version": old_version, "previous_wrapper_version": old_wrapper,
                                "wrapper_version": build_versions(spec, build)[1] or release,
                                "portable_engine": engine})
                context.log(f"[DONE] {label}: only the wrapper updated.")
            except OperationCancelled as exc:
                # A stop is not one browser that failed: the batch does not go on to the next,
                # and the run is reported as stopped, not as done.
                done = ", ".join(str(item["browser"]) for item in updated) or "nothing"
                raise OperationCancelled(f"{exc} Updated before that: {done}.") from None
            except Exception as exc:  # noqa: BLE001 - keep the other builds available
                context.log(f"[FAIL] {label}: {exc}")
                failed.append({"browser": spec.name, "folder": build.name, "error": str(exc)})
            finally:
                context.progress(index / len(found))
    finally:
        try:
            if not _param_bool(context, "keep_temp", False):
                _discard_tmp(context)
        finally:
            # Released last: what is cleared from the work folder is the library too.
            guards.close()
    if not updated:
        elsewhere = [str(item["folder"]) for item in skipped if item.get("reason") == "not in the standard layout"]
        raise RuntimeError(
            "; ".join(item["error"] for item in failed)
            or (f"No build in the standard layout: {', '.join(elsewhere)} - run Update first, it brings a build "
                "to App, Data and Cache and keeps the profile." if elsewhere else "")
            or "No existing builds found. Select the build or its parent as Source."
        )
    return {"updated": updated, "skipped": skipped, "failed": failed, "portable_engine": engine}


def _build_one(
    context: JobContext,
    spec: BrowserSpec,
    *,
    engine: str,
    plus_archive: Path | None,
    plus_version: str,
    wipe_registry: bool,
    with_certificates: bool,
    package_archive: bool,
    keep_data_from: Path | None = None,
    adopt: FoundBuild | None = None,
    archive_name: str = "",
) -> dict[str, object]:
    """Assemble one browser and put it in place.

    With `keep_data_from` the new App goes into that build and its profile
    stays; without it a new build is published into the Target. `archive_name`
    names the archive of a build that is updated - several builds of one
    browser must not all be packed under the browser's name.
    """
    published, url, filename = _browser_source(context, spec)
    context.log(f"[{spec.name}] published: {published or 'unknown'}")
    permanent = _permanent_installer(context, spec)
    asset = _download(
        context,
        url,
        _archives_dir(context) / _safe_name(filename),
        f"{spec.name} {published}".strip(),
        user_agent=spec.user_agent or USER_AGENT,
        # A file kept under a name that carries no version ("...-latest.exe": the vendor's
        # address stopped naming one) says nothing of what is inside: unless the server can
        # vouch for it by its ETag, it is fetched anew every time.
        use_cache=not bool(_custom_installer_url(context, spec)) and (bool(published) or permanent),
        verify_cache=permanent,
    )

    work = _tmp_dir(context) / spec.folder
    _remove_tree(work)
    work.mkdir(parents=True, exist_ok=True)

    _check_cancelled(context, f"{spec.name} was unpacked")
    try:
        payload = _unpack_browser(context, spec, asset.path)
    except RuntimeError:
        # A file that does not open is not kept for the next run to trip over: it was taken
        # from the cache under its name alone, every time, until somebody deleted it by hand.
        _forget_download(context, asset.path)
        raise
    _prune_archives(context, spec, asset.path)
    app_dir = work / "App"
    app_dir.mkdir(parents=True, exist_ok=True)
    _check_cancelled(context, f"{spec.name} was assembled")
    context.log(f"[COPY] {payload.name} -> App")
    _copy_tree_contents(context, payload, app_dir, f"{spec.name} was assembled")
    if not (app_dir / spec.executable).exists():
        raise RuntimeError(f"{spec.name}: {spec.executable} is missing from App after the copy.")
    removed_updaters = _disable_yandex_updater(context, spec, work)

    if plus_archive is None:
        detected = pe_architecture(app_dir / spec.executable)
        forced = _chrome_plus_arch(context)
        if forced and detected and forced != detected:
            raise RuntimeError(f"Browser is {detected}; requested library is {forced}. They must match.")
        plus_archive, plus_version = _download_wrapper(context, engine, forced or detected or "x64")

    _place_wrapper(
        context,
        spec,
        work,
        engine=engine,
        archive=plus_archive,
        wipe_registry=wipe_registry,
    )
    # A build of ours that is updated keeps the settings of its library. One brought from
    # elsewhere does not: its wrapper goes with its old browser, and the new one comes whole.
    if keep_data_from is not None and adopt is None:
        _keep_library_settings(context, spec, keep_data_from, work, engine, wipe_registry)

    # The certificate files are fetched here, into the work folder, while a stop or a server
    # that does not answer still costs nothing. They used to be fetched after the browser had
    # been swapped: the build was new by then, the run said "failed" or "nothing updated", the
    # note of the build kept the old version - and the next run passed the build by as current.
    kept_certificates = _long(keep_data_from) / CERTIFICATES_DIRECTORY if keep_data_from is not None else None
    certificates = stage_certificates(context, work, kept_certificates) if with_certificates else {}

    # Everything so far happened in the work folder. From here on somebody's build
    # is changed, so this is where a cancel still costs nothing.
    _check_cancelled(context, f"{spec.name} was put in place")
    if keep_data_from is not None:
        # The new App is brought beside the build first - the one step that can
        # fail halfway - and the build is left alone until that has worked.
        brought_into = adopt.layout.home if adopt is not None else keep_data_from
        incoming = _bring_app_beside(context, spec, brought_into, work / "App")
        try:
            _check_cancelled(context, f"{spec.name} was put in place")
            # A build made elsewhere is rearranged only now, with the new App
            # already beside it: a failed download leaves it as it was brought in.
            if adopt is not None:
                # Asked once more: the question before the download is minutes old, and the
                # browser of a build from elsewhere does not lie where the copy above looked.
                if adoption.in_use(adopt.layout.browser_dir / spec.executable):
                    raise RuntimeError(
                        f"{spec.name}: the browser was started while the new one was being fetched. "
                        "The build is as it was. Close the browser and run the update again."
                    )
                fresh = [item.name for item in incoming.iterdir()]
                keep_data_from = _adopt_build(context, spec, adopt.layout, adopt.alone, fresh)
            # Update: the freshly assembled App takes the place of the old one, and
            # the Target folder is left for new builds only.
            _swap_app(context, spec, keep_data_from, incoming.name)
        except BaseException:
            for folder in {str(brought_into), str(keep_data_from)}:
                with contextlib.suppress(OSError):
                    _remove_tree(Path(folder) / incoming.name)
            raise
        # The build may itself lie deep; what is written into it goes the long way too.
        home = _long(keep_data_from)
        for name in ("Data", "Cache"):
            (home / name).mkdir(exist_ok=True)
    else:
        home = work

    # Past the swap nothing waits for the network and nothing looks for a stop: the browser
    # in the build is the new one, and what is written here only says so.
    _write_launcher(context, spec, home)
    if certificates and keep_data_from is not None:
        _put_certificates(context, spec, work / CERTIFICATES_DIRECTORY, keep_data_from)
    _write_build_stamp(
        context,
        spec,
        home,
        {
            "mode": "update" if keep_data_from is not None else "build",
            "adopted_from_another_layout": adopt is not None,
            "source_url": asset.url,
            "installer_etag": asset.etag,
            "portable_engine": engine,
            "chrome_plus_release": plus_version,
            # What lies in the build, not what was ticked: files put there by an earlier run stay.
            "certificates_staged": _certificates_in(home),
            "yandex_updater_disabled": bool(spec.id == "yandex" and _param_bool(context, "disable_yandex_updater", True)),
        },
    )
    version, plus_in_build = build_versions(spec, home)
    if keep_data_from is not None:
        # The archive of a build somebody uses is the build as it is handed over: without the profile.
        artifact = (_archive_build(context, home, archive_name or spec.folder, _handover_parts(spec, home))
                    if package_archive else home)
    else:
        artifact = _archive_build(context, work, spec.folder) if package_archive else _publish(context, work, spec.folder)
    # Only a folder can lie too deep; an archive is unpacked wherever it is taken.
    placed = home if keep_data_from is not None else (None if package_archive else artifact)
    return {
        "browser": spec.name,
        "id": spec.id,
        "deep_paths": _warn_deep_build(context, spec.name, placed) if placed else 0,
        "version": version or published,
        "portable_engine": engine,
        "wrapper_version": plus_in_build or plus_version,
        "chrome_plus_version": plus_in_build or plus_version,
        "artifact": str(_plain(artifact)),
        "certificates": bool(certificates),
        "registry_wiped_on_exit": bool(engine == "chrome_plus" and wipe_registry and spec.registry_branch),
        "registry_writes_blocked": False,
        "removed_updaters": removed_updaters,
    }


def _built_before(context: JobContext, spec: BrowserSpec) -> Path | None:
    """The folder a Build of this browser would land in, when it is already there.

    Somebody built the browser, used it, and pressed Build again. The folder
    holds a profile by now, so the new browser goes into it the way an update
    does - App is replaced and nothing else is touched.
    """
    target = _safe_output_child(context, spec.folder)
    if not _long(target).exists():
        return None
    context.log(
        f"[KEEP] {spec.name}: {target} is already there. Only its App is replaced; "
        "Data, Cache and everything else in it stay."
    )
    return target


def _unique_name(name: str, taken: set[str]) -> str:
    """`name`, or `name (2)` and so on, whichever nobody in `taken` has yet."""
    candidate, number = name, 2
    while candidate.casefold() in taken:
        candidate = f"{name} ({number})"
        number += 1
    taken.add(candidate.casefold())
    return candidate


def build_selected(context: JobContext) -> dict[str, object]:
    """Build every selected browser; one failure does not stop the rest."""
    _require_7zip(context)
    specs = _selected_specs(context)
    keep_temp = _param_bool(context, "keep_temp", False)
    wipe_registry = _param_bool(context, "wipe_registry_on_exit", False)
    with_certificates = _param_bool(context, "stage_certificates", False)
    package_archive = _param_bool(context, "package_archive", False)
    guard_defender = _param_bool(context, "guard_defender", True)

    # Chrome++'s version.dll is a routine Defender false positive; every browser's
    # download and assembly happen under output, so the guard keeps that one folder
    # out of Defender's reach for the whole batch. It is a no-op where Defender is
    # not running.
    guard = (
        _defender_guard(context, _output_root(context))
        if guard_defender
        else contextlib.nullcontext()
    )
    with guard:
        engine = _portable_engine(context)
        plus_archive, plus_version = None, ""

        built: list[dict[str, object]] = []
        failed: list[dict[str, str]] = []
        try:
            for index, spec in enumerate(specs, start=1):
                _check_cancelled(context, f"{spec.name} was started")
                context.log(f"[{spec.name}] building {index}/{len(specs)}")
                try:
                    built.append(
                        _build_one(
                            context,
                            spec,
                            engine=engine,
                            plus_archive=plus_archive,
                            plus_version=plus_version,
                            wipe_registry=wipe_registry,
                            with_certificates=with_certificates,
                            package_archive=package_archive,
                            keep_data_from=None if package_archive else _built_before(context, spec),
                        )
                    )
                    context.log(f"[DONE] {spec.name}")
                except OperationCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - one browser must not stop the batch
                    context.log(f"[FAIL] {spec.name}: {exc}")
                    failed.append({"browser": spec.name, "error": str(exc)})
                finally:
                    context.progress(index / len(specs))
        except OperationCancelled as exc:
            done = ", ".join(str(item["browser"]) for item in built) or "nothing"
            raise OperationCancelled(f"{exc} Built before that: {done}.") from None
        finally:
            if not keep_temp:
                _discard_tmp(context)
    if failed and not built:
        raise RuntimeError("; ".join(f"{item['browser']}: {item['error']}" for item in failed))
    return {"built": built, "failed": failed, "output": str(_portable_root(context))}


def update_selected(context: JobContext) -> dict[str, object]:
    """Refresh App in existing builds, keeping Data and Cache.

    Every build found in the Source is updated in one run - each lies in a
    folder of its own, whatever browser it is. Ticking browsers narrows that
    down; with nothing ticked the builds themselves say what they are.
    """
    _require_7zip(context)
    specs, automatic = _requested_specs(context)
    explicit = [] if automatic else [spec.id for spec in specs]
    keep_temp = _param_bool(context, "keep_temp", False)
    wipe_registry = _param_bool(context, "wipe_registry_on_exit", False)
    with_certificates = _param_bool(context, "stage_certificates", False)
    package_archive = _param_bool(context, "package_archive", False)
    force = _param_bool(context, "force_update", False)
    guard_defender = _param_bool(context, "guard_defender", True)

    # version.dll lands in the workspace (under output) and in each build being
    # updated, which lives under input or output. Excluding both roots covers
    # every place a build can be found in. No-op where Defender is not running.
    guard = (
        _defender_guard(context, [_output_root(context), _input_root(context)])
        if guard_defender
        else contextlib.nullcontext()
    )
    with guard:
        engine = _portable_engine(context)
        plus_archive, plus_version = None, _published_wrapper_version(context, engine)

        updated: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        failed: list[dict[str, str]] = []
        etags: dict[str, str] = {}
        archive_names: set[str] = set()
        found, unclaimed = _find_builds(context, specs, explicit)
        _report_unclaimed(context, specs, unclaimed, explicit)
        if not automatic:
            # A ticked browser with nothing to update is said out loud, as before.
            present = {item.spec.id for item in found}
            for spec in specs:
                if spec.id not in present:
                    context.log(f"[SKIP] {spec.name}: no build found in input or output.")
                    skipped.append({"browser": spec.name, "reason": "no build"})
        try:
            for index, item in enumerate(found, start=1):
                spec, layout, label = item.spec, item.layout, item.label
                build = item.home
                _check_cancelled(context, f"{label} was started")
                context.log(f"[{label}] updating {index}/{len(found)}")
                try:
                    foreign = not _is_standard_build(context, spec, layout, item.alone)
                    if foreign:
                        # Asked before anything is downloaded: there is no point in
                        # fetching a browser that cannot be put in place.
                        blocker = _adoption_blocker(context, spec, layout, item.alone)
                        if blocker:
                            raise RuntimeError(f"{build}: {blocker}.")
                        current, current_plus = _file_version(layout.browser_dir / spec.executable), ""
                        browser_at = os.path.relpath(layout.browser_dir, build)
                        profile_at = os.path.relpath(layout.profile_dir, build) if layout.profile_dir else ""
                        context.log(
                            f"[ADOPT] {label}: {build} is not in the standard layout "
                            f"(browser {'right in the folder' if browser_at == '.' else f'in {browser_at!r}'}, "
                            f"{f'profile in {profile_at!r}' if profile_at else 'no profile yet'}). "
                            "It is updated and rearranged; the profile stays."
                        )
                    else:
                        current, current_plus = build_versions(spec, build)
                    published, _url, _filename = _browser_source(context, spec)
                    matches = bool(published) and same_version(published, current, spec.version_match)
                    # Same reasoning as in the check: a newer number on the list is
                    # no update while the installer it would come from is unchanged.
                    ahead = bool(
                        published and not matches and not foreign
                        and _installer_unchanged(context, spec, build, current, etags)
                    )
                    if (
                        not foreign
                        and (matches or ahead)
                        and same_version(plus_version, current_plus)
                        and not libraries.needs_refresh(build, engine)
                        and not force
                        and not _custom_installer_url(context, spec)
                    ):
                        removed_updaters = _disable_yandex_updater(context, spec, build)
                        if with_certificates and not _certificates_in(build):
                            # Ticked, and the build is current: the files still go in - a build
                            # passed by as current never got them otherwise.
                            try:
                                for name in [str(entry["name"]) for entry in RUSSIAN_TRUSTED_CERTIFICATES]:
                                    _managed_build_file(build, f"{CERTIFICATES_DIRECTORY}/{name}")
                                stage_certificates(context, build)
                            except OperationCancelled:
                                raise
                            except Exception as exc:  # noqa: BLE001 - the build is current either way
                                context.log(f"[WARN] {label}: the certificate files were not put into the build ({exc}).")
                        context.log(
                            f"[SKIP] {label}: {current} is current, wrapper {current_plus or '-'} too."
                            + (f" {published} is on the list, but the installer still carries {current}." if ahead else "")
                        )
                        skipped.append({"browser": spec.name, "reason": "already current", "version": current,
                                        "folder": build.name, "removed_updaters": removed_updaters})
                        continue
                    # An archive is named after the build's own folder, as it will be
                    # called once updated: two builds of one browser are two archives.
                    packed_as = ""
                    if package_archive:
                        final = _adopted_home(context, spec, layout, item.alone) if foreign else build
                        packed_as = _unique_name(final.name, archive_names)
                    result = _build_one(
                        context,
                        spec,
                        engine=engine,
                        plus_archive=plus_archive,
                        plus_version=plus_version,
                        wipe_registry=wipe_registry,
                        with_certificates=with_certificates,
                        package_archive=package_archive,
                        keep_data_from=build,
                        adopt=item if foreign else None,
                        archive_name=packed_as,
                    )
                    updated.append(result)
                    # The number reported is the one read from the browser that was
                    # put in place. The published one is only what the vendor
                    # announced, and the file it serves can lag behind that.
                    installed = str(result.get("version") or "")
                    context.log(f"[DONE] {label}: {current or '-'} -> {installed or published}")
                    if published and installed and not same_version(published, installed, spec.version_match):
                        context.log(
                            f"[INFO] {label}: the installer carries {installed}; "
                            f"the published version is {published}."
                        )
                except OperationCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - one build must not stop the batch
                    context.log(f"[FAIL] {label}: {exc}")
                    failed.append({"browser": spec.name, "folder": build.name, "error": str(exc)})
                finally:
                    context.progress(index / len(found))
        except OperationCancelled as exc:
            done = ", ".join(Path(str(item["artifact"])).name for item in updated) or "nothing"
            raise OperationCancelled(f"{exc} Updated before that: {done}.") from None
        finally:
            if not keep_temp:
                _discard_tmp(context)
    if not found:
        # Nothing was found - whether nothing was ticked or what was ticked has no build: a
        # green "finished" here would read as "updated", which is exactly what did not happen.
        # (A browser ticked beside others that do have builds is passed by with a line, as before.)
        asked = "" if automatic else " of " + ", ".join(spec.name for spec in specs)
        raise RuntimeError(
            f"No portable build{asked} was found in {_input_root(context)} or in {_portable_root(context)}. "
            "A build is a folder holding the browser; put it into the Source in a folder of its own."
        )
    if failed and not updated and not any(item["reason"] == "already current" for item in skipped):
        raise RuntimeError("; ".join(item["error"] for item in failed))
    return {"updated": updated, "skipped": skipped, "failed": failed, "output": str(_portable_root(context))}


def stage_certificates_selected(context: JobContext) -> dict[str, object]:
    """Save CA files in existing builds without changing Windows trust."""
    key = "certificate_browsers" if "certificate_browsers" in context.operation.parameters else "browsers"
    specs = _selected_specs(context, key)
    staged: list[dict[str, object]] = []
    failed: list[dict[str, str]] = []
    found, skipped = _builds_of_ticked(context, specs)
    for index, item in enumerate(found, start=1):
        spec, build, label = item.spec, item.home, item.label
        try:
            _check_cancelled(context, f"certificate files were put into the build of {label}")
            for name in [str(item["name"]) for item in RUSSIAN_TRUSTED_CERTIFICATES] + ["Install-Russian-Trusted-CA.cmd", "Uninstall-Russian-Trusted-CA.cmd"]:
                _managed_build_file(build, f"{CERTIFICATES_DIRECTORY}/{name}")
            staged.append({"browser": spec.name, "build": str(build), **stage_certificates(context, build)})
        except OperationCancelled as exc:
            # A stop is not one browser that failed: the batch does not go on to the next.
            done = ", ".join(str(item["browser"]) for item in staged) or "nothing"
            raise OperationCancelled(f"{exc} Done before that: {done}.") from None
        except Exception as exc:  # noqa: BLE001 - a failed source/build need not stop the batch
            context.log(f"[FAIL] {label}: {exc}")
            failed.append({"browser": spec.name, "folder": build.name, "error": str(exc)})
        finally:
            context.progress(index / len(found))
    if not staged:
        raise RuntimeError("; ".join(item["error"] for item in failed) or "No existing builds found. Select the build or its parent as Source.")
    return {"staged": staged, "skipped": skipped, "failed": failed}


def browser_options(root: Path | None = None) -> list[dict[str, str]]:
    """Checkbox options for the manifest, straight from the registry."""
    return [
        {"value": spec.id, "label": spec.name, "label_ru": spec.name, "icon": spec.icon, "tone": spec.tone}
        for spec in BROWSERS
    ]
