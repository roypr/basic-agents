"""SSE transport smoke test, all in one process (no background shell process).

Same shape as ``smoke_http_inproc.py``: the real MCP server runs under uvicorn
in a daemon thread, and our ``McpServer`` connects over loopback.
"""

import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

import uvicorn  # noqa: E402

from smoke_server import server as mcp_server  # noqa: E402
from utils.mcp.config import McpServerConfig  # noqa: E402
from utils.mcp.connection import McpServer  # noqa: E402

PORT = 8792
failures = []


def check(label, value, *expected):
    ok = all(item in (value or "") for item in expected)
    print(f"{'PASS' if ok else 'FAIL'} {label}: {value!r}")
    if not ok:
        failures.append(label)


def main():
    app = mcp_server.sse_app()
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

    server = McpServer(
        McpServerConfig(
            name="smoke-sse",
            transport="sse",
            url=f"http://127.0.0.1:{PORT}/sse",
            headers={"Authorization": "Bearer ignored-by-this-server"},
            timeout=30.0,
        )
    )
    try:
        tools = server.tools
        check("tools", ", ".join(t.name for t in tools), "echo")
        print(f"INFO capabilities: {server.capabilities}")
        check("call_tool", server.call_tool("echo", {"text": "hi"}), "echo: hi")
        check("list_resources", server.list_resources(), "hello.txt")
        check("get_prompt", server.get_prompt("greet", {"who": "Ada"}), "Ada")
    finally:
        started = time.monotonic()
        server.close()
        print(f"INFO close() took {time.monotonic() - started:.2f}s")
        uvicorn_server.should_exit = True
        thread.join(10)

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
