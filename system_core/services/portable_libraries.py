"""Portable browser libraries: validated downloads with a project-local reserve."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote
import hashlib
import json
import re
import zipfile

from system_core.services.long_paths import plain_path


ENGINES = ("proxy_library", "chrome_plus", "vivaldi_plus")
REPOSITORIES = {"chrome_plus": "DeftKing/chrome_plus", "vivaldi_plus": "ca-x/vivaldi_plus"}
LABELS = {"proxy_library": "Proxy library", "chrome_plus": "Chrome++ (DeftKing)", "vivaldi_plus": "Vivaldi++"}
MACHINES = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}
PROXY_DLLS = {"x86": "version x32.dll", "x64": "version x64.dll"}
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
        raise RuntimeError(f"{LABELS[engine]} does not support {arch}. Choose Chrome++ (DeftKing) or Vivaldi++ for ARM64.")


def github_release(engine: str, arch: str, latest_assets: Callable) -> tuple[str, str, str]:
    require_architecture(engine, arch)
    tag, assets = latest_assets(REPOSITORIES[engine])
    pattern = rf"version-{arch}-.+\.zip" if engine == "chrome_plus" else rf"windows_{arch}\.zip"
    for encoded_name, url in assets:
        name = unquote(encoded_name)
        if re.fullmatch(pattern, name, re.IGNORECASE):
            return tag.lstrip("v"), name, url
    raise RuntimeError(f"{LABELS[engine]}: no {arch} archive in release {tag}.")


def library_files(archive: Path, engine: str, arch: str) -> dict[str, bytes]:
    """Read only known files; never extract paths supplied by an archive."""
    require_architecture(engine, arch)
    wanted = PROXY_DLLS[arch] if engine == "proxy_library" else "version.dll"
    with zipfile.ZipFile(archive) as zipped:
        matches = [item for item in zipped.infolist() if Path(item.filename).name.lower() == wanted.lower()]
        if len(matches) != 1 or matches[0].file_size > 16 * 1024 * 1024:
            raise RuntimeError(f"{archive.name}: expected one {wanted}.")
        dll = zipped.read(matches[0])
        if pe_architecture_bytes(dll) != arch:
            raise RuntimeError(f"{archive.name}: DLL architecture must match {arch}.")
        files = {"version.dll": dll}
        if engine == "chrome_plus":
            configs = [item for item in zipped.infolist() if Path(item.filename).name.lower() == "chrome++.ini"]
            if len(configs) != 1 or configs[0].file_size > 1024 * 1024:
                raise RuntimeError(f"{archive.name}: chrome++.ini is missing or ambiguous.")
            files["chrome++.ini"] = zipped.read(configs[0])
        return files


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
    if installed_engine(build) != engine:
        return True
    if engine == "proxy_library":
        try:
            return (build / "App" / "version.ini").read_bytes() != PROXY_INI.replace("\n", "\r\n").encode("ascii")
        except OSError:
            return True
    return False
