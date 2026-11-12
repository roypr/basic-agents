"""The stdio connection: asyncio bridge, pagination, timeouts, teardown.

The real ``mcp`` package is optional (and not required to run this suite), so
a small fake SDK is installed in ``sys.modules``.  Everything below the SDK
boundary — threading, timeouts, error wrapping, closing — is the real code.
"""

import asyncio
import sys
import time
import types

import pytest

from utils.mcp import connection
from utils.mcp.config import McpServerConfig
from utils.mcp.connection import McpServer
from utils.mcp.errors import KIND_CONNECT, KIND_TIMEOUT, McpError


def _tool(name, schema=None):
    return types.SimpleNamespace(
        name=name,
        description=f"{name} tool",
        input_schema=schema or {"type": "object", "properties": {}},
    )


class _FakeResult:
    def __init__(self, text="", is_error=False):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.is_error = is_error
        self.structured_content = None


def _page(state, cursor):
    """One page of ``tools/list``, shared by the v1 and v2 fake sessions."""
    tools = state["tools"]
    page_size = state["page_size"]
    start = int(cursor) if cursor else 0
    page = tools[start : start + page_size]
    next_cursor = str(start + page_size) if start + page_size < len(tools) else None
    return types.SimpleNamespace(tools=page, next_cursor=next_cursor)


class _PaginationParams:
    """Stand-in for ``mcp.types.PaginatedRequestParams`` (v2)."""

    def __init__(self, cursor=None):
        self.cursor = cursor


# The annotations the real SDK factories carry, used to pick the httpx dialect.
HTTPX2_ANNOTATION = "httpx2.AsyncClient | None"
HTTPX_ANNOTATION = "httpx.AsyncClient | None"


class _FakeTransport:
    def __init__(self, state):
        self._state = state

    async def __aenter__(self):
        self._state["transport_open"] = True
        return ("read-stream", "write-stream")

    async def __aexit__(self, *exc_info):
        self._state["transport_open"] = False


class _FakeSession:
    state: dict = {}

    def __init__(self, read, write):
        self.read = read
        self.write = write

    async def __aenter__(self):
        self.state["session_open"] = True
        return self

    async def __aexit__(self, *exc_info):
        self.state["session_open"] = False

    async def initialize(self):
        if self.state.get("raise_in_initialize"):
            raise RuntimeError("handshake failed")
        self.state["initialized"] += 1
        return types.SimpleNamespace(
            capabilities=types.SimpleNamespace(
                resources=object() if self.state["resources"] else None,
                prompts=object() if self.state["prompts"] else None,
            )
        )

    def _maybe_fail(self, method):
        if self.state.get("fail_method") == method:
            raise RuntimeError(f"{method} failed")

    async def list_tools(self, cursor=None):
        self.state["dialect"] = "cursor"
        return _page(self.state, cursor)

    async def call_tool(self, name, arguments=None):
        self.state["calls"].append((name, arguments))
        if self.state["delay"]:
            await asyncio.sleep(self.state["delay"])
        if self.state["raise_in_call"]:
            raise RuntimeError("server crashed")
        return _FakeResult(text=self.state["reply"], is_error=self.state["is_error"])

    async def list_resources(self):
        self._maybe_fail("list_resources")
        return types.SimpleNamespace(
            resources=[
                types.SimpleNamespace(
                    uri="file:///a.txt", name="a.txt", mime_type="text/plain"
                )
            ]
        )

    async def read_resource(self, uri):
        self._maybe_fail("read_resource")
        return types.SimpleNamespace(
            contents=[
                types.SimpleNamespace(
                    uri=uri, text=f"content of {uri}", mime_type="text/plain"
                )
            ]
        )

    async def list_prompts(self):
        self._maybe_fail("list_prompts")
        return types.SimpleNamespace(
            prompts=[
                types.SimpleNamespace(
                    name="greet",
                    description="Say hi",
                    arguments=[types.SimpleNamespace(name="who")],
                )
            ]
        )

    async def get_prompt(self, name, arguments=None):
        self._maybe_fail("get_prompt")
        return types.SimpleNamespace(
            description="A greeting",
            messages=[
                types.SimpleNamespace(
                    role="user",
                    content=types.SimpleNamespace(
                        type="text", text=f"{name}:{(arguments or {}).get('who')}"
                    ),
                )
            ],
        )


class _FakeSessionV2(_FakeSession):
    """v2 dialect: pagination travels in ``params=``, not ``cursor=``."""

    async def list_tools(self, *, params=None):
        self.state["dialect"] = "params"
        return _page(self.state, getattr(params, "cursor", None))


class _FakeParams:
    last: dict = {}

    def __init__(self, command, args=None, env=None):
        self.command = command
        self.args = args or []
        self.env = env or {}
        _FakeParams.last = {"command": command, "args": self.args, "env": self.env}


class _FakeHttpClient:
    last: dict = {}

    def __init__(self, headers=None, timeout=None):
        self.headers = headers or {}
        self.timeout = timeout
        _FakeHttpClient.last = {"headers": self.headers, "timeout": timeout}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None


class _FakeTimeout:
    def __init__(self, default=None, read=None):
        self.default = default
        self.read = read


def _install_fake_sdk(
    monkeypatch,
    *,
    tools,
    page_size=10,
    delay=0.0,
    reply="ok",
    is_error=False,
    raise_in_call=False,
    resources=False,
    prompts=False,
    fail_method=None,
    raise_in_initialize=False,
    http3tuple=True,
    with_httpx2=True,
    http_factory="v2",
    list_tools_dialect="v1",
):
    state = {
        "tools": list(tools),
        "page_size": page_size,
        "delay": delay,
        "reply": reply,
        "is_error": is_error,
        "raise_in_call": raise_in_call,
        "resources": resources,
        "prompts": prompts,
        "fail_method": fail_method,
        "raise_in_initialize": raise_in_initialize,
        "dialect": None,
        "calls": [],
        "initialized": 0,
        "session_open": False,
        "transport_open": False,
        "http": None,
        "sse": None,
    }
    session_cls = _FakeSessionV2 if list_tools_dialect == "v2" else _FakeSession
    session_cls.state = state
    _FakeSession.state = state
    _FakeParams.last = {}
    _FakeHttpClient.last = {}

    class _HttpTransport:
        def __init__(self, url, **kwargs):
            state["http"] = {"url": url, **kwargs}

        async def __aenter__(self):
            if http3tuple:
                return ("read", "write", "session-id")  # v1 streamablehttp shape
            return ("read", "write")

        async def __aexit__(self, *exc_info):
            return None

    class _SseTransport:
        def __init__(self, url, headers=None, **kwargs):
            state["sse"] = {"url": url, "headers": headers}

        async def __aenter__(self):
            return ("read", "write")

        async def __aexit__(self, *exc_info):
            return None

    mcp_module = types.ModuleType("mcp")
    client_module = types.ModuleType("mcp.client")
    stdio_module = types.ModuleType("mcp.client.stdio")
    http_module = types.ModuleType("mcp.client.streamable_http")
    sse_module = types.ModuleType("mcp.client.sse")

    mcp_module.ClientSession = session_cls
    mcp_module.StdioServerParameters = _FakeParams
    stdio_module.stdio_client = lambda params: _FakeTransport(state)

    # v2 pagination travels as mcp.types.PaginatedRequestParams(cursor=...).
    types_module = types.ModuleType("mcp.types")
    types_module.PaginatedRequestParams = _PaginationParams
    mcp_module.types = types_module
    monkeypatch.setitem(sys.modules, "mcp.types", types_module)

    if http_factory == "v1":
        # mcp 1.x: auth headers travel as a plain keyword
        def streamable_http_client(url, headers=None, timeout=30, **kwargs):
            return _HttpTransport(url, headers=headers)

        http_module.streamable_http_client = streamable_http_client

    elif http_factory == "v1-legacy":
        # mcp 1.2–1.2x spelled the function streamablehttp_client
        def streamablehttp_client(
            url, headers=None, timeout=30, sse_read_timeout=300, **kwargs
        ):
            return _HttpTransport(url, headers=headers)

        http_module.streamablehttp_client = streamablehttp_client

    elif http_factory == "v1-http-client":
        # mcp 1.x newest: http_client= carrying a plain httpx client
        def streamable_http_client(url, http_client: HTTPX_ANNOTATION = None, **kwargs):
            return _HttpTransport(url, http_client=http_client)

        http_module.streamable_http_client = streamable_http_client

    else:
        # mcp 2.x: the caller supplies an httpx2 client
        def streamable_http_client(
            url, http_client: HTTPX2_ANNOTATION = None, **kwargs
        ):
            return _HttpTransport(url, http_client=http_client)

        http_module.streamable_http_client = streamable_http_client

    def sse_client(url, headers=None, timeout=5, **kwargs):
        return _SseTransport(url, headers=headers)

    sse_module.sse_client = sse_client
    client_module.stdio = stdio_module
    client_module.streamable_http = http_module
    client_module.sse = sse_module
    mcp_module.client = client_module

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_module)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", http_module)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_module)

    if with_httpx2:
        httpx2_module = types.ModuleType("httpx2")
        httpx2_module.AsyncClient = _FakeHttpClient
        httpx2_module.Timeout = _FakeTimeout
        monkeypatch.setitem(sys.modules, "httpx2", httpx2_module)
    else:
        monkeypatch.setitem(sys.modules, "httpx2", None)  # import fails

    return state


def _config(**overrides):
    values = dict(name="fs", command="fake-server", args=(), timeout=5.0)
    values.update(overrides)
    return McpServerConfig(**values)


@pytest.mark.unit
class TestStartup:
    def test_tools_are_listed_across_pages(self, monkeypatch):
        state = _install_fake_sdk(
            monkeypatch, tools=[_tool("a"), _tool("b"), _tool("c")], page_size=2
        )
        server = McpServer(_config())

        try:
            tools = server.tools
        finally:
            server.close()

        assert [tool.name for tool in tools] == ["a", "b", "c"]
        assert state["initialized"] == 1
        assert state["dialect"] == "cursor"  # v1 passes the cursor directly

    def test_pagination_uses_the_v2_params_dialect(self, monkeypatch):
        """mcp 2.x replaced ``list_tools(cursor=...)`` with ``params=``."""
        state = _install_fake_sdk(
            monkeypatch,
            tools=[_tool("a"), _tool("b"), _tool("c")],
            page_size=2,
            list_tools_dialect="v2",
        )
        server = McpServer(_config())

        try:
            tools = server.tools
        finally:
            server.close()

        assert [tool.name for tool in tools] == ["a", "b", "c"]
        assert state["dialect"] == "params"

    def test_start_is_idempotent(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("a")])
        server = McpServer(_config())

        try:
            server.start()
            server.start()
            assert state["initialized"] == 1
        finally:
            server.close()

    def test_missing_sdk_is_reported_with_a_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "mcp", None)  # makes the import fail
        server = McpServer(_config())

        with pytest.raises(McpError) as excinfo:
            server.start()

        assert excinfo.value.kind == KIND_CONNECT
        assert "pip install" in (excinfo.value.hint or "")

    def test_failed_handshake_tears_down_the_transport(self, monkeypatch):
        """A failed start must not leak the transport, the loop or the lock."""
        state = _install_fake_sdk(
            monkeypatch, tools=[_tool("a")], raise_in_initialize=True
        )
        server = McpServer(_config())

        with pytest.raises(McpError) as excinfo:
            server.start()

        assert excinfo.value.kind == KIND_CONNECT
        assert state["transport_open"] is False
        assert state["session_open"] is False
        assert server._loop is None and server._thread is None

        # `_start_lock` must be released: retrying fails promptly instead of
        # deadlocking on the (non-reentrant) start lock.
        started = time.monotonic()
        with pytest.raises(McpError):
            server.start()
        assert time.monotonic() - started < 5
        server.close()

    def test_env_is_inherited_by_default(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[])
        monkeypatch.setenv("SOME_PATH_VAR", "/opt/bin")
        server = McpServer(_config(env={"EXTRA": "1"}))

        try:
            server.start()
        finally:
            server.close()

        assert _FakeParams.last["env"]["SOME_PATH_VAR"] == "/opt/bin"
        assert _FakeParams.last["env"]["EXTRA"] == "1"

    def test_env_can_be_isolated(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[])
        monkeypatch.setenv("SHOULD_NOT_LEAK", "1")
        server = McpServer(_config(env={"ONLY": "1"}, inherit_env=False))

        try:
            server.start()
        finally:
            server.close()

        assert _FakeParams.last["env"] == {"ONLY": "1"}


@pytest.mark.unit
class TestCalls:
    def test_arguments_are_forwarded_and_text_rendered(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("read")], reply="file body")
        server = McpServer(_config())

        try:
            result = server.call_tool("read", {"path": "a.txt"})
        finally:
            server.close()

        assert result == "file body"
        assert state["calls"] == [("read", {"path": "a.txt"})]

    def test_tool_error_result_is_returned_as_text(self, monkeypatch):
        _install_fake_sdk(
            monkeypatch, tools=[_tool("read")], reply="nope", is_error=True
        )
        server = McpServer(_config())

        try:
            result = server.call_tool("read", {})
        finally:
            server.close()

        assert result == "Error: nope"

    def test_timeout_becomes_an_error_result_not_an_exception(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[_tool("slow")], delay=1.0)
        server = McpServer(_config(timeout=0.05))

        try:
            result = server.call_tool("slow", {})
        finally:
            server.close()

        assert result.startswith("Error:")
        assert "did not answer" in result

    def test_timeout_raises_mcp_error_of_kind_timeout(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[_tool("slow")], delay=1.0)
        server = McpServer(_config(timeout=0.05))
        server.start()

        try:
            with pytest.raises(McpError) as excinfo:
                server._run(server._session.call_tool("slow", {}), 0.05)
        finally:
            server.close()

        assert excinfo.value.kind == KIND_TIMEOUT
        assert "timeout" in (excinfo.value.hint or "")

    def test_server_exception_becomes_an_error_result(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[_tool("boom")], raise_in_call=True)
        server = McpServer(_config())

        try:
            result = server.call_tool("boom", {})
        finally:
            server.close()

        assert result.startswith("Error:")
        assert "server crashed" in result


@pytest.mark.unit
class TestShutdown:
    def test_close_tears_down_and_is_idempotent(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("a")])
        server = McpServer(_config())
        server.start()
        assert state["session_open"] and state["transport_open"]

        server.close()
        server.close()

        assert state["session_open"] is False
        assert state["transport_open"] is False

    def test_call_after_close_reconnects(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("a")], reply="again")
        server = McpServer(_config())
        server.start()
        server.close()

        try:
            assert server.call_tool("a", {}) == "again"
        finally:
            server.close()

        assert state["initialized"] == 2


@pytest.mark.unit
class TestHttpTransport:
    def test_http_uses_the_v2_transport_and_carries_headers(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("a")])
        server = McpServer(
            _config(
                transport="http",
                command="",
                url="https://api.example.com/mcp",
                headers={"Authorization": "Bearer t"},
            )
        )

        try:
            assert [tool.name for tool in server.tools] == ["a"]
        finally:
            server.close()

        assert state["http"]["url"] == "https://api.example.com/mcp"
        client = state["http"]["http_client"]
        assert client is not None
        assert _FakeHttpClient.last["headers"] == {"Authorization": "Bearer t"}
        assert _FakeHttpClient.last["timeout"].read == 5.0

    def test_http_transport_tolerates_a_three_tuple_stream(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("a")], http3tuple=True)
        server = McpServer(_config(transport="http", command="", url="https://x/mcp"))

        try:
            assert server.tools
        finally:
            server.close()

        assert state["initialized"] == 1

    def test_http_v1_signature_takes_the_headers_kwarg(self, monkeypatch):
        state = _install_fake_sdk(
            monkeypatch, tools=[_tool("a")], with_httpx2=False, http_factory="v1"
        )
        server = McpServer(
            _config(
                transport="http",
                command="",
                url="https://x/mcp",
                headers={"Authorization": "Bearer t"},
            )
        )

        try:
            assert server.tools
        finally:
            server.close()

        assert state["http"]["headers"] == {"Authorization": "Bearer t"}

    def test_http_headers_without_an_http_client_is_an_error(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[_tool("a")], with_httpx2=False)
        server = McpServer(
            _config(
                transport="http",
                command="",
                url="https://x/mcp",
                headers={"Authorization": "Bearer t"},
            )
        )

        with pytest.raises(McpError) as excinfo:
            server.start()

        assert "no HTTP client" in str(excinfo.value)

    def test_v1_http_client_dialect_carries_headers_via_httpx(self, monkeypatch):
        """mcp 1.30 has the v2 module shape but expects a plain httpx client."""
        state = _install_fake_sdk(
            monkeypatch, tools=[_tool("a")], http_factory="v1-http-client"
        )
        monkeypatch.setitem(sys.modules, "httpx2", None)  # v2 client absent
        httpx_module = types.ModuleType("httpx")
        httpx_module.AsyncClient = _FakeHttpClient
        httpx_module.Timeout = _FakeTimeout
        monkeypatch.setitem(sys.modules, "httpx", httpx_module)

        server = McpServer(
            _config(
                transport="http",
                command="",
                url="https://x/mcp",
                headers={"Authorization": "Bearer t"},
            )
        )

        try:
            assert server.tools
        finally:
            server.close()

        assert _FakeHttpClient.last["headers"] == {"Authorization": "Bearer t"}
        assert state["http"]["http_client"] is not None

    def test_http_client_module_follows_the_factory_annotation(self, monkeypatch):
        httpx2_module = types.ModuleType("httpx2")
        httpx_module = types.ModuleType("httpx")
        monkeypatch.setitem(sys.modules, "httpx2", httpx2_module)
        monkeypatch.setitem(sys.modules, "httpx", httpx_module)

        def v2_factory(url, http_client: HTTPX2_ANNOTATION = None):
            return None

        def v1_factory(url, http_client: HTTPX_ANNOTATION = None):
            return None

        assert connection._http_client_module(v2_factory) is httpx2_module
        assert connection._http_client_module(v1_factory) is httpx_module

    def test_http_without_headers_still_connects(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("a")])
        server = McpServer(_config(transport="http", command="", url="https://x/mcp"))

        try:
            assert server.tools
        finally:
            server.close()

        assert state["http"]["http_client"] is None

    def test_sse_transport(self, monkeypatch):
        state = _install_fake_sdk(monkeypatch, tools=[_tool("a")])
        server = McpServer(
            _config(
                transport="sse",
                command="",
                url="https://x/sse",
                headers={"X-Key": "v"},
            )
        )

        try:
            assert server.tools
        finally:
            server.close()

        assert state["sse"] == {"url": "https://x/sse", "headers": {"X-Key": "v"}}

    def test_http_needs_the_sdk_transport(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[_tool("a")])
        monkeypatch.delitem(sys.modules, "mcp.client.streamable_http", raising=False)
        server = McpServer(_config(transport="http", command="", url="https://x/mcp"))

        with pytest.raises(McpError) as excinfo:
            server.start()

        assert "http transport" in str(excinfo.value)
        assert excinfo.value.server == "fs"


@pytest.mark.unit
class TestCapabilities:
    def test_capabilities_are_recorded(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[], resources=True, prompts=False)
        server = McpServer(_config())
        server.start()

        try:
            assert server.supports("resources") is True
            assert server.supports("prompts") is False
        finally:
            server.close()

    def test_resources_and_prompts_render(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[], resources=True, prompts=True)
        server = McpServer(_config())
        server.start()

        try:
            listing = server.list_resources()
            read = server.read_resource("file:///a.txt")
            prompts = server.list_prompts()
            prompt = server.get_prompt("greet", {"who": "Ada"})
        finally:
            server.close()

        assert "file:///a.txt" in listing and "text/plain" in listing
        assert "content of file:///a.txt" in read
        assert "greet(who)" in prompts and "Say hi" in prompts
        assert "A greeting" in prompt and "user: greet:Ada" in prompt

    def test_non_text_resource_reports_binary(self, monkeypatch):
        _install_fake_sdk(monkeypatch, tools=[], resources=True)
        server = McpServer(_config())
        server.start()

        async def blob(uri):
            return types.SimpleNamespace(
                contents=[
                    types.SimpleNamespace(
                        uri=uri, blob="QUJD", mime_type="application/pdf"
                    )
                ]
            )

        server._session.read_resource = blob
        try:
            result = server.read_resource("file:///b.pdf")
        finally:
            server.close()

        assert "binary resource file:///b.pdf" in result
        assert "application/pdf" in result

    def test_resource_failure_is_an_error_result(self, monkeypatch):
        _install_fake_sdk(
            monkeypatch, tools=[], resources=True, fail_method="read_resource"
        )
        server = McpServer(_config())

        try:
            result = server.read_resource("file:///a.txt")
        finally:
            server.close()

        assert result.startswith("Error:")
        assert "read_resource failed" in result


@pytest.mark.unit
def test_pagination_params_carry_the_cursor(monkeypatch):
    module = types.ModuleType("mcp.types")
    module.PaginatedRequestParams = _PaginationParams
    monkeypatch.setitem(sys.modules, "mcp.types", module)

    params = connection._pagination_params("page-2")

    assert params.cursor == "page-2"


@pytest.mark.unit
def test_pagination_params_are_none_without_the_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "mcp.types", types.ModuleType("mcp.types"))

    assert connection._pagination_params("page-2") is None


@pytest.mark.unit
def test_wrapping_a_messageless_exception_names_its_type(monkeypatch):
    """mcp 1.x raises bare transport errors; "... failed:" alone is useless."""
    _install_fake_sdk(monkeypatch, tools=[])
    server = McpServer(_config())

    error = server._wrap(TimeoutError(), KIND_CONNECT)

    assert error.message.endswith("failed: TimeoutError")


@pytest.mark.unit
def test_http_finds_the_legacy_v1_function_spelling(monkeypatch):
    """mcp 1.2-1.2x exposes streamablehttp_client, not streamable_http_client."""
    state = _install_fake_sdk(
        monkeypatch,
        tools=[_tool("a")],
        with_httpx2=False,
        http_factory="v1-legacy",
    )
    server = McpServer(
        _config(
            transport="http",
            command="",
            url="https://x/mcp",
            headers={"Authorization": "Bearer t"},
        )
    )

    try:
        assert [tool.name for tool in server.tools] == ["a"]
    finally:
        server.close()

    assert state["http"]["headers"] == {"Authorization": "Bearer t"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("transport", "expected"),
    [
        ("stdio", "command exists on PATH"),
        ("http", "headers/token"),
        ("sse", "headers/token"),
    ],
)
def test_connect_hint_matches_the_transport(monkeypatch, transport, expected):
    _install_fake_sdk(monkeypatch, tools=[])
    server = McpServer(_config(transport=transport))

    error = server._wrap(RuntimeError("boom"), KIND_CONNECT)

    assert expected in (error.hint or "")
