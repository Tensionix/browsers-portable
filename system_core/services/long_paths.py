"""Paths past the 260 characters Windows stops at.

Windows refuses an ordinary path longer than that unless long paths were
switched on for the whole machine, and they usually are not. A browser
installer nests its payload deep - Chrome's sits under
`bin\\Offline\\{GUID}\\{GUID}\\` - so the work folder runs past the limit as soon
as the Target is not a short one. 7-Zip writes such files without complaint;
Python then cannot see them, and the build reported `Chrome.7z was not found`.

The extended-length form (`\\\\?\\C:\\...`) lifts the limit for every call that is
given it, whatever the machine is set to. The work folders are handed out in
that form, and everything built under them inherits it. A person never needs
to see it: what goes into the log or into a result is the plain spelling.
"""

from __future__ import annotations

from pathlib import Path
import os


EXTENDED_PREFIX = "\\\\?\\"
# 260 counts the terminating null, so this many characters still open.
MAX_PLAIN_PATH = 259


def long_path(path: Path) -> Path:
    """The same path in the extended-length form, which has no 260 limit."""
    text = str(path)
    if os.name != "nt" or text.startswith(EXTENDED_PREFIX):
        return Path(path)
    text = os.path.abspath(text)
    if text.startswith("\\\\"):
        return Path(EXTENDED_PREFIX + "UNC\\" + text[2:])
    return Path(EXTENDED_PREFIX + text)


def plain_path(path: Path) -> Path:
    """The ordinary spelling of a path, for the log and for what cannot take the long form."""
    text = str(path)
    if text.startswith(EXTENDED_PREFIX + "UNC\\"):
        return Path("\\\\" + text[len(EXTENDED_PREFIX) + 4:])
    if text.startswith(EXTENDED_PREFIX):
        return Path(text[len(EXTENDED_PREFIX):])
    return Path(path)
