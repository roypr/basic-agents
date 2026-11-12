"""Drive our McpServer against the real MCP SDK.

Usage:
    python smoke_client.py stdio
    python smoke_client.py http <port> <token>
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from utils.mcp.config import McpServerConfig  # noqa: E402
from utils.mcp.connection import McpServer  # noqa: E402

failures = []


def check(label, value, *expected):
    ok = all(item in (value or "") for item in expected)
    print(f"{'PASS' if ok else 'FAIL'} {label}: {value!r}")
    if not ok:
        failures.append(label)


def main():
    mode = sys.argv[1]
    if mode == "stdio":
        config = McpServerConfig(
            name="smoke",
            command=sys.executable,
            args=(str(pathlib.Path(__file__).with_name("smoke_server.py")), "stdio"),
            timeout=30.0,
        )
    else:
        port, token = sys.argv[2], sys.argv[3]
        config = McpServerConfig(
            name="smoke",
            transport="http",
            url=f"http://127.0.0.1:{port}/mcp",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    server = McpServer(config)
    try:
        tools = server.tools
        check("tools", ", ".join(t.name for t in tools), "echo")
        check("description", tools[0].description, "Echo the given text")
        print(f"INFO capabilities: {server.capabilities}")
        check("call_tool", server.call_tool("echo", {"text": "hi"}), "echo: hi")
        check("list_resources", server.list_resources(), "hello.txt")
        check(
            "read_resource",
            server.read_resource("file:///hello.txt"),
            "hello from the smoke server",
        )
        check("list_prompts", server.list_prompts(), "greet")
        check("get_prompt", server.get_prompt("greet", {"who": "Ada"}), "Ada")
    finally:
        started = time.monotonic()
        server.close()
        print(f"INFO close() took {time.monotonic() - started:.2f}s")
        server.close()  # idempotent
        print(f"INFO after close: started={server._started} loop={server._loop}")

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
