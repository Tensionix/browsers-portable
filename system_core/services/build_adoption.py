"""Recognise a portable build of any layout and bring it to this program's standard.

The standard is three folders side by side and nothing else that matters:

    <build>\\App\\      the browser, with its executable right inside
    <build>\\Data\\     the profile
    <build>\\Cache\\    the cache

Builds made elsewhere keep the same three things under other names - `Chrome\\`
instead of `App\\`, `App\\Chrome-bin\\` with the profile in `Data\\profile\\`, or
everything flat in one folder. None of that needs guessing by name: the browser
is wherever its executable is, and the profile is the folder holding
`Local State`. Everything here works from those two facts.

Nothing in this module downloads anything, and the profile is only ever moved by
renaming - it is never copied and never deleted.

Nor is anything else a person may have put into the build. Only what is certainly
the browser goes into `App`, where the update replaces it; whatever lay beside it
stays where it was, and what is in the way - another packer's launchers, the rest
of its `Data` - is moved into `Old files` inside the build instead of being
deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator
import os
import re


Log = Callable[[str], None]

# How deep the executable may sit below the folder being searched. Three covers
# `input\<build>\App\Chrome-bin\chrome.exe`, the deepest layout in common use.
SEARCH_DEPTH = 3

# Names a browser branch goes by. Used only when a build has no profile yet and
# there is nothing better to tell `<build>\Chrome\` from a flat build by.
BRANCH_NAMES = frozenset({"app", "chrome", "chrome-bin", "browser-bin", "bin", "browser", "application"})

# What another packer leaves at the root of its build to start it: launchers,
# their settings and wrappers. They point at folders that no longer exist once
# the build is brought to the standard.
LAUNCHER_SUFFIXES = frozenset({".bat", ".cmd", ".lnk", ".url", ".exe", ".dll", ".ini"})

# PortableApps keeps a profile *template* here. It looks like a profile and is
# not one: the real profile is in `Data\profile`.
TEMPLATE_NAMES = frozenset({"defaultdata"})

# Where the things that were in the way are put, inside the build. A person
# decides what to do with them; nothing in there is looked at again.
ASIDE_NAME = "Old files"

# What a portability wrapper keeps beside the browser. It goes with the old
# browser: the new one comes with its own.
WRAPPER_FILES = frozenset({"version.dll", "chrome++.ini", "version.ini", "config.ini", "portable-library.json", "debug.log"})

# Folders this program makes inside a build while it works on it. One left behind
# by a failed run holds a browser, and must not be taken for a build.
WORK_PREFIXES = ("app.incoming-", "app.replaced-", "app.adopting-", "data.adopting-")

_VERSION_DIRECTORY = re.compile(r"\d+(\.\d+){2,}")


def _is_work_folder(name: str) -> bool:
    return name.lower().startswith(WORK_PREFIXES)


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def same_path(left: Path, right: Path) -> bool:
    return _key(left) == _key(right)


def inside(path: Path, root: Path) -> bool:
    """`path` is `root` itself or lies under it."""
    child, parent = _key(path), _key(root)
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or os.path.isjunction(path)


def looks_like_profile(path: Path) -> bool:
    """A Chromium profile root: `Local State`, or a `Default` profile inside."""
    try:
        return (path / "Local State").is_file() or (path / "Default" / "Preferences").is_file()
    except OSError:
        return False


def _subdirectories(path: Path) -> list[Path]:
    try:
        found = [item for item in path.iterdir() if item.is_dir() and not _is_link(item)]
    except OSError:
        return []
    # `Data` first: when two folders qualify, the conventional one wins.
    return sorted(found, key=lambda item: (item.name.lower() != "data", item.name.lower()))


def _walk(root: Path, max_depth: int, skip: Path | None = None) -> Iterator[Path]:
    """Folders under `root`, nearest first, never entering a profile or a cache.

    A profile holds tens of thousands of folders and nothing this module looks
    for, so it is reported and then left alone.
    """
    level = [root]
    for _depth in range(max_depth):
        following: list[Path] = []
        for parent in level:
            for child in _subdirectories(parent):
                if skip is not None and same_path(child, skip):
                    continue
                lowered = child.name.lower()
                if lowered in TEMPLATE_NAMES or lowered == ASIDE_NAME.lower() or _is_work_folder(lowered):
                    continue
                yield child
                if (
                    looks_like_profile(child)
                    or child.name.lower() == "cache"
                    or _VERSION_DIRECTORY.fullmatch(child.name)
                ):
                    continue
                following.append(child)
        level = following


def browser_directories(scope: Path, executable: str, max_depth: int = SEARCH_DEPTH) -> list[Path]:
    """Every folder under `scope` that holds the browser executable."""
    found = [scope] if (scope / executable).is_file() else []
    for folder in _walk(scope, max_depth):
        if looks_like_profile(folder) or folder.name.lower() == "cache":
            continue
        if (folder / executable).is_file():
            found.append(folder)
    return found


def find_profile(root: Path, skip: Path | None = None, max_depth: int = 2) -> Path | None:
    """The profile folder under `root`, ignoring the branch `skip`."""
    for folder in _walk(root, max_depth, skip):
        if looks_like_profile(folder):
            return folder
    return None


def _holds_browser(folder: Path, executables: Iterable[str]) -> bool:
    names = [name for name in executables if name]
    candidates = [folder, *_walk(folder, 2)]
    return any((item / name).is_file() for item in candidates for name in names)


def _branch_top(root: Path, path: Path) -> Path:
    """The direct child of `root` that `path` lives in."""
    return root / Path(os.path.relpath(path, root)).parts[0]


@dataclass(frozen=True)
class Layout:
    """Where the three parts of one build actually are."""

    home: Path
    browser_dir: Path
    profile_dir: Path | None

    @property
    def standard(self) -> bool:
        if not same_path(self.browser_dir, self.home / "App"):
            return False
        return self.profile_dir is None or same_path(self.profile_dir, self.home / "Data")


def build_home(
    browser_dir: Path,
    scope: Path,
    executables: Iterable[str] = (),
    stop: Iterable[Path] = (),
) -> tuple[Path, Path | None]:
    """The build's own folder and its profile, worked out from the executable.

    The build is the nearest folder that holds both the browser and a profile:
    the browser folder itself when the profile is inside it, otherwise the first
    ancestor with a profile beside the browser branch. A profile that belongs to
    a neighbouring build - its branch has a browser of its own - does not count,
    and the folders in `stop` are containers that can never be a build.
    """
    own = find_profile(browser_dir, max_depth=1)
    if own is not None:
        return browser_dir, own

    stops = list(stop)
    level = browser_dir
    for _ in range(SEARCH_DEPTH):
        if same_path(level, scope):
            break
        parent = level.parent
        if not inside(parent, scope) or any(same_path(parent, item) for item in stops):
            break
        profile = find_profile(parent, skip=level)
        if profile is not None and not _holds_browser(_branch_top(parent, profile), executables):
            return parent, profile
        level = parent

    # No profile anywhere: a build that was never started. Step out of the
    # browser branch by its name, since there is nothing else to go by.
    level = browser_dir
    while (
        not same_path(level, scope)
        and level.name.lower() in BRANCH_NAMES
        and inside(level.parent, scope)
        and not any(same_path(level.parent, item) for item in stops)
    ):
        level = level.parent
    return level, None


def builds_in(
    scope: Path,
    executable: str,
    executables: Iterable[str] = (),
    stop: Iterable[Path] = (),
) -> list[Layout]:
    """Every build of this browser under `scope`, one entry per build folder."""
    known = list(executables) or [executable]
    layouts: list[Layout] = []
    for browser_dir in browser_directories(scope, executable):
        home, profile = build_home(browser_dir, scope, known, stop)
        if any(same_path(home, item.home) for item in layouts):
            continue
        layouts.append(Layout(home=home, browser_dir=browser_dir, profile_dir=profile))
    return layouts


def in_use(path: Path) -> bool:
    """Whether a running process holds this executable.

    Windows refuses write access to an image that is being executed, which makes
    opening the file for writing a cheap and exact test - and it changes nothing.
    """
    if not path.is_file() or not os.access(path, os.W_OK):
        return False
    try:
        handle = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except PermissionError:
        return True
    except OSError:
        return False
    os.close(handle)
    return False


def file_string(path: Path, name: str) -> str:
    """A named string from a Windows binary's version resource, e.g. `ProductName`."""
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
        pairs: list[tuple[int, int]] = []
        if version_api.VerQueryValueW(
            buffer, ctypes.c_wchar_p("\\VarFileInfo\\Translation"), ctypes.byref(block), ctypes.byref(length)
        ) and length.value >= 4:
            words = ctypes.cast(block, ctypes.POINTER(ctypes.c_uint16 * (length.value // 2))).contents
            pairs = [(words[index], words[index + 1]) for index in range(0, len(words) - 1, 2)]
        pairs.append((0x0409, 0x04B0))
        for language, codepage in pairs:
            query = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{name}"
            if version_api.VerQueryValueW(
                buffer, ctypes.c_wchar_p(query), ctypes.byref(block), ctypes.byref(length)
            ) and length.value:
                return ctypes.wstring_at(block.value, length.value).rstrip("\x00").strip()
        return ""
    except Exception:  # noqa: BLE001 - identification is a check, never a crash
        return ""


def _rename_case(path: Path, name: str) -> Path:
    """Give an existing folder the exact spelling the standard uses."""
    if path.name == name:
        return path
    target = path.with_name(name)
    parked = path.with_name(f"{name}.case-{os.getpid()}")
    path.rename(parked)
    parked.rename(target)
    return target


def is_browser_item(item: Path, names: frozenset[str]) -> bool:
    """Whether an entry beside the executable is the browser's and goes with it.

    Only what is certain: an entry the new browser carries under the same name,
    a folder named after a version, a wrapper's own files. Anything else may be
    a person's own, and is kept.
    """
    name = item.name.casefold()
    if name in names or name in WRAPPER_FILES:
        return True
    return bool(_VERSION_DIRECTORY.fullmatch(item.name)) and item.is_dir()


def _has_files(folder: Path) -> bool:
    return any(files for _root, _dirs, files in os.walk(folder))


def _set_aside(home: Path, item: Path) -> str:
    """Move `item` into the build's `Old files`; the name it has there."""
    aside = home / ASIDE_NAME
    aside.mkdir(exist_ok=True)
    target = aside / item.name
    number = 2
    while target.exists():
        target = aside / f"{item.stem} ({number}){item.suffix}"
        number += 1
    item.rename(target)
    return target.name


def _keep_what_is_not_the_browser(home: Path, branch: Path, browser_dir: Path, names: frozenset[str]) -> list[str]:
    """Take out of the browser's branch whatever is not the browser.

    The branch is about to be replaced as a whole, and it may hold more than
    the browser: PortableApps keeps folders of its own in `App` beside
    `Chrome-bin`, and a person may keep a file beside the executable. On the way
    down to the executable everything off that path is moved aside, and beside
    the executable everything that is not certainly the browser's.
    """
    kept: list[str] = []
    level = branch
    for part in Path(os.path.relpath(browser_dir, branch)).parts:
        if part == ".":
            continue
        for item in list(level.iterdir()):
            if item.name.lower() != part.lower():
                kept.append(_set_aside(home, item))
        level = level / part
    for item in list(level.iterdir()):
        if not is_browser_item(item, names):
            kept.append(_set_aside(home, item))
    return kept


def _clear_the_way_for_app(home: Path, log: Log) -> None:
    app = home / "App"
    if app.exists():
        aside = home / f"App.foreign-{os.getpid()}"
        app.rename(aside)
        log(f"[ADOPT] an unrelated App folder was left in place as {aside.name}")


def normalize(layout: Layout, log: Log, remove_tree: Callable[[Path], None], browser_names: Iterable[str]) -> None:
    """Rearrange a foreign build into `App`, `Data` and `Cache`, in place.

    The profile goes first and only by renaming, so the one thing that cannot be
    downloaded again is never at risk; if the browser is still running, that very
    first rename fails and nothing has been touched. The old browser ends up in
    `App`, where the regular update replaces it with a fresh one.

    `browser_names` are the entries the new browser has beside its executable.
    They tell what in the old place is the browser's. The rest is not judged
    here and is never deleted: it stays where it was, or goes into `Old files`
    when it is in the way.
    """
    home = layout.home
    stamp = os.getpid()
    data, app = home / "Data", home / "App"
    names = frozenset(name.casefold() for name in browser_names)
    kept: list[str] = []

    profile = layout.profile_dir
    if profile is not None and not same_path(profile, data):
        shown = os.path.relpath(profile, home)
        parked = home / f"Data.adopting-{stamp}"
        profile.rename(parked)
        if data.exists():
            # What is left of a foreign `Data` once the profile is out of it.
            # Anything that still looks like a profile is kept beside the build;
            # launcher settings, or somebody's files, go into `Old files`. Only
            # empty folders are removed.
            if looks_like_profile(data) or find_profile(data) is not None:
                aside = home / f"Data.foreign-{stamp}"
                data.rename(aside)
                log(f"[ADOPT] another profile was left in place as {aside.name}")
            elif _has_files(data):
                kept.append(_set_aside(home, data))
            else:
                remove_tree(data)
        parked.rename(data)
        log(f"[ADOPT] profile: {shown} -> Data")
    elif profile is not None:
        _rename_case(profile, "Data")

    for child in _subdirectories(home):
        if child.name.lower() == "cache":
            _rename_case(child, "Cache")

    if same_path(layout.browser_dir, home):
        # Flat build: the browser lies among whatever else the folder holds. Only
        # what is certainly the browser goes into App; the rest stays where it is.
        staging = home / f"App.adopting-{stamp}"
        staging.mkdir()
        for item in list(home.iterdir()):
            if same_path(item, staging) or item.name.lower() in {"app", "data", "cache"}:
                continue
            if is_browser_item(item, names):
                item.rename(staging / item.name)
        _clear_the_way_for_app(home, log)
        staging.rename(app)
        log("[ADOPT] browser: its files in the build folder -> App")
    else:
        top = _branch_top(home, layout.browser_dir)
        inner = Path(os.path.relpath(layout.browser_dir, top))
        if same_path(top, app):
            _rename_case(top, "App")
        else:
            _clear_the_way_for_app(home, log)
            shown = top.name
            top.rename(app)
            log(f"[ADOPT] browser: {shown} -> App")
        kept.extend(_keep_what_is_not_the_browser(home, app, app / inner, names))

    # What another packer left at the root to start the build no longer starts
    # it. It is put away rather than deleted: a file with such a name may just
    # as well be the person's own.
    for item in sorted(home.iterdir(), key=lambda entry: entry.name.lower()):
        lowered = item.name.lower()
        if item.is_file() and item.suffix.lower() in LAUNCHER_SUFFIXES:
            kept.append(_set_aside(home, item))
        elif item.is_dir() and lowered not in {"app", "data", "cache", ASIDE_NAME.lower()} and not _is_work_folder(lowered):
            log(f"[ADOPT] left as it is: {item.name}\\")
    if kept:
        log(f"[ADOPT] moved into '{ASIDE_NAME}' instead of being deleted: {', '.join(kept)}")
