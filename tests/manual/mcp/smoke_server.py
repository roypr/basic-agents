"""A real MCP server (SDK 2.x) for the connection-layer smoke test.

Usage:
    python smoke_server.py stdio
    python smoke_server.py http <port>
"""

import sys

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

server = _Server("smoke")


@server.tool(description="Echo the given text back")
def echo(text: str) -> str:
    return f"echo: {text}"


@server.resource("file:///hello.txt", name="hello", mime_type="text/plain")
def hello() -> str:
    return "hello from the smoke server"


@server.prompt(description="Greet someone")
def greet(who: str) -> str:
    return f"Please greet {who} warmly."


def _http_app(required_auth: str):
    """The Streamable HTTP app, guarded by a header check."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class RequireAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("authorization") != required_auth:
                from starlette.responses import PlainTextResponse

                return PlainTextResponse("missing bearer token", status_code=401)
            return await call_next(request)

    app = server.streamable_http_app()
    app.add_middleware(RequireAuth)
    return app


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if mode == "stdio":
        server.run("stdio")
    else:
        import uvicorn

        port = int(sys.argv[2])
        token = sys.argv[3]
        uvicorn.run(
            _http_app(f"Bearer {token}"),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
