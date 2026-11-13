"""Discover MCP tools and expose them as ordinary agent tools.

A process-wide registry keeps one connection per server: the REPL rebuilds its
agent on ``/provider``, ``/model`` and ``/agents`` switches, and respawning
stdio servers on every switch would be slow and leak child processes.  The
registry is closed once, from ``BaseAgent.shutdown()``.
"""

from __future__ import annotations

import threading

from .adapter import PUBLIC_PREFIX, public_tool_name, to_openai_tool
from .config import McpConfig, McpServerConfig, load_mcp_config
from .connection import McpServer
from .errors import McpError, log_error

_registry: dict[str, McpServer] = {}
_registry_lock = threading.Lock()


def _get_server(config: McpServerConfig) -> McpServer:
    """Return the shared connection for ``config``, starting it on first use."""
    with _registry_lock:
        server = _registry.get(config.name)
        if server is not None:
            if server.config != config:
                log_error(
                    McpError(
                        f"MCP server '{config.name}' is already connected with a "
                        "different configuration; restart to apply the change.",
                        server=config.name,
                        kind="config",
                    )
                )
            return server
        server = McpServer(config)
        _registry[config.name] = server

    # Start outside the lock: spawning a process must not block other servers.
    try:
        server.start()
    except McpError:
        with _registry_lock:
            if _registry.get(config.name) is server:
                del _registry[config.name]
        raise
    return server


def connected_servers() -> dict[str, McpServer]:
    """Snapshot of the registry (used by ``/mcp`` and tests)."""
    with _registry_lock:
        return dict(_registry)


def close_mcp_server(name: str) -> bool:
    """Drop and close one server so the next use reconnects it."""
    with _registry_lock:
        server = _registry.pop(name, None)
    if server is None:
        return False
    try:
        server.close()
    except Exception:  # noqa: BLE001 - teardown must not mask the caller
        pass
    return True


def describe_servers(config: McpConfig) -> list[dict]:
    """Connect every configured server and report what it exposes.

    Used by ``main.py mcp list``.  A failed server is reported, not raised, so
    one broken entry does not hide the rest.
    """
    rows: list[dict] = []
    for server_config in config.servers:
        row = {
            "name": server_config.name,
            "transport": server_config.transport,
            "target": server_config.url or server_config.command,
            "agents": ", ".join(server_config.agents) or "all",
            "tools": 0,
            "resources": False,
            "prompts": False,
            "error": None,
        }
        try:
            server = _get_server(server_config)
            row["tools"] = len(
                [
                    tool
                    for tool in server.tools
                    if server_config.allows_tool(getattr(tool, "name", ""))
                ]
            )
            row["resources"] = server.supports("resources")
            row["prompts"] = server.supports("prompts")
        except McpError as exc:
            log_error(exc)
            row["error"] = exc
        rows.append(row)
    return rows


def close_mcp_servers() -> None:
    """Close every connection; idempotent and never raises."""
    with _registry_lock:
        servers = list(_registry.values())
        _registry.clear()

    for server in servers:
        try:
            server.close()
        except Exception:  # noqa: BLE001 - teardown must not mask exit
            pass


def _is_safe(config: McpConfig, public_name: str) -> bool:
    """Whether the config pre-approves this tool (no permission prompt).

    Accepts the full name (``mcp__fs__read``), the bare form (``fs__read``),
    ``*`` and prefix wildcards (``mcp__fs__*``).
    """
    bare = (
        public_name[len(PUBLIC_PREFIX) :]
        if public_name.startswith(PUBLIC_PREFIX)
        else public_name
    )
    for entry in config.safe_tools:
        if entry in ("*", public_name, bare):
            return True
        if entry.endswith("*"):
            prefix = entry[:-1]
            if public_name.startswith(prefix) or bare.startswith(prefix):
                return True
    return False


def _make_caller(server: McpServer, server_name: str, tool_name: str):
    """Wrap a server tool as ``callable(**arguments) -> str``."""

    def call(**arguments) -> str:
        return server.call_tool(tool_name, arguments)

    call.__name__ = f"mcp_{server_name}_{tool_name}"
    return call


class McpToolProvider:
    """The MCP tools one agent should see.

    ``tools`` are OpenAI-style function schemas (accepted unchanged by every
    provider adapter) and ``tool_map`` maps the namespaced name to a blocking
    callable, so they slot into the existing agent loop untouched.
    """

    def __init__(self, agent_name: str, config: McpConfig | None = None):
        self.config = config if config is not None else load_mcp_config()
        self.tools: list[dict] = []
        self.tool_map: dict[str, object] = {}
        self.safe_tool_names: set[str] = set()
        self.warnings: list[McpError] = []
        self._loaded_servers: list[tuple[str, int]] = []
        if self.config.enabled:
            self._load(agent_name)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _load(self, agent_name: str) -> None:
        for server_config in self.config.for_agent(agent_name):
            try:
                server = _get_server(server_config)
                mcp_tools = list(server.tools)
            except McpError as exc:
                if server_config.required:
                    raise
                # A dead optional server must not take the agent down with it.
                log_error(exc)
                self.warnings.append(exc)
                continue

            exposed = 0
            for mcp_tool in mcp_tools:
                original_name = getattr(mcp_tool, "name", "") or ""
                if not original_name or not server_config.allows_tool(original_name):
                    continue
                public_name = public_tool_name(server_config.name, original_name)
                if public_name in self.tool_map:
                    self.warnings.append(
                        McpError(
                            f"Duplicate MCP tool name '{public_name}' from server "
                            f"'{server_config.name}' was ignored.",
                            server=server_config.name,
                            kind="config",
                        )
                    )
                    continue
                self.tools.append(
                    to_openai_tool(public_name, mcp_tool, server=server_config.name)
                )
                self.tool_map[public_name] = _make_caller(
                    server, server_config.name, original_name
                )
                if _is_safe(self.config, public_name):
                    self.safe_tool_names.add(public_name)
                exposed += 1

            exposed += self._add_resource_and_prompt_tools(server_config, server)
            self._loaded_servers.append((server_config.name, exposed))

    def _add_resource_and_prompt_tools(
        self, server_config: McpServerConfig, server: McpServer
    ) -> int:
        """Expose resources/prompts as tools when configured and supported.

        Two tools per capability (list + fetch) instead of one per resource, so
        the schema payload stays small and templated URIs still work.
        """
        added = 0
        if server_config.expose_resources and server.supports("resources"):
            added += self._add_tool(
                server_config,
                server,
                "list_resources",
                "List the resources this MCP server exposes (URIs and mime types).",
                {"type": "object", "properties": {}},
                server.list_resources,
            )
            added += self._add_tool(
                server_config,
                server,
                "read_resource",
                "Read one MCP resource by URI. Use mcp__<server>__list_resources "
                "first to discover valid URIs.",
                {
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string", "description": "Resource URI"}
                    },
                    "required": ["uri"],
                },
                server.read_resource,
            )
        if server_config.expose_prompts and server.supports("prompts"):
            added += self._add_tool(
                server_config,
                server,
                "list_prompts",
                "List the prompt templates this MCP server exposes.",
                {"type": "object", "properties": {}},
                server.list_prompts,
            )
            added += self._add_tool(
                server_config,
                server,
                "get_prompt",
                "Render one MCP prompt template by name, with optional arguments.",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Prompt name"},
                        "arguments": {
                            "type": "object",
                            "description": "Prompt arguments (name -> value)",
                        },
                    },
                    "required": ["name"],
                },
                server.get_prompt,
            )
        return added

    def _add_tool(
        self,
        server_config: McpServerConfig,
        server: McpServer,
        tool_name: str,
        description: str,
        parameters: dict,
        caller,
    ) -> int:
        public_name = public_tool_name(server_config.name, tool_name)
        if public_name in self.tool_map:
            # A real MCP tool with this name wins; note the skipped synthetic.
            self.warnings.append(
                McpError(
                    f"Server '{server_config.name}' already exposes "
                    f"'{tool_name}'; the synthetic tool was skipped.",
                    server=server_config.name,
                    kind="config",
                )
            )
            return 0
        self.tools.append(
            {
                "type": "function",
                "function": {
                    "name": public_name,
                    "description": f"[{server_config.name}] {description}",
                    "parameters": parameters,
                },
            }
        )
        self.tool_map[public_name] = caller
        if _is_safe(self.config, public_name):
            self.safe_tool_names.add(public_name)
        return 1

    def report_lines(self) -> list[str]:
        """Short status lines for startup output (empty when MCP is off)."""
        if not self.config.enabled:
            return []
        lines = []
        if self.tools:
            servers = ", ".join(
                f"{name} ({count} tools)" for name, count in self._loaded_servers
            )
            lines.append(f"[MCP] {len(self.tools)} tools from {servers}")
        for warning in self.warnings:
            lines.append(warning.render())
        return lines
