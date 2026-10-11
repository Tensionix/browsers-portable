"""The paint of a console taken off a line: what goes into a log file is its text.

The window on NiceGUI drew its terminal from this module - colour codes into HTML spans, the
output of a process decoded and redrawn. That window is gone (2.0.0), and with it everything
here but the one thing the engine itself needs: a line written to the log of an operation
without the escape sequences of whatever program printed it. The window on Avalonia reads the
colour codes itself (`Source\\Core\\Ansi.cs`), by the table this module used to hold.
"""
from __future__ import annotations

import re


# A pipe can swallow the escape and leave the rest of an SGR sequence behind.
# "[36m" is noise; "[OK]" is text, and the final "m" is what tells them apart.
ORPHAN_SGR = re.compile(r"\[[0-9;]*m")


def _escape_end(text: str, index: int) -> int:
    """Where the text goes on after the escape sequence that starts at `index`."""
    end = len(text)
    if index + 1 >= end:
        return index + 1

    marker = text[index + 1]
    if marker == "[":
        cursor = index + 2
        while cursor < end and not ("@" <= text[cursor] <= "~"):
            cursor += 1
        return end if cursor >= end else cursor + 1

    if marker == "]":
        # The title of a console window and the like: up to its end mark.
        cursor = index + 2
        while cursor < end:
            if text[cursor] == "\x07":
                return cursor + 1
            if text[cursor] == "\x1b" and cursor + 1 < end and text[cursor + 1] == "\\":
                return cursor + 2
            cursor += 1
        return end

    return min(index + 2, end)


def _is_control_char(char: str) -> bool:
    code = ord(char)
    if char in "\n\t":
        return False
    return code < 32 or code == 127 or 128 <= code <= 159


def strip_ansi(text: str) -> str:
    """Remove ANSI/OSC/control escapes while keeping printable Unicode text."""
    source = str(text)
    result: list[str] = []
    cursor = 0
    length = len(source)

    while cursor < length:
        char = source[cursor]
        if char == "\x1b":
            cursor = _escape_end(source, cursor)
            continue
        if _is_control_char(char):
            cursor += 1
            continue
        result.append(char)
        cursor += 1

    return ORPHAN_SGR.sub("", "".join(result))
