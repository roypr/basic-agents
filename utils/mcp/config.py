"""``mcp.json`` loading and validation.

The config is intentionally small and static (no runtime reloading): it is
read whenever an agent is constructed, and connections are reused across
agents, so editing the file requires a restart — a changed server definition
is reported instead of being silently swapped under a live connection.

Shape::

    {
      "enabled": true,
      "safe_tools": ["filesystem__read_file"],
      "servers": {
        "filesystem": {
          "transport": "stdio",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
          "env": {"TOKEN": "${GITHUB_TOKEN}"},
          "agents": ["code"],
          "allowed_tools": ["read_file"],
          "timeout": 60
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import McpConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "mcp.json"

# Escape hatch for CI / sandboxes / debugging: force MCP off process-wide.
DISABLE_ENV_VAR = "BASIC_AGENTS_NO_MCP"

SUPPORTED_TRANSPORTS = ("stdio", "http", "sse")

# Accept the spellings people copy out of docs and other clients.
_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "http": "http",
    "streamable-http": "http",
    "streamable_http": "http",
    "streamablehttp": "http",
    "streamable": "http",
    "sse": "sse",
}

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


@dataclass(frozen=True)
class McpServerConfig:
    """One MCP server entry from ``mcp.json``."""

    name: str
    # stdio
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    # http / sse
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # shared
    transport: str = "stdio"
    # Empty tuple means "every agent"; otherwise only these agent directories.
    agents: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    timeout: float = 60.0
    # Fail agent construction when this server cannot start.
    required: bool = False
    # stdio servers need PATH/HOME to run npx, node, uv, ... by default.
    inherit_env: bool = True
    # Expose resources/prompts as synthetic tools (only when the server
    # advertises the capability). Off by default to keep the tool list small.
    expose_resources: bool = False
    expose_prompts: bool = False

    def applies_to(self, agent_name: str) -> bool:
        return not self.agents or agent_name in self.agents

    def allows_tool(self, tool_name: str) -> bool:
        if tool_name in self.denied_tools:
            return False
        if self.allowed_tools:
            return tool_name in self.allowed_tools
        return True


@dataclass(frozen=True)
class McpConfig:
    """The whole ``mcp.json`` document."""

    enabled: bool = False
    safe_tools: tuple[str, ...] = ()
    servers: tuple[McpServerConfig, ...] = ()
    path: Path | None = None

    def for_agent(self, agent_name: str) -> tuple[McpServerConfig, ...]:
        if not self.enabled:
            return ()
        return tuple(server for server in self.servers if server.applies_to(agent_name))


def _env_expand(value, *, where: str):
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in config strings."""

    def substitute(text: str) -> str:
        def replace(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise McpConfigError(
                f"Environment variable '{name}' is not set (referenced by {where}).",
                hint=f"export {name}=... before starting the agent",
            )

        return _ENV_RE.sub(replace, text)

    if isinstance(value, str):
        return substitute(value)
    if isinstance(value, (list, tuple)):
        return [_env_expand(item, where=where) for item in value]
    if isinstance(value, dict):
        return {key: _env_expand(item, where=where) for key, item in value.items()}
    return value


def _string_list(raw, *, server: str, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise McpConfigError(
            f"'{key}' for MCP server '{server}' must be a list of strings.",
            server=server,
        )
    return tuple(raw)


def _parse_server(name: str, raw) -> McpServerConfig:
    if not isinstance(raw, dict):
        raise McpConfigError(f"MCP server '{name}' must be an object.", server=name)

    where = f"server '{name}'"
    expanded = _env_expand(raw, where=where)

    raw_transport = str(expanded.get("transport") or "stdio").strip().lower()
    transport = _TRANSPORT_ALIASES.get(raw_transport)
    if transport is None:
        supported = ", ".join(SUPPORTED_TRANSPORTS)
        raise McpConfigError(
            f"MCP server '{name}' uses transport '{raw_transport}', which is "
            f"not supported (supported: {supported}).",
            server=name,
            hint='use "transport": "stdio" with a command, or "http" with a url',
        )

    command = expanded.get("command")
    url = expanded.get("url")
    if transport == "stdio":
        if not isinstance(command, str) or not command.strip():
            raise McpConfigError(
                f"MCP server '{name}' needs a 'command' to launch over stdio.",
                server=name,
                hint='e.g. "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]',
            )
        command = command.strip()
    else:
        if not isinstance(url, str) or not url.strip():
            raise McpConfigError(
                f"MCP server '{name}' needs a 'url' for the {transport} transport.",
                server=name,
                hint='e.g. "url": "https://api.example.com/mcp"',
            )
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise McpConfigError(
                f"'url' for MCP server '{name}' must be an http(s) URL.",
                server=name,
            )
        command = ""

    args = expanded.get("args") or []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise McpConfigError(
            f"'args' for MCP server '{name}' must be a list of strings.", server=name
        )

    env = expanded.get("env") or {}
    headers = expanded.get("headers") or {}
    for key, value in (("env", env), ("headers", headers)):
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise McpConfigError(
                f"'{key}' for MCP server '{name}' must be an object of string values.",
                server=name,
            )

    timeout = expanded.get("timeout", 60)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise McpConfigError(
            f"'timeout' for MCP server '{name}' must be a number of seconds.",
            server=name,
        ) from None
    if timeout <= 0:
        raise McpConfigError(
            f"'timeout' for MCP server '{name}' must be greater than zero.",
            server=name,
        )

    return McpServerConfig(
        name=name,
        command=command,
        args=tuple(args),
        env=dict(env),
        url=url or "",
        headers=dict(headers),
        transport=transport,
        agents=_string_list(expanded.get("agents"), server=name, key="agents"),
        allowed_tools=_string_list(
            expanded.get("allowed_tools"), server=name, key="allowed_tools"
        ),
        denied_tools=_string_list(
            expanded.get("denied_tools"), server=name, key="denied_tools"
        ),
        timeout=timeout,
        required=bool(expanded.get("required", False)),
        inherit_env=bool(expanded.get("inherit_env", True)),
        expose_resources=bool(expanded.get("expose_resources", False)),
        expose_prompts=bool(expanded.get("expose_prompts", False)),
    )


def parse_mcp_config(data, *, path: Path | None = None) -> McpConfig:
    """Validate an already-decoded ``mcp.json`` document."""
    if not isinstance(data, dict):
        raise McpConfigError("mcp.json must contain a JSON object.")

    if os.environ.get(DISABLE_ENV_VAR):
        return McpConfig(enabled=False, path=path)

    enabled = bool(data.get("enabled", True))
    safe_tools = _string_list(
        data.get("safe_tools"), server="(global)", key="safe_tools"
    )

    raw_servers = data.get("servers") or {}
    if not isinstance(raw_servers, dict):
        raise McpConfigError("'servers' in mcp.json must be an object keyed by name.")

    servers = tuple(_parse_server(name, raw) for name, raw in raw_servers.items())
    return McpConfig(
        enabled=enabled and bool(servers),
        safe_tools=safe_tools,
        servers=servers,
        path=path,
    )


def load_mcp_config(path: str | os.PathLike | None = None) -> McpConfig:
    """Load ``mcp.json``.

    A missing *default* file means "MCP not configured" and returns a disabled
    config.  A missing file passed explicitly to ``--mcp-config`` is an error,
    because silently ignoring it would hide a typo.
    """
    if os.environ.get(DISABLE_ENV_VAR):
        return McpConfig(enabled=False)

    explicit = path is not None
    config_path = Path(path) if explicit else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        if explicit:
            raise McpConfigError(f"MCP config not found: {config_path}")
        return McpConfig(enabled=False)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise McpConfigError(f"Could not read {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"{config_path} is not valid JSON: {exc}") from exc

    return parse_mcp_config(data, path=config_path)
