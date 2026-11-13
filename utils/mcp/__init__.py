"""Optional MCP (Model Context Protocol) tool support.

MCP servers expose JSON-Schema tools, which map one-to-one onto this
framework's tool list, so enabling MCP does not touch the agent loop, the
provider adapters, the session database or the permission engine:

* :mod:`~utils.mcp.config` — ``mcp.json`` loading, validation, ``${ENV}``
* :mod:`~utils.mcp.connection` — one server (stdio/http/sse), asyncio bridged
  to blocking calls
* :mod:`~utils.mcp.adapter` — schema and result translation
* :mod:`~utils.mcp.manager` — shared registry + ``McpToolProvider``

The ``mcp`` package itself is optional and imported lazily; without it the
rest of the application runs normally.
"""

from .config import (
    DEFAULT_CONFIG_PATH,
    McpConfig,
    McpServerConfig,
    load_mcp_config,
    parse_mcp_config,
)
from .errors import McpConfigError, McpError
from .manager import (
    McpToolProvider,
    close_mcp_server,
    close_mcp_servers,
    connected_servers,
    describe_servers,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "McpConfig",
    "McpConfigError",
    "McpError",
    "McpServerConfig",
    "McpToolProvider",
    "close_mcp_server",
    "close_mcp_servers",
    "connected_servers",
    "describe_servers",
    "load_mcp_config",
    "parse_mcp_config",
]
