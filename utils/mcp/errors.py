"""Errors raised while configuring, starting or using an MCP server.

A failed *tool call* never raises into the agent loop: it becomes an
``Error: ...`` tool result so the model can react (and the session stays
valid).  These exceptions are for configuration and connection problems,
which are surfaced at construction time or collected as non-fatal warnings.
"""

from __future__ import annotations

import logging

from cli.style import bold, dim, red, yellow
from utils.logging_setup import configure_error_logging

logger = logging.getLogger("basic_agents")

KIND_CONFIG = "config"
KIND_CONNECT = "connect"
KIND_TIMEOUT = "timeout"
KIND_PROTOCOL = "protocol"
KIND_TOOL = "tool"


class McpError(RuntimeError):
    """One MCP server could not be configured, started or queried."""

    def __init__(
        self,
        message: str,
        *,
        server: str = "",
        kind: str = KIND_PROTOCOL,
        hint: str | None = None,
        cause: BaseException | None = None,
    ):
        self.message = " ".join(str(message or "").split()) or "MCP request failed"
        self.server = server or ""
        self.kind = kind
        self.hint = hint

    def render(self) -> str:
        """Short, actionable block for the terminal."""
        who = self.server or ("mcp.json" if self.kind == KIND_CONFIG else "server")
        header = f"[MCP error] {who} — {self.kind}"
        lines = [bold(red(header)), f"  {self.message}"]
        if self.hint:
            lines.append("  " + yellow("hint:") + " " + self.hint)
        lines.append(dim("  check mcp.json; use --no-mcp to start without MCP"))
        return "\n".join(lines)

    def __str__(self) -> str:  # keep generic handlers to one readable line
        return self.message


class McpConfigError(McpError):
    """``mcp.json`` is missing, invalid, or incomplete."""

    def __init__(self, message: str, *, server: str = "", hint: str | None = None):
        super().__init__(message, server=server, kind=KIND_CONFIG, hint=hint)


def log_error(error: McpError) -> None:
    """Write the diagnostic to ``logs/app_errors.log`` (never raises)."""
    configure_error_logging()
    logger.error(
        "MCP server failed (server=%s kind=%s)\nMessage: %s%s",
        error.server or "?",
        error.kind,
        error.message,
        f"\nHint: {error.hint}" if error.hint else "",
    )
