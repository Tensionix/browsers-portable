"""The program icon: the frames Windows asks for, and the same icon in Start.exe.

The icon this replaced had seven frames, every one of them stored as PNG, and
none at 20 or 40 px - the sizes a display at 125% takes in place of 16 and 32.
"""

from __future__ import annotations

from pathlib import Path
import ctypes
import os
import struct

import pytest

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "system_core" / "icons"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FRAMES = (16, 20, 24, 32, 40, 48, 64, 256)


def _ico_frames(path: Path) -> list[tuple[int, bytes]]:
    """`(size, image bytes)` of every frame, in the order the file lists them."""
    data = path.read_bytes()
    _reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert kind == 1, "not an icon file"
    frames = []
    for index in range(count):
        width, _height, _colors, _zero, _planes, _bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
        frames.append((width or 256, data[offset:offset + size]))
    return frames


def _exe_icons(path: Path) -> list[bytes]:
    """The icon images an executable carries, in the order of their resource ids."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel.FindResourceW.restype = wintypes.HRSRC
    kernel.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID]
    kernel.LoadResource.restype = wintypes.HGLOBAL
    kernel.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    kernel.LockResource.restype = ctypes.c_void_p
    kernel.LockResource.argtypes = [wintypes.HGLOBAL]
    kernel.SizeofResource.restype = wintypes.DWORD
    kernel.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPARAM)
    kernel.EnumResourceNamesW.argtypes = [wintypes.HMODULE, wintypes.LPVOID, callback_type, wintypes.LPARAM]

    rt_icon = 3
    module = kernel.LoadLibraryExW(str(path), None, 0x00000002 | 0x00000020)  # read as data, nothing is run
    assert module, f"cannot open {path.name} as a resource file"
    try:
        names: list[int] = []
        collect = callback_type(lambda _module, _kind, name, _param: names.append(int(name)) or True)
        kernel.EnumResourceNamesW(module, rt_icon, collect, 0)
        images = []
        for name in sorted(names):
            resource = kernel.FindResourceW(module, name, rt_icon)
            size = kernel.SizeofResource(module, resource)
            images.append(ctypes.string_at(kernel.LockResource(kernel.LoadResource(module, resource)), size))
        return images
    finally:
        kernel.FreeLibrary(module)


def test_the_icon_has_a_frame_for_every_size_windows_takes() -> None:
    assert [size for size, _image in _ico_frames(ICONS / "app.ico")] == list(FRAMES)


def test_only_the_largest_frame_is_stored_as_png() -> None:
    for size, image in _ico_frames(ICONS / "app.ico"):
        assert image.startswith(PNG_SIGNATURE) is (size == 256), f"the {size} px frame"


def test_the_icon_is_built_from_a_vector_drawing_kept_beside_it() -> None:
    assert (ICONS / "app.svg").read_text(encoding="utf-8").lstrip().startswith(("<?xml", "<svg"))


@pytest.mark.skipif(os.name != "nt", reason="reads the resources of a Windows executable")
def test_start_exe_carries_the_very_icon_of_the_window() -> None:
    """Start.exe holds a copy made when it was built; a new icon needs the launcher rebuilt."""
    launcher = ROOT / "Start.exe"
    if not launcher.is_file():
        pytest.skip("Start.exe has not been built here")

    assert _exe_icons(launcher) == [image for _size, image in _ico_frames(ICONS / "app.ico")], (
        "Start.exe was built with another icon - run install\\Build-StartLauncher.cmd"
    )
