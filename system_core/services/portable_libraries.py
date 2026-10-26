"""Portable browser libraries: validated downloads with a project-local reserve."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable
from urllib.parse import unquote
import hashlib
import json
import re
import subprocess
import zipfile

from system_core.core.jobs import hidden_subprocess_kwargs
from system_core.services.long_paths import plain_path


ENGINES = ("proxy_library", "chrome_plus", "vivaldi_plus")
REPOSITORIES = {"chrome_plus": "Bush2021/chrome_plus", "vivaldi_plus": "ca-x/vivaldi_plus"}
LABELS = {"proxy_library": "Proxy library", "chrome_plus": "Chrome++", "vivaldi_plus": "Vivaldi++"}
# The author ships Chrome++ as one 7z of every architecture (Chrome++_v1.18.2_x86_x64_arm64.7z, <arch>\App\...);
# it is read by the 7-Zip the program carries (Install-Portable-7Zip.cmd).
SEVEN_ZIP = Path(__file__).resolve().parents[2] / "tools" / "7zip" / "bin" / "7za.exe"
MACHINES = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}
PROXY_DLLS = {"x86": "version x32.dll", "x64": "version x64.dll"}
# The settings file each library reads beside the browser.
CONFIG_FILES = {"chrome_plus": "chrome++.ini", "proxy_library": "version.ini", "vivaldi_plus": "config.ini"}
PROXY_INI = """\
; Written by Audion Portable. Optional switches are disabled.
[Parameters]
APPDIR=0
REGOFF=0
AIDOFF=0
DIROFF=0
RMDISK=0
REFINE=0
SPFOLD=0
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
VIVALDI_INI = """\
; Written by Audion Portable for Vivaldi++.
[general]
win32k=0
debug_log=0
command_line=
disable_features=

[dir_setting]
data=%app%\\..\\Data
cache=%app%\\..\\Cache

[hotkey]
boss_key=
"""


@dataclass(frozen=True)
class LibraryAsset:
    name: str
    url: str
    path: Path
    sha256: str
    size: int


def pe_architecture_bytes(raw: bytes) -> str:
    if len(raw) < 64 or raw[:2] != b"MZ":
        return ""
    offset = int.from_bytes(raw[0x3C:0x40], "little")
    if raw[offset:offset + 4] != b"PE\x00\x00":
        return ""
    return MACHINES.get(int.from_bytes(raw[offset + 4:offset + 6], "little"), "")


def pe_architecture(path: Path) -> str:
    try:
        return pe_architecture_bytes(path.read_bytes())
    except OSError:
        return ""


def require_architecture(engine: str, arch: str) -> None:
    if engine not in ENGINES:
        raise ValueError(f"Unknown portable library: {engine}")
    if arch not in MACHINES.values() or (engine == "proxy_library" and arch not in PROXY_DLLS):
        raise RuntimeError(f"{LABELS[engine]} does not support {arch}. Choose Chrome++ or Vivaldi++ for ARM64.")


def github_release(engine: str, arch: str, latest_assets: Callable) -> tuple[str, str, str]:
    require_architecture(engine, arch)
    tag, assets = latest_assets(REPOSITORIES[engine])
    for encoded_name, url in assets:
        name = unquote(encoded_name)
        if _holds(engine, name, arch):
            return tag.lstrip("v"), name, url
    raise RuntimeError(f"{LABELS[engine]}: no {arch} archive in release {tag}.")


def _holds(engine: str, name: str, arch: str) -> bool:
    """Whether an asset of a release is the archive of the library for `arch` (setdll.7z beside Chrome++ is not)."""
    if engine == "chrome_plus":
        named = re.fullmatch(r"Chrome\+\+_v[^_]+_(.+)\.7z", name, re.IGNORECASE)
        return bool(named) and arch in named.group(1).lower().split("_")
    return re.fullmatch(rf"windows_{arch}\.zip", name, re.IGNORECASE) is not None


def _seven_zip(*args: str) -> bytes:
    if not SEVEN_ZIP.is_file():
        raise RuntimeError(f"Portable 7-Zip is not available: {plain_path(SEVEN_ZIP)}. Run 'Check / install portable 7-Zip' first.")
    done = subprocess.run([str(SEVEN_ZIP), *args, "-sccUTF-8"], capture_output=True, timeout=120, **hidden_subprocess_kwargs())
    if done.returncode != 0:
        raise RuntimeError("7-Zip: " + done.stderr.decode("utf-8", "replace").strip()[-300:])
    return done.stdout


def _chrome_plus_files(archive: Path, arch: str) -> dict[str, bytes]:
    """version.dll and chrome++.ini of one architecture out of the 7z of the author: known names, read to memory."""
    if archive.suffix.lower() != ".7z":
        raise RuntimeError(f"{archive.name}: Chrome++ comes as the 7z of {REPOSITORIES['chrome_plus']}.")
    sizes: dict[str, int] = {}
    for block in _seven_zip("l", "-slt", "-ba", str(archive)).decode("utf-8", "replace").replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" in fields and not fields.get("Attributes", "").startswith("D"):
            sizes[fields["Path"]] = int(fields.get("Size") or 0)
    files = {}
    for wanted, limit in (("version.dll", 16 * 1024 * 1024), ("chrome++.ini", 1024 * 1024)):
        matches = [path for path in sizes if PureWindowsPath(path).name.lower() == wanted
                   and arch in (part.lower() for part in PureWindowsPath(path).parts[:-1])]
        if len(matches) != 1 or sizes[matches[0]] > limit:
            raise RuntimeError(f"{archive.name}: expected one {wanted} for {arch}.")
        files[wanted] = _seven_zip("e", "-so", "-spd", str(archive), matches[0])
        if len(files[wanted]) != sizes[matches[0]]:
            raise RuntimeError(f"{archive.name}: {wanted} for {arch} did not read whole.")
    if pe_architecture_bytes(files["version.dll"]) != arch:
        raise RuntimeError(f"{archive.name}: DLL architecture must match {arch}.")
    return files


def library_files(archive: Path, engine: str, arch: str) -> dict[str, bytes]:
    """Read only known files; never extract paths supplied by an archive."""
    require_architecture(engine, arch)
    if engine == "chrome_plus":
        return _chrome_plus_files(archive, arch)
    wanted = PROXY_DLLS[arch] if engine == "proxy_library" else "version.dll"
    with zipfile.ZipFile(archive) as zipped:
        matches = [item for item in zipped.infolist() if Path(item.filename).name.lower() == wanted.lower()]
        if len(matches) != 1 or matches[0].file_size > 16 * 1024 * 1024:
            raise RuntimeError(f"{archive.name}: expected one {wanted}.")
        dll = zipped.read(matches[0])
        if pe_architecture_bytes(dll) != arch:
            raise RuntimeError(f"{archive.name}: DLL architecture must match {arch}.")
        return {"version.dll": dll}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(path: Path, engine: str, arch: str) -> None:
    for selected in PROXY_DLLS if engine == "proxy_library" else (arch,):
        library_files(path, engine, selected)


def _key(engine: str, arch: str) -> Path:
    return Path(engine) / ("all" if engine == "proxy_library" else arch)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def local_library(context: Any, engine: str, arch: str, cache_root: Path) -> tuple[LibraryAsset, str] | None:
    """Newest verified cached release, then the bundled release of the same library."""
    require_architecture(engine, arch)
    roots = (cache_root / "portable_libraries" / _key(engine, arch),
             context.paths.root / "tools" / "portable_libraries" / _key(engine, arch))
    for root in roots:
        candidates = []
        for meta in root.glob("*.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                name = data["name"]
                if Path(name).name != name or "/" in name or "\\" in name:
                    continue
                if data["engine"] != engine or data["architecture"] != _key(engine, arch).name:
                    continue
                # A reserve is of the source the library comes from now: not the build of a fork it once came from.
                if engine in REPOSITORIES and f"github.com/{REPOSITORIES[engine]}/" not in data.get("url", ""):
                    continue
                path = root / name
                if _digest(path) != data["sha256"]:
                    raise RuntimeError("SHA256 mismatch")
                _validate(path, engine, arch)
                asset = LibraryAsset(name, data["url"], path, data["sha256"], path.stat().st_size)
                candidates.append((asset, data["version"]))
            except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
                context.log(f"[WARN] Invalid library reserve {plain_path(meta)}: {exc}")
        if candidates:
            return max(candidates, key=lambda item: _version_key(item[1]))
    return None


def download_library(context: Any, engine: str, arch: str, lookup: Callable,
                     download: Callable, cache_root: Path, **progress: Any) -> tuple[Any, str]:
    require_architecture(engine, arch)
    try:
        version, name, url = lookup()
        safe_version = re.sub(r"[^a-zA-Z0-9._-]", "_", version or "latest")
        target = cache_root / "portable_libraries" / _key(engine, arch) / f"{safe_version}-{Path(name).name}"
        context.log(f"[LIBRARY] {LABELS[engine]} {version} / {arch}")
        asset = download(context, url, target, f"{LABELS[engine]} {version}", **progress)
        _validate(asset.path, engine, arch)
        data = dict(engine=engine, architecture=_key(engine, arch).name, version=version,
                    name=target.name, url=url, sha256=_digest(asset.path), size=asset.size)
        meta = target.with_suffix(".json")
        part = meta.with_suffix(".json.part")
        part.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        part.replace(meta)
        return asset, version
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        context.log(f"[WARN] {LABELS[engine]} source unavailable: {exc}")
        reserve = local_library(context, engine, arch, cache_root)
        if reserve is None:
            raise RuntimeError(f"{LABELS[engine]} ({arch}) is unavailable and has no valid local reserve. "
                               "Restore tools/portable_libraries or choose another library; the build was not published.") from exc
        asset, version = reserve
        context.log(f"[FALLBACK] {LABELS[engine]} {version}: {plain_path(asset.path)}")
        if progress.get("progress_end") is not None:
            context.progress(progress["progress_end"])
        return asset, version


def published_version(context: Any, engine: str, arch: str, lookup: Callable, cache_root: Path) -> str:
    require_architecture(engine, arch)
    try:
        return lookup()[0]
    except (OSError, ValueError, RuntimeError) as exc:
        context.log(f"[WARN] Cannot check {LABELS[engine]} release: {exc}")
        reserve = local_library(context, engine, arch, cache_root)
        if reserve:
            context.log(f"[FALLBACK] Comparing local {LABELS[engine]} {reserve[1]}; latest online version is unknown.")
            return reserve[1]
        return ""


def prepare_chrome_archive(archive: Path, arch: str, target: Path) -> Path:
    app = target / arch / "App"
    files = library_files(archive, "chrome_plus", arch)
    app.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        (app / name).write_bytes(raw)
    return app.parent


def record_engine(build: Path, engine: str) -> None:
    app = build / "App"
    (app / "portable-library.json").write_text(json.dumps({"engine": engine}), encoding="utf-8")
    for name, owner in (("chrome++.ini", "chrome_plus"), ("version.ini", "proxy_library")):
        if owner != engine:
            (app / name).unlink(missing_ok=True)


def install_vivaldi(context: Any, build: Path, archive: Path, arch: str, executable: str) -> str:
    detected = pe_architecture(build / "App" / executable)
    if detected and detected != arch:
        raise RuntimeError(f"Browser is {detected}; Vivaldi++ is {arch}. They must match.")
    files = library_files(archive, "vivaldi_plus", arch)
    app = build / "App"
    app.mkdir(parents=True, exist_ok=True)
    (app / "version.dll").write_bytes(files["version.dll"])
    (app / "config.ini").write_bytes(b"\xff\xfe" + VIVALDI_INI.encode("utf-16-le"))
    for name in ("Data", "Cache"):
        (build / name).mkdir(parents=True, exist_ok=True)
    record_engine(build, "vivaldi_plus")
    context.log(f"[COPY] Vivaldi++ {arch} -> {plain_path(app)}")
    return arch


def installed_engine(build: Path) -> str:
    app = build / "App"
    try:
        return json.loads((app / "portable-library.json").read_text(encoding="utf-8"))["engine"]
    except (OSError, ValueError, KeyError):
        if (app / "version.ini").is_file():
            return "proxy_library"
        if (app / "chrome++.ini").is_file():
            return "chrome_plus"
        if (app / "config.ini").is_file():
            return "vivaldi_plus"
        return ""


def needs_refresh(build: Path, engine: str) -> bool:
    """Whether the library has to be put into the build anew: another one is there, or its settings file is gone.

    A settings file that was edited is no reason. It used to be one for the proxy library - any
    difference from the file this program writes counted - so a switch a person turned on by
    hand (the very edit the comment beside that file invites) made Check say "wrapper update
    available" for good, and the next Update downloaded the browser again to put the file back.
    The settings of a library are the person's: see `_keep_library_settings` in the service.
    """
    if installed_engine(build) != engine:
        return True
    # The proxy library does nothing portable without its file: the paths of Data and Cache are in it.
    return engine == "proxy_library" and not (build / "App" / CONFIG_FILES[engine]).is_file()
