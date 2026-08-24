"""One engine for the whole Chromium stack: download, unpack, publish, update.

Every browser in `browser_registry` is built the same way and differs only in
what the registry says about it:

    installer .exe -> payload .7z -> <browser folder>   ┐
    archive .zip   -> <browser folder>                  ┴-> <build>\\App\\

Portability is Chrome++ (`version.dll` beside the browser executable), the same
piece for all of them, and the certificate block is shared too - files into the
build, trust as a separate act, and an equally separate way to take it back.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
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

from system_core.core.jobs import JobContext, hidden_subprocess_kwargs, utf8_subprocess_env
from system_core.services.browser_registry import BROWSERS, BrowserSpec, browser


USER_AGENT = "Audion-Browsers-Portable"
CHROME_PLUS_REPO = "Bush2021/chrome_plus"
CHROME_PLUS_ASSET = re.compile(r"Chrome\+\+_v.+_x86_x64_arm64\.7z", re.IGNORECASE)

# What makes a build portable, and the operator picks one:
#   chrome_plus    - Chrome++, the wrapper this program started with;
#   proxy_library  - the other proxy `version.dll`, published on GitFlic.
PORTABLE_ENGINES = ("chrome_plus", "proxy_library")
DEFAULT_PORTABLE_ENGINE = "chrome_plus"

# GitFlic's REST API needs a personal token, so the public pages are read
# instead: the release list carries the newest release id and its version, and
# the release page carries the single attachment.
PROXY_LIBRARY_HOST = "https://gitflic.ru"
PROXY_LIBRARY_PROJECT = "neyrostalker/proksi-biblioteka"
PROXY_LIBRARY_DLL = {"x86": "version x32.dll", "x64": "version x64.dll"}
CHROME_VERSION_API = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/win64/channels/stable/versions?pageSize=1"
)
BUILD_STAMP_FILE = "Portable-Build.json"

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
    path = Path(os.path.expandvars(path_text)).expanduser()
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
    path = _portable_root(context) / "_archives"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tmp_dir(context: JobContext) -> Path:
    path = _portable_root(context) / "_tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def handle_remove_error(function: Any, item_path: str, _exc_info: Any) -> None:
        os.chmod(item_path, 0o700)
        function(item_path)

    shutil.rmtree(path, onerror=handle_remove_error)


def _reset_dir(path: Path) -> None:
    _remove_tree(path)
    path.mkdir(parents=True, exist_ok=True)


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


def _download(
    context: JobContext,
    url: str,
    target: Path,
    label: str,
    *,
    user_agent: str = USER_AGENT,
    progress_start: float | None = None,
    progress_end: float | None = None,
) -> DownloadedAsset:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        size = target.stat().st_size
        digest = _sha256(target)
        context.log(f"[CACHE] {label}: {target.name} ({size:,} bytes)")
        _progress_between(context, progress_start, progress_end, 1.0)
        return DownloadedAsset(label, url, target, digest, size)

    context.log(f"[DOWNLOAD] {label}")
    context.log(f"[URL] {url}")
    request = Request(url, headers={"User-Agent": user_agent or USER_AGENT})
    part = target.with_name(target.name + ".part")
    if part.exists():
        part.unlink()
    try:
        with urlopen(request, timeout=120) as response, part.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            last_logged_percent = -10
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    fraction = downloaded / total
                    _progress_between(context, progress_start, progress_end, fraction)
                    percent = int(fraction * 100)
                    if percent >= last_logged_percent + 10:
                        context.log(f"[DOWNLOAD] {label}: {percent}% ({downloaded:,}/{total:,} bytes)")
                        last_logged_percent = percent
            if total <= 0:
                _progress_between(context, progress_start, progress_end, 1.0)
        part.replace(target)
    except (HTTPError, URLError, TimeoutError) as exc:
        if part.exists():
            part.unlink()
        raise RuntimeError(f"Download failed: {url} ({exc})") from exc
    size = target.stat().st_size
    digest = _sha256(target)
    context.log(f"[OK] {target.name} ({size:,} bytes)")
    context.log(f"[SHA256] {digest}")
    return DownloadedAsset(label, url, target, digest, size)


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


def chrome_plus_release() -> tuple[str, str, str]:
    """Version, asset name and URL of the current Chrome++ release."""
    tag, assets = github_latest_assets(CHROME_PLUS_REPO)
    for name, url in assets:
        if CHROME_PLUS_ASSET.fullmatch(name):
            return tag.lstrip("v"), name, url
    listed = ", ".join(name for name, _url in assets)
    raise RuntimeError(f"Chrome++ archive was not found in release {tag}. Assets: {listed}")


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


def _chrome_api_version() -> str:
    request = Request(CHROME_VERSION_API, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    versions = payload.get("versions") or []
    return str(versions[0].get("version") or "") if versions else ""


def _yandex_redirect(url: str, user_agent: str) -> tuple[str, str]:
    """Version and file URL, read out of the redirect instead of the file."""
    request = Request(url, headers={"User-Agent": user_agent}, method="HEAD")
    with urlopen(request, timeout=60) as response:
        resolved = response.geturl()
    if "Yandex.exe" not in resolved:
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
    return context.paths.system_core / "powershell" / "defender_guard.ps1"


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
    # Folders are joined with '|', which Windows paths can never contain, so a
    # single quoted argument survives ShellExecute and the script splits it back.
    return "|".join(str(path) for path in paths)


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
    and the input folder (what an update excludes, since builds are updated in
    place wherever they live). Idempotent: removing a path that is not excluded is
    not an error.
    """
    if os.name != "nt":
        raise RuntimeError("Defender exclusions are a Windows-only concern.")
    folders = [_output_root(context), _input_root(context)]
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
    context.progress(1.0)
    return {"removed": status.startswith("REMOVED"), "paths": [str(f) for f in folders]}


def _run_7z(context: JobContext, args: list[str], *, cwd: Path | None = None) -> None:
    exe = _require_7zip(context)
    command = [str(exe), *args]
    result = subprocess.run(
        command,
        cwd=str(cwd or context.paths.root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        **hidden_subprocess_kwargs(),
    )
    for line in result.stderr.splitlines():
        if line.strip():
            context.log("[STDERR] " + line)
    if result.returncode != 0:
        raise RuntimeError(f"7-Zip failed with exit code {result.returncode}.")


def _extract_archive(context: JobContext, archive: Path, target: Path) -> None:
    context.log(f"[UNPACK] {archive.name} -> {target.name}")
    _reset_dir(target)
    _run_7z(context, ["x", str(archive), f"-o{target}", "-y"])


def _copy_tree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            if destination.exists():
                _remove_tree(destination)
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)


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


def _download_chrome_plus(context: JobContext) -> tuple[Path, str]:
    version, name, url = chrome_plus_release()
    asset = _download(context, url, _archives_dir(context) / _safe_name(name), f"Chrome++ {version}")
    return asset.path, version


def _chrome_plus_arch_dir(context: JobContext, archive: Path, arch: str) -> Path:
    extract_root = _tmp_dir(context) / "chrome_plus"
    if not extract_root.exists() or not any(extract_root.iterdir()):
        _extract_archive(context, archive, extract_root)
    arch_dir = extract_root / arch
    if not arch_dir.is_dir():
        arch_dir = _find_dir(extract_root, arch) or arch_dir
    if not arch_dir.is_dir():
        raise RuntimeError(f"{arch} folder was not found inside the Chrome++ archive.")
    return arch_dir


def _place_chrome_plus(context: JobContext, spec: BrowserSpec, portable_dir: Path, plus_archive: Path) -> str:
    """Put the wrapper matching the browser's own architecture beside it."""
    app_dir = portable_dir / "App"
    app_dir.mkdir(parents=True, exist_ok=True)
    browser_exe = app_dir / spec.executable
    detected = pe_architecture(browser_exe)
    forced = _chrome_plus_arch(context)
    arch = forced or detected or "x64"
    if forced and detected and forced != detected:
        context.log(f"[WARN] {spec.name} is {detected}, but {forced} was forced: the hijack will not take.")
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
PROXY_LIBRARY_INI = """\
; Written by this program for the build it sits in.
[Parameters]
APPDIR=1
REGOFF={regoff}
AIDOFF=1
DIROFF=0
RMDISK=0
REFINE=0
SPFOLD=1
BCTOFF=0
STARTM=0
ECHOFF=0
DNSOFF=0

[General]
COMPNAME=
DATADIR=..\\Data
CACHEDIR=..\\Cache
SPECFOLDER=..\\Data
RUNPARAM=
"""


def _write_proxy_library_ini(context: JobContext, portable_dir: Path, *, block_registry: bool) -> None:
    text = PROXY_LIBRARY_INI.format(regoff="1" if block_registry else "0")
    (portable_dir / "App" / "version.ini").write_bytes(text.replace("\n", "\r\n").encode("ascii"))
    if block_registry:
        context.log("[INI] registry writes are blocked while the browser runs")
    else:
        context.log("[INI] registry writes left alone: the browser keeps its own branch")


def _place_proxy_library(
    context: JobContext,
    spec: BrowserSpec,
    portable_dir: Path,
    archive: Path,
    *,
    block_registry: bool,
) -> str:
    """Put the proxy library's `version.dll` beside the browser, plus its ini.

    Unlike Chrome++ this one does not wipe the registry branch on exit - it
    blocks the writes outright, so nothing accumulates to be wiped. The cost is
    that `Set as default browser` stops working, which a portable build has no
    business doing anyway.
    """
    app_dir = portable_dir / "App"
    app_dir.mkdir(parents=True, exist_ok=True)
    detected = pe_architecture(app_dir / spec.executable)
    forced = _chrome_plus_arch(context)
    arch = forced or detected or "x64"
    if arch not in PROXY_LIBRARY_DLL:
        raise RuntimeError(
            f"{spec.name}: the proxy library ships x86 and x64 only, and this build is {arch}. "
            "Chrome++ is the wrapper that covers ARM64."
        )
    if forced and detected and forced != detected:
        context.log(f"[WARN] {spec.name} is {detected}, but {forced} was forced: the hijack will not take.")
    else:
        context.log(f"[ARCH] {spec.name}: {detected or 'unknown'} -> proxy library {arch}")

    extract_root = _tmp_dir(context) / "proxy_library"
    if not extract_root.exists() or not any(extract_root.iterdir()):
        _extract_archive(context, archive, extract_root)
    source = _find_file(extract_root, PROXY_LIBRARY_DLL[arch])
    if source is None:
        raise RuntimeError(f"{PROXY_LIBRARY_DLL[arch]} was not found inside the proxy library archive.")
    shutil.copy2(source, app_dir / "version.dll")
    placed = pe_architecture(app_dir / "version.dll")
    if detected and placed and detected != placed:
        raise RuntimeError(
            f"{spec.name}: the browser is {detected} and the proxy library is {placed}. "
            "They must match, or the build will not be portable."
        )
    _write_proxy_library_ini(context, portable_dir, block_registry=block_registry)
    for folder_name in ("Data", "Cache"):
        (portable_dir / folder_name).mkdir(parents=True, exist_ok=True)
    return arch


def _portable_engine(context: JobContext) -> str:
    value = _param_text(context, "portable_engine", DEFAULT_PORTABLE_ENGINE).strip().lower()
    return value if value in PORTABLE_ENGINES else DEFAULT_PORTABLE_ENGINE


def _download_wrapper(context: JobContext, engine: str) -> tuple[Path | None, str]:
    """The archive the chosen engine needs, or `(None, "")` when it needs none."""
    if engine == "chrome_plus":
        archive, version = _download_chrome_plus(context)
        context.log(f"[CHROME++] {version}")
        return archive, version
    version, url = proxy_library_release()
    context.log(f"[PROXY] published: {version or 'unknown'}")
    asset = _download(
        context,
        url,
        _archives_dir(context) / f"proxy-library-{version or 'latest'}.zip",
        f"Proxy library {version}".strip(),
    )
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
    if engine == "chrome_plus":
        arch = _place_chrome_plus(context, spec, portable_dir, archive)
        _configure_chrome_plus_ini(context, spec, portable_dir, wipe_registry=wipe_registry)
        return arch
    return _place_proxy_library(context, spec, portable_dir, archive, block_registry=wipe_registry)


def _write_launcher(context: JobContext, spec: BrowserSpec, portable_dir: Path) -> Path:
    """The `.cmd` that starts the build.

    The wrapper reads the profile paths itself, so the browser needs no
    switches here.
    """
    launcher = portable_dir / f"{spec.folder}.cmd"
    launcher.write_bytes(
        ("@echo off\r\n" f'start "" "%~dp0App\\{spec.executable}" %*\r\n').encode("utf-8")
    )
    return launcher


def build_versions(spec: BrowserSpec, portable_dir: Path) -> tuple[str, str]:
    """`(browser version, Chrome++ version)`, read from the build itself."""
    app_dir = portable_dir / "App"
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


def _safe_output_child(context: JobContext, name: str) -> Path:
    portable = _portable_root(context).resolve()
    target = (portable / name).resolve()
    if target == portable or not target.is_relative_to(portable):
        raise RuntimeError(f"Refusing unsafe portable output path: {target}")
    return target


def _publish(context: JobContext, source_dir: Path, name: str) -> Path:
    target = _safe_output_child(context, name)
    if target.exists():
        _remove_tree(target)
    shutil.copytree(source_dir, target)
    context.log(f"[OK] FOLDER: {target}")
    return target


def _zip_dir(context: JobContext, source_dir: Path, zip_path: Path) -> Path:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in source_dir.rglob("*"):
            if item.is_dir():
                archive.writestr(item.relative_to(source_dir.parent).as_posix().rstrip("/") + "/", b"")
            elif item.is_file():
                archive.write(item, item.relative_to(source_dir.parent))
    context.log(f"[OK] ZIP: {zip_path} ({zip_path.stat().st_size:,} bytes)")
    return zip_path


def _archive_build(context: JobContext, source_dir: Path, base_name: str) -> Path:
    archive_format = _param_text(context, "archive_format", "zip").lower()
    if archive_format == "7z":
        archive_path = _portable_root(context) / f"{base_name}.7z"
        if archive_path.exists():
            archive_path.unlink()
        _run_7z(context, ["a", "-t7z", "-mx=9", str(archive_path), source_dir.name], cwd=source_dir.parent)
        context.log(f"[OK] 7Z: {archive_path} ({archive_path.stat().st_size:,} bytes)")
        return archive_path
    return _zip_dir(context, source_dir, _portable_root(context) / f"{base_name}.zip")


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
    context.log(f"[OK] certificate wrappers: {target}")
    return written


def stage_certificates(context: JobContext, portable_dir: Path | None = None) -> dict[str, object]:
    """Put the certificate files and their two wrappers into the build."""
    target = _certificates_dir(context, portable_dir)
    files: list[str] = []
    for item in RUSSIAN_TRUSTED_CERTIFICATES:
        asset = _download(context, str(item["url"]), target / str(item["name"]), str(item["subject"]))
        files.append(str(asset.path))
    wrappers = _write_certificate_wrappers(context, target)
    return {"directory": str(target), "files": files, "wrappers": [str(path) for path in wrappers]}


def _certificate_present(thumbprint: str, store: str) -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["certutil", "-store", "-user", store, thumbprint],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
    for index, item in enumerate(RUSSIAN_TRUSTED_CERTIFICATES, start=1):
        path = target / str(item["name"])
        if not path.is_file():
            _download(context, str(item["url"]), path, str(item["subject"]))
        before = _certificate_present(str(item["thumbprint"]), str(item["store"]))
        context.log(f"[RUN] certutil -addstore -user -f {item['store']} {path.name}")
        result = subprocess.run(
            ["certutil", "-addstore", "-user", "-f", str(item["store"]), str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=utf8_subprocess_env(),
            **hidden_subprocess_kwargs(),
        )
        for line in (result.stdout or "").splitlines():
            if line.strip():
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
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=utf8_subprocess_env(),
            **hidden_subprocess_kwargs(),
        )
        for line in (result.stdout or "").splitlines():
            if line.strip():
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
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env=utf8_subprocess_env(),
        **hidden_subprocess_kwargs(),
    )
    for line in result.stdout.splitlines():
        if line.strip():
            context.log(line)
    if result.returncode != 0:
        raise RuntimeError(f"Portable 7-Zip install failed with exit code {result.returncode}.")
    version = _seven_zip_version(context)
    if not version:
        raise RuntimeError("Portable 7-Zip installer finished, but 7za.exe is not usable.")
    context.log(f"[OK] Portable 7-Zip installed: {version}")
    context.progress(1.0)
    return {"installed": True, "version": version, "path": str(_seven_zip_path(context))}


def _selected_specs(context: JobContext) -> list[BrowserSpec]:
    selected = _param_list(context, "browsers")
    if not selected:
        raise RuntimeError("Select at least one browser.")
    return [browser(item) for item in selected]


def _existing_build(context: JobContext, spec: BrowserSpec) -> Path | None:
    """A build to update, wherever the person pointed the Source at.

    Both readings of "this folder" are accepted, because both are what someone
    means when they pick one: the build itself, or the folder holding it. The
    published Target is the last resort, so an update still works when nothing
    was picked at all.
    """
    source = _input_root(context)
    candidates = (
        source if source.name == spec.folder else None,
        source / spec.folder,
        _portable_root(context) / spec.folder,
    )
    for candidate in candidates:
        if candidate is not None and (candidate / "App").is_dir():
            return candidate
    return None


def _published_wrapper_version(context: JobContext, engine: str) -> str:
    """What the chosen wrapper publishes today; nothing is downloaded."""
    if engine == "chrome_plus":
        version, _name, _url = chrome_plus_release()
        context.log(f"[CHROME++] published: {version}")
        return version
    version, _url = proxy_library_release()
    context.log(f"[PROXY] published: {version or 'unknown'}")
    return version


def check_updates(context: JobContext) -> dict[str, object]:
    """What is published against what is on disk. Downloads nothing."""
    specs = _selected_specs(context)
    engine = _portable_engine(context)
    plus_version = _published_wrapper_version(context, engine)
    rows: list[dict[str, object]] = []
    for index, spec in enumerate(specs, start=1):
        try:
            published, _url, _filename = published_source(spec)
        except RuntimeError as exc:
            context.log(f"[FAIL] {spec.name}: {exc}")
            rows.append({"browser": spec.name, "error": str(exc)})
            context.progress(index / len(specs))
            continue
        build = _existing_build(context, spec)
        current, current_plus = build_versions(spec, build) if build else ("", "")
        needs = bool(published) and not same_version(published, current, spec.version_match)
        plus_needs = bool(plus_version) and not same_version(plus_version, current_plus)
        state = "not built yet" if not build else ("update available" if needs else "up to date")
        context.log(
            f"[{spec.name}] published {published or '?'} / build {current or '-'} -> {state}"
            + ("; wrapper update available" if build and plus_needs else "")
        )
        rows.append(
            {
                "browser": spec.name,
                "published": published,
                "build": current,
                "wrapper": current_plus,
                "chrome_plus": current_plus,
                "update": needs,
                "wrapper_update": plus_needs,
                "chrome_plus_update": plus_needs,
                "path": str(build) if build else "",
            }
        )
        context.progress(index / len(specs))
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
    """
    app_dir = target / "App"
    retired = target / f"App.replaced-{os.getpid()}"
    if app_dir.exists():
        try:
            app_dir.rename(retired)
        except OSError as exc:
            raise RuntimeError(
                f"{spec.name}: the build is in use, so App could not be replaced. "
                f"Close the browser and run the update again. ({exc})"
            ) from exc
    try:
        shutil.move(str(staged_app), str(app_dir))
    except OSError:
        if retired.exists():
            retired.rename(app_dir)
        raise
    _remove_tree(retired)
    context.log(f"[UPDATE] App replaced in place: {target}")


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
) -> dict[str, object]:
    published, url, filename = published_source(spec)
    context.log(f"[{spec.name}] published: {published or 'unknown'}")
    asset = _download(
        context,
        url,
        _archives_dir(context) / _safe_name(filename),
        f"{spec.name} {published}".strip(),
        user_agent=spec.user_agent or USER_AGENT,
    )

    work = _tmp_dir(context) / spec.folder
    _remove_tree(work)
    work.mkdir(parents=True, exist_ok=True)

    payload = _unpack_browser(context, spec, asset.path)
    app_dir = work / "App"
    app_dir.mkdir(parents=True, exist_ok=True)
    context.log(f"[COPY] {payload.name} -> App")
    _copy_tree_contents(payload, app_dir)
    if not (app_dir / spec.executable).exists():
        raise RuntimeError(f"{spec.name}: {spec.executable} is missing from App after the copy.")

    _place_wrapper(
        context,
        spec,
        work,
        engine=engine,
        archive=plus_archive,
        wipe_registry=wipe_registry,
    )

    if keep_data_from is not None:
        # Update: the freshly assembled App moves into the existing build, and
        # the Target folder is left for new builds only.
        _replace_app_in_place(context, spec, keep_data_from, work / "App")
        home = keep_data_from
    else:
        home = work

    _write_launcher(context, spec, home)
    certificates = stage_certificates(context, home) if with_certificates else {}
    _write_build_stamp(
        context,
        spec,
        home,
        {
            "mode": "update" if keep_data_from is not None else "build",
            "source_url": asset.url,
            "portable_engine": engine,
            "chrome_plus_release": plus_version,
            "certificates_staged": bool(with_certificates),
        },
    )
    version, plus_in_build = build_versions(spec, home)
    if keep_data_from is not None:
        artifact = _archive_build(context, home, spec.folder) if package_archive else home
    else:
        artifact = _archive_build(context, work, spec.folder) if package_archive else _publish(context, work, spec.folder)
    return {
        "browser": spec.name,
        "id": spec.id,
        "version": version or published,
        "portable_engine": engine,
        "wrapper_version": plus_in_build or plus_version,
        "chrome_plus_version": plus_in_build or plus_version,
        "artifact": str(artifact),
        "certificates": bool(certificates),
        "registry_wiped_on_exit": bool(engine == "chrome_plus" and wipe_registry and spec.registry_branch),
        "registry_writes_blocked": bool(engine == "proxy_library" and wipe_registry),
    }


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
        plus_archive, plus_version = _download_wrapper(context, engine)

        built: list[dict[str, object]] = []
        failed: list[dict[str, str]] = []
        for index, spec in enumerate(specs, start=1):
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
                    )
                )
                context.log(f"[DONE] {spec.name}")
            except Exception as exc:  # noqa: BLE001 - one browser must not stop the batch
                context.log(f"[FAIL] {spec.name}: {exc}")
                failed.append({"browser": spec.name, "error": str(exc)})
            finally:
                context.progress(index / len(specs))
        if not keep_temp:
            _remove_tree(_tmp_dir(context))
    if failed and not built:
        raise RuntimeError("; ".join(f"{item['browser']}: {item['error']}" for item in failed))
    return {"built": built, "failed": failed, "output": str(_portable_root(context))}


def update_selected(context: JobContext) -> dict[str, object]:
    """Refresh App in existing builds, keeping Data and Cache."""
    _require_7zip(context)
    specs = _selected_specs(context)
    keep_temp = _param_bool(context, "keep_temp", False)
    wipe_registry = _param_bool(context, "wipe_registry_on_exit", False)
    with_certificates = _param_bool(context, "stage_certificates", False)
    package_archive = _param_bool(context, "package_archive", False)
    force = _param_bool(context, "force_update", False)
    guard_defender = _param_bool(context, "guard_defender", True)

    # version.dll lands in the workspace (under output) and in each build being
    # updated, which lives under input or output. Excluding both roots covers
    # every place _existing_build can point at. No-op where Defender is not running.
    guard = (
        _defender_guard(context, [_output_root(context), _input_root(context)])
        if guard_defender
        else contextlib.nullcontext()
    )
    with guard:
        engine = _portable_engine(context)
        plus_archive, plus_version = _download_wrapper(context, engine)

        updated: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        failed: list[dict[str, str]] = []
        for index, spec in enumerate(specs, start=1):
            context.log(f"[{spec.name}] updating {index}/{len(specs)}")
            try:
                build = _existing_build(context, spec)
                if build is None:
                    context.log(f"[SKIP] {spec.name}: no build found in input or output.")
                    skipped.append({"browser": spec.name, "reason": "no build"})
                    continue
                current, current_plus = build_versions(spec, build)
                published, _url, _filename = published_source(spec)
                if (
                    published
                    and same_version(published, current, spec.version_match)
                    and same_version(plus_version, current_plus)
                    and not force
                ):
                    context.log(f"[SKIP] {spec.name}: {current} is current, wrapper {current_plus or '-'} too.")
                    skipped.append({"browser": spec.name, "reason": "already current", "version": current})
                    continue
                updated.append(
                    _build_one(
                        context,
                        spec,
                        engine=engine,
                        plus_archive=plus_archive,
                        plus_version=plus_version,
                        wipe_registry=wipe_registry,
                        with_certificates=with_certificates,
                        package_archive=package_archive,
                        keep_data_from=build,
                    )
                )
                context.log(f"[DONE] {spec.name}: {current or '-'} -> {published}")
            except Exception as exc:  # noqa: BLE001 - one browser must not stop the batch
                context.log(f"[FAIL] {spec.name}: {exc}")
                failed.append({"browser": spec.name, "error": str(exc)})
            finally:
                context.progress(index / len(specs))
        if not keep_temp:
            _remove_tree(_tmp_dir(context))
    return {"updated": updated, "skipped": skipped, "failed": failed, "output": str(_portable_root(context))}


def browser_options(root: Path | None = None) -> list[dict[str, str]]:
    """Checkbox options for the manifest, straight from the registry."""
    return [
        {"value": spec.id, "label": spec.name, "label_ru": spec.name}
        for spec in BROWSERS
    ]
