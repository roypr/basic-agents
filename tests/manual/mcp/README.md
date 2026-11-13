# Manual MCP smoke tests

These scripts exercise `utils/mcp/connection.py` against the **real** `mcp`
package over all three transports. They are not collected by pytest (the SDK
is an optional dependency and these spawn processes / bind ports).

They were used to verify the transport layer against `mcp` 2.3.0 and 1.30.0;
re-run them after bumping the `mcp` requirement or changing `connection.py`.

## Run

```bash
# throwaway venv so the project/env is untouched
python -m venv /tmp/mcp-smoke
/tmp/mcp-smoke/bin/pip install "mcp>=1.2,<3"

/tmp/mcp-smoke/bin/python tests/manual/mcp/smoke_client.py stdio
/tmp/mcp-smoke/bin/python tests/manual/mcp/smoke_http_inproc.py
/tmp/mcp-smoke/bin/python tests/manual/mcp/smoke_sse_inproc.py
```

Pin `"mcp<2"` in a second venv to check the v1 dialect (`cursor=` pagination,
`httpx.AsyncClient` for `http_client=`, the 3-tuple transport streams).

Note: `mcp` 1.9.x does not import with `pydantic>=2.11` (it wants
`eval_type_backport`, removed from pydantic), which is an upstream
incompatibility rather than a problem here.

## What each script covers

| Script | Covers |
|---|---|
| `smoke_server.py` | A real server exposing one tool, one resource and one prompt; works on both SDK lines (`MCPServer` / `FastMCP`), and the HTTP mode requires a bearer token |
| `smoke_client.py` | stdio: connect, list tools, `call_tool`, resources, prompts, idempotent `close()` |
| `smoke_http_inproc.py` | Streamable HTTP against a live server: auth rejection with the wrong token, full session with the right one, session teardown (`DELETE`) |
| `smoke_sse_inproc.py` | SSE transport against a live server |

The HTTP/SSE servers run under uvicorn in a daemon thread inside the same
process, so nothing is left listening and no shell background job is needed.
