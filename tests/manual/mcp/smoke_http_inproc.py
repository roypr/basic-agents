"""Streamable HTTP smoke test, all in one process.

The MCP server runs under uvicorn in a daemon thread so no background shell
process (and no lingering pipe) is involved; our ``McpServer`` connects to it
over loopback, once with the wrong bearer token and once with the right one.
"""

import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

import uvicorn  # noqa: E402

from smoke_server import _http_app  # noqa: E402
from utils.mcp.config import McpServerConfig  # noqa: E402
from utils.mcp.connection import McpServer  # noqa: E402
from utils.mcp.errors import McpError  # noqa: E402

PORT = 8791
TOKEN = "smoke-token"
failures = []


def check(label, value, *expected):
    ok = all(item in (value or "") for item in expected)
    print(f"{'PASS' if ok else 'FAIL'} {label}: {value!r}")
    if not ok:
        failures.append(label)


def config_for(token, timeout):
    return McpServerConfig(
        name="smoke",
        transport="http",
        url=f"http://127.0.0.1:{PORT}/mcp",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )


def exercise(token, timeout):
    server = McpServer(config_for(token, timeout))
    try:
        tools = server.tools
        check("tools", ", ".join(t.name for t in tools), "echo")
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
        server.close()
        print(f"INFO after close: started={server._started} loop={server._loop}")


def main():
    app = _http_app(f"Bearer {TOKEN}")
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    )
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()

    for _ in range(200):
        if uvicorn_server.started:
            break
        time.sleep(0.05)
    if not uvicorn_server.started:
        print("FAIL: uvicorn did not start")
        return 1

    try:
        print("=== wrong token (server-side auth must reject it) ===")
        started = time.monotonic()
        try:
            exercise("wrong-token", 5.0)
        except McpError as exc:
            print(f"PASS rejected as expected: kind={exc.kind} msg={exc.message!r}")
            print(f"INFO rejection took {time.monotonic() - started:.2f}s")

        print("=== correct token (headers must reach the server) ===")
        exercise(TOKEN, 30.0)
    finally:
        uvicorn_server.should_exit = True
        thread.join(10)

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
