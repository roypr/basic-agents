"""Tiny zero-dependency ANSI styling helpers.

Colors are emitted only when the output stream is a TTY and the user has not
opted out via the ``NO_COLOR`` environment variable or ``TERM=dumb``. When
styling is disabled every helper returns its input unchanged, so piped output,
logs and tests stay plain text.

Usage::

    from cli.style import cyan, dim, bold

    print(cyan("basic-agents"))
    print(dim("[Think] ") + "reasoning")
"""

from __future__ import annotations

import os
import sys

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_ITALIC = "\x1b[3m"
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_BLUE = "\x1b[34m"
_MAGENTA = "\x1b[35m"
_CYAN = "\x1b[36m"
_GRAY = "\x1b[90m"


def _supports_color(stream=None) -> bool:
    """Whether ANSI colors should be emitted for ``stream`` (default stdout).

    Disabled when ``NO_COLOR`` is set, when ``TERM=dumb``, or when the stream is
    not a TTY — so redirected/piped output and captured test output stay plain.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def style(text: str, *codes: str) -> str:
    """Wrap ``text`` in ANSI ``codes``, or return it unchanged if unsupported."""
    if not codes or not _supports_color():
        return text
    return "".join(codes) + text + _RESET


def bold(text: str) -> str:
    return style(text, _BOLD)


def dim(text: str) -> str:
    return style(text, _DIM)


def italic(text: str) -> str:
    return style(text, _ITALIC)


def red(text: str) -> str:
    return style(text, _RED)


def green(text: str) -> str:
    return style(text, _GREEN)


def yellow(text: str) -> str:
    return style(text, _YELLOW)


def blue(text: str) -> str:
    return style(text, _BLUE)


def magenta(text: str) -> str:
    return style(text, _MAGENTA)


def cyan(text: str) -> str:
    return style(text, _CYAN)


def gray(text: str) -> str:
    return style(text, _GRAY)
