"""Interactive CLI implementation of the permission resolver.

The resolver owns all terminal I/O: it renders prompts via :mod:`cli.prompt`
and reads the user's choice with ``input()``. Non-interactive stdin (piped
input, CI) fails closed — a gated tool is denied rather than silently allowed.
"""

from __future__ import annotations

import sys

from core.permissions import (
    AutoAllowResolver,
    AutoDenyResolver,
    Decision,
    PermissionOutcome,
)
from cli import prompt

# Permission modes accepted by ``--permission-mode``.
PERMISSION_MODES = ("allow", "deny", "ask")


def _stdin_is_tty() -> bool:
    """Whether stdin is an interactive terminal (fail closed on any error)."""
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


class CliPermissionResolver:
    """Prompts the user for a decision on a gated tool call.

    ``input_fn`` / ``output_fn`` / ``is_tty`` are injectable so the resolver can
    be unit-tested without a real terminal. In interactive mode the prompt
    blocks indefinitely (the user is present) — the per-call tool timeout only
    applies *after* the gate.
    """

    def __init__(self, input_fn=None, output_fn=None, is_tty=None, scope="session"):
        self._input = input_fn or input
        self._output = output_fn or print
        self._is_tty = is_tty or _stdin_is_tty
        self.scope = scope

    def resolve(self, request) -> PermissionOutcome:
        # Fail closed: never silently allow when there is nobody to ask.
        if not self._is_tty():
            self._output(
                f"[Permission] Non-interactive stdin — denying "
                f"'{request.tool_name}'. Use --permission-mode allow / --yes "
                f"to auto-approve.",
                file=sys.stderr,
            )
            return PermissionOutcome(False, None, Decision.DENY_ONCE)

        self._output(prompt.render_prompt(request, self.scope))
        while True:
            try:
                choice = self._input(prompt.CHOICE_PROMPT).strip().lower()
            except EOFError:
                self._output("\n  End of input — denying.")
                return PermissionOutcome(False, None, Decision.DENY_ONCE)

            if choice in ("o", "1"):
                return PermissionOutcome(True, None, Decision.ALLOW_ONCE)
            if choice in ("a", "2"):
                return PermissionOutcome(True, None, Decision.ALLOW_ALWAYS)
            if choice in ("d", "3"):
                return PermissionOutcome(False, None, Decision.DENY_ONCE)
            if choice in ("n", "4"):
                return PermissionOutcome(False, None, Decision.DENY_ALWAYS)
            if choice in ("m", "5"):
                message = self._read_message()
                if message:
                    return PermissionOutcome(False, message, Decision.DENY_WITH_MESSAGE)
                return PermissionOutcome(False, None, Decision.DENY_ONCE)

            self._output("  Invalid choice. Enter o / a / d / n / m.")

    def _read_message(self) -> str:
        """Read the free-text redirect for a deny-with-message decision."""
        self._output("  Enter a message for the model:")
        try:
            return self._input("  message> ").strip()
        except EOFError:
            return ""


def build_resolver(mode, scope: str = "session", is_tty=None):
    """Map a ``--permission-mode`` value to a resolver.

    ``allow`` -> auto-allow (non-interactive escape hatch);
    ``deny``  -> auto-deny;
    ``ask`` (and any unrecognized value) -> interactive CLI resolver, which
    itself fails closed when stdin is not a TTY.
    """
    normalized = (mode or "").strip().lower()
    if normalized == "allow":
        return AutoAllowResolver()
    if normalized == "deny":
        return AutoDenyResolver()
    return CliPermissionResolver(scope=scope, is_tty=is_tty)
