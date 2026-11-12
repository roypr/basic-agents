"""Encoding-safe console helpers.

Windows terminals frequently report a legacy stdout encoding (``cp1252``,
``cp437``, ...).  Writing CJK or other non-encodable text then raises
``UnicodeEncodeError`` and can kill a stream mid-turn.  This module centralises
two small safeguards:

* :func:`configure_console` switches Git Bash / Windows Terminal streams to
  UTF-8 when the terminal is known to understand it, and otherwise makes
  encoding failures non-fatal by escaping unsupported characters.
* :func:`safe_write` / :func:`safe_print` fall back to escaped output if a
  particular write still fails.

The fallback is intentionally lossless-ish: unsupported characters become
``\\uXXXX`` escapes instead of raising or corrupting the rest of the stream.
"""

from __future__ import annotations

import os
import sys


def _prefers_utf8() -> bool:
    """Best-effort detection of a UTF-8 capable terminal.

    Git Bash sets ``MSYSTEM``; Windows Terminal sets ``WT_SESSION``; most
    modern Unix terminals advertise UTF-8 through ``TERM``/``LC_*``.  When in
    doubt we leave the existing encoding alone and only relax error handling.
    """
    if os.environ.get("PYTHONIOENCODING", "").lower().startswith("utf-8"):
        return True
    if os.environ.get("MSYSTEM") or os.environ.get("WT_SESSION"):
        return True
    term = os.environ.get("TERM", "").lower()
    if "utf" in term:
        return True
    for key in ("LC_ALL", "LC_CTYPE", "LANG"):
        if "utf-8" in os.environ.get(key, "").lower():
            return True
    return False


def configure_console() -> None:
    """Make stdout/stderr tolerant of characters the terminal cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            is_tty = bool(stream.isatty())
        except (AttributeError, OSError, ValueError):
            is_tty = False
        try:
            # UTF-8 is the right choice for Git Bash, Windows Terminal and
            # redirected/piped output.  Legacy interactive consoles keep their
            # code page but escape unsupported characters instead of crashing.
            if not is_tty or _prefers_utf8():
                reconfigure(encoding="utf-8", errors="replace")
            else:
                reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            # Captured streams (pytest, StringIO, redirected pipes) may not
            # support reconfiguration; that is fine.
            pass


def safe_write(text: str, stream=None, flush: bool = True) -> None:
    """Write ``text`` without ever raising ``UnicodeEncodeError``."""
    stream = stream or sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        escaped = text.encode(encoding, errors="backslashreplace").decode(
            encoding, errors="replace"
        )
        stream.write(escaped)
    if flush:
        stream.flush()


def safe_print(*args, **kwargs) -> None:
    """``print`` with the same keyword arguments, routed through safe_write."""
    file = kwargs.pop("file", None)
    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    flush = kwargs.pop("flush", False)
    if kwargs:
        unexpected = ", ".join(kwargs)
        raise TypeError(
            f"safe_print() got unexpected keyword argument(s): {unexpected}"
        )
    safe_write(sep.join(str(arg) for arg in args) + end, file, flush=flush)
