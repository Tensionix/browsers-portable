"""Download unchanged official browser packages; never install or patch them."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen
import hashlib
import json
import os
import re
import struct
import tempfile
import zipfile
import zlib

from system_core.core.jobs import JobContext


USER_AGENT = "Audion-Browsers-Portable/Ready-Downloads"
DUCK_INSTALLER = "https://staticcdn.duckduckgo.com/windows-desktop-browser/installer/DuckDuckGo.Installer.exe"


@dataclass(frozen=True)
class ReadyBrowser:
    id: str
    name: str
    source_page: str
    download_host: str
    architectures: tuple[str, ...]
    package_kind: str


READY_BROWSERS = (
    ReadyBrowser("cent", "Cent Browser", "https://www.centbrowser.com/history.html",
                 "static.centbrowser.com", ("x64", "x86"), "portable_sfx"),
    ReadyBrowser("vivaldi", "Vivaldi", "https://vivaldi.com/download/",
                 "downloads.vivaldi.com", ("x64", "x86", "arm64"), "standalone_installer"),
    ReadyBrowser("librewolf", "LibreWolf", "https://librewolf.net/installation/windows/",
                 "dl.librewolf.net", ("x64", "arm64"), "portable_zip"),
    ReadyBrowser("duckduckgo", "DuckDuckGo", "https://duckduckgo.com/windows",
                 "staticcdn.duckduckgo.com", ("x64",), "installer"),
)
READY_BY_ID = {browser.id: browser for browser in READY_BROWSERS}


@dataclass(frozen=True)
class ReadyRelease:
    browser: str
    version: str
    architecture: str
    filename: str
    url: str
    source_page: str
    package_kind: str


class DownloadCancelled(RuntimeError):
    pass


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.links.extend(value for key, value in attrs if key.lower() == "href" and value)


def browser_options(*_args, **_kwargs) -> list[dict[str, str]]:
    return [{"value": browser.id,
             "label": browser.name + (" (installer)" if browser.id == "duckduckgo" else ""),
             "label_ru": browser.name + (" (установщик)" if browser.id == "duckduckgo" else "")}
            for browser in READY_BROWSERS]


def _package_pattern(browser_id: str, architecture: str) -> str:
    version = r"(?P<version>\d+(?:\.\d+){1,3}(?:-\d+)?)"
    if browser_id == "cent":
        suffix = "_x64" if architecture == "x64" else ""
        return rf"centbrowser_{version}{suffix}_portable\.exe"
    if browser_id == "vivaldi":
        suffix = {"x64": r"\.x64", "x86": "", "arm64": r"\.arm64"}[architecture]
        return rf"Vivaldi\.{version}{suffix}\.exe"
    arch = {"x64": "x86_64", "arm64": "arm64"}[architecture]
    return rf"librewolf-{version}-windows-{arch}-portable\.zip"


def parse_release(browser_id: str, architecture: str, page: str) -> ReadyRelease:
    browser = READY_BY_ID[browser_id]
    if architecture not in browser.architectures:
        raise RuntimeError(f"{browser.name}: no official {architecture} package; supported: {', '.join(browser.architectures)}.")
    if browser_id == "duckduckgo":
        raise RuntimeError("DuckDuckGo uses the official installer endpoint, not a versioned HTML link.")
    parser = _Links()
    parser.feed(page)
    candidates: list[ReadyRelease] = []
    for link in parser.links:
        url = urljoin(browser.source_page, link)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != browser.download_host or parsed.username or parsed.password:
            continue
        if browser_id == "cent" and not parsed.path.startswith("/win_stable/"):
            continue
        if browser_id == "vivaldi" and not parsed.path.startswith("/stable/"):
            continue
        filename = unquote(parsed.path.rsplit("/", 1)[-1])
        match = re.fullmatch(_package_pattern(browser_id, architecture), filename, re.IGNORECASE)
        if match:
            candidates.append(ReadyRelease(browser.id, match["version"], architecture, filename,
                                           url, browser.source_page, browser.package_kind))
    if not candidates:
        raise RuntimeError(f"{browser.name}: official {architecture} download was not found on {browser.source_page}.")
    return max(candidates, key=lambda release: tuple(int(part) for part in re.findall(r"\d+", release.version)))


def _read_page(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError(f"Official download page is too large: {url}")
        return raw.decode("utf-8-sig")


def resolve_release(browser_id: str, architecture: str) -> ReadyRelease:
    browser = READY_BY_ID[browser_id]
    if architecture not in browser.architectures:
        raise RuntimeError(f"{browser.name}: no official {architecture} package; supported: {', '.join(browser.architectures)}.")
    if browser_id == "duckduckgo":
        # The vendor's Windows page uses an unversioned EXE endpoint. Always
        # fetch a temporary copy and compare its hash with the saved installer.
        return ReadyRelease(browser.id, "current", architecture, "DuckDuckGo.Installer.exe",
                            DUCK_INSTALLER, browser.source_page, browser.package_kind)
    return parse_release(browser_id, architecture, _read_page(browser.source_page))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_package(path: Path, release: ReadyRelease) -> None:
    if release.package_kind == "portable_zip":
        try:
            with zipfile.ZipFile(path) as archive:
                names = {item.filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
                         for item in archive.infolist() if not item.is_dir()}
                if "librewolf.exe" not in names:
                    raise RuntimeError("The portable ZIP has no librewolf.exe.")
                if archive.testzip() is not None:
                    raise RuntimeError("The portable ZIP failed its CRC check.")
        except (zipfile.BadZipFile, zlib.error) as exc:
            raise RuntimeError(f"Invalid portable ZIP: {exc}") from exc
    else:
        with path.open("rb") as source:
            header = source.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise RuntimeError("The download is not a Windows executable.")
            source.seek(struct.unpack_from("<I", header, 60)[0])
            if source.read(4) != b"PE\0\0":
                raise RuntimeError("The download has no valid Windows PE signature.")


def _check_cancelled(context: JobContext) -> None:
    if context.cancelled():
        raise DownloadCancelled("Official browser download cancelled.")


def _metadata_path(context: JobContext, target: Path) -> Path:
    try:
        cache_key = "project:" + str(target.resolve().relative_to(context.paths.root.resolve()))
    except ValueError:
        cache_key = str(target.resolve())
    if os.name == "nt":
        cache_key = cache_key.casefold()
    return context.paths.report / "official_download_cache" / (hashlib.sha256(cache_key.encode("utf-8")).hexdigest() + ".json")


def download_release(context: JobContext, release: ReadyRelease, folder: Path,
                     progress_start: float, progress_end: float) -> dict[str, object]:
    _check_cancelled(context)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / release.filename
    meta = _metadata_path(context, target)
    saved_digest = ""
    replace_metadata = False
    if target.exists():
        try:
            cached = json.loads(meta.read_text(encoding="utf-8"))
            if not isinstance(cached, dict):
                raise ValueError("invalid saved metadata")
            if cached["url"] != release.url or cached["sha256"] != _sha256(target):
                raise ValueError("source or SHA256 differs")
            _validate_package(target, release)
        except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
            raise RuntimeError(f"Existing file was kept unchanged: {target}. Its saved download metadata is missing or invalid ({exc}).") from exc
        saved_digest = cached["sha256"]
        replace_metadata = True
        if release.browser != "duckduckgo":
            context.log(f"[CACHE] {target}")
            context.progress(progress_end)
            return asdict(release) | {"artifact": str(target), "sha256": saved_digest, "size": target.stat().st_size, "cached": True}
        context.log(f"[CHECK] Fetch the current DuckDuckGo installer and compare SHA256. / Сравнение SHA256 с актуальным установщиком DuckDuckGo.")

    elif meta.exists():
        try:
            cached = json.loads(meta.read_text(encoding="utf-8"))
            if (not isinstance(cached, dict) or cached.get("url") != release.url
                    or not re.fullmatch(r"[0-9a-f]{64}", str(cached.get("sha256", "")))):
                raise ValueError("invalid download record")
            replace_metadata = True
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Existing download record was kept unchanged: {meta} ({exc}).") from exc
    context.log(f"[DOWNLOAD] {READY_BY_ID[release.browser].name} {release.version} / {release.architecture}")
    context.log(f"[URL] {release.url}")
    part: Path | None = None
    meta_part: Path | None = None
    try:
        _check_cancelled(context)
        request = Request(release.url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=30) as response, tempfile.NamedTemporaryFile(
                mode="wb", dir=folder, prefix=release.filename + ".", suffix=".part", delete=False) as destination:
            part = Path(destination.name)
            expected = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            last_percent = -10
            while True:
                _check_cancelled(context)
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                destination.write(chunk)
                downloaded += len(chunk)
                if expected:
                    fraction = min(1.0, downloaded / expected)
                    context.progress(progress_start + (progress_end - progress_start) * fraction)
                    percent = int(fraction * 100)
                    if percent >= last_percent + 10:
                        context.log(f"[DOWNLOAD] {percent}% ({downloaded:,}/{expected:,} bytes)")
                        last_percent = percent
            if not downloaded or (expected and downloaded != expected):
                raise RuntimeError(f"Incomplete download: {downloaded} bytes; expected {expected or 'a nonempty file'}.")
        _check_cancelled(context)
        _validate_package(part, release)
        digest = _sha256(part)
        if saved_digest:
            if not target.is_file() or _sha256(target) != saved_digest:
                raise RuntimeError(f"The saved installer changed during download and was kept: {target}")
            if digest == saved_digest:
                context.log(f"[CACHE] SHA256 is unchanged: {target}")
                context.progress(progress_end)
                return asdict(release) | {"artifact": str(target), "sha256": digest, "size": downloaded, "cached": True}
        result = asdict(release) | {"artifact": str(target), "sha256": digest, "size": downloaded, "cached": False}
        meta.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=meta.parent, suffix=".part", encoding="utf-8", delete=False) as saved:
            meta_part = Path(saved.name)
            saved.write(json.dumps(result | {"downloaded_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2))
        # On Windows rename refuses an existing destination, preserving a file
        # that another process might have created during the download.
        if saved_digest:
            part.replace(target)
            context.log(f"[UPDATE] SHA256 changed; saved the current DuckDuckGo installer: {target}")
        elif target.exists():
            raise RuntimeError(f"A file appeared during download and was kept: {target}")
        else:
            part.rename(target)
        if replace_metadata:
            meta_part.replace(meta)
        else:
            meta_part.rename(meta)
        context.log(f"[OK] {target}")
        context.log(f"[SHA256] {digest}")
        context.progress(progress_end)
        return result
    finally:
        if part is not None:
            part.unlink(missing_ok=True)
        if meta_part is not None:
            meta_part.unlink(missing_ok=True)


def download_selected(context: JobContext) -> dict[str, object]:
    selected = context.operation.parameters.get("ready_browsers", [])
    selected = [selected] if isinstance(selected, str) else list(selected or [])
    selected = list(dict.fromkeys(str(value).strip().lower() for value in selected))
    if not selected:
        raise RuntimeError("Choose at least one browser to download.")
    if any(browser_id not in READY_BY_ID for browser_id in selected):
        raise RuntimeError("Supported official downloads: Cent Browser, Vivaldi, LibreWolf and DuckDuckGo.")
    architecture = str(context.operation.parameters.get("ready_arch", "x64")).strip().lower()
    if architecture not in {"x64", "x86", "arm64"}:
        raise RuntimeError(f"Unknown Windows architecture: {architecture}")
    raw_output = str(context.operation.parameters.get("output_path", "") or "").strip().strip('"')
    output = Path(os.path.expandvars(raw_output)).expanduser() if raw_output else context.paths.output
    if not output.is_absolute():
        output = context.paths.root / output
    output = output.resolve() / "Browser Downloads"
    downloaded: list[dict[str, object]] = []
    failed: list[dict[str, str]] = []
    for index, browser_id in enumerate(selected):
        _check_cancelled(context)
        browser = READY_BY_ID[browser_id]
        try:
            release = resolve_release(browser_id, architecture)
            result = download_release(context, release, output / browser.name, index / len(selected), (index + 1) / len(selected))
            downloaded.append(result)
            if browser_id == "vivaldi":
                context.log("[VIVALDI] Open the saved installer: Advanced -> Install Standalone. / Откройте сохранённый установщик: Дополнительно -> Установить автономную версию.")
            elif browser_id == "duckduckgo":
                context.log("[DUCKDUCKGO] Regular Windows installer saved. / Сохранён обычный установщик для Windows.")
        except DownloadCancelled:
            raise
        except (OSError, ValueError, RuntimeError, HTTPException, zipfile.BadZipFile) as exc:
            failed.append({"browser": browser.name, "error": str(exc)})
            context.log(f"[FAIL] {browser.name}: {exc}")
        context.progress((index + 1) / len(selected))
    result = {"mode": "official_download", "downloaded": downloaded, "failed": failed, "output": str(output)}
    context.report_dir.mkdir(parents=True, exist_ok=True)
    (context.report_dir / "ready_portable_downloads.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not downloaded:
        raise RuntimeError("No official files were downloaded. " + "; ".join(row["browser"] + ": " + row["error"] for row in failed))
    context.log(f"[DONE] Official files downloaded: {len(downloaded)}; failed: {len(failed)}. / Официальных файлов скачано: {len(downloaded)}; ошибок: {len(failed)}.")
    return result
