"""Pure rendering of permission prompts.

No I/O happens in this module: :mod:`cli.resolver` calls these helpers and owns
all ``input()``/``print()`` interaction. Keeping rendering pure makes the prompt
layout unit-testable without a terminal.
"""

from __future__ import annotations

import json

from core.permissions import PermissionRequest

# Long argument values are truncated so a single prompt stays readable.
DEFAULT_MAX_VALUE_LEN = 120

# The text handed to ``input()`` when reading the user's choice.
CHOICE_PROMPT = "  choice> "


def truncate(value, max_len: int = DEFAULT_MAX_VALUE_LEN) -> str:
    """Render ``value`` as a single-line string, truncated with an ellipsis."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    # Collapse newlines so multi-line commands stay on one prompt line.
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def format_args(args: dict, max_value_len: int = DEFAULT_MAX_VALUE_LEN) -> str:
    """Pretty-print a tool call's arguments, one ``key: value`` per line."""
    if not args:
        return "    (no arguments)"
    return "\n".join(
        f"    {key}: {truncate(value, max_value_len)}" for key, value in args.items()
    )


def render_choices(scope: str = "session") -> str:
    """Render the decision menu, labelling the standing-rule options by scope."""
    return "\n".join(
        [
            "  [o] allow once",
            f"  [a] allow always (this {scope})",
            "  [d] deny once",
            f"  [n] deny always (this {scope})",
            "  [m] deny with a message",
        ]
    )


def render_prompt(request: PermissionRequest, scope: str = "session") -> str:
    """Render a full permission prompt for a gated tool call."""
    return "\n".join(
        [
            "",
            "── Permission required ──",
            f"  tool: {request.tool_name}",
            "  args:",
            format_args(request.args),
            "",
            render_choices(scope),
        ]
    )
