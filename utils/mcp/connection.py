"""One MCP server connection, adapted to a blocking API.

The agent loop and ``utils.tool_executor`` are synchronous and thread-pooled,
while the MCP SDK is asyncio.  Rather than colour the whole codebase async,
each server owns a private event loop on a daemon thread; calls are submitted
with ``asyncio.run_coroutine_threadsafe`` and awaited with a timeout.  The
async boundary therefore stops at this module.

The SDK is imported lazily so the application (and the test suite) runs without
the optional ``mcp`` package installed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import os
import threading
from types import SimpleNamespace

from .adapter import (
    render_prompt_list,
    render_prompt_result,
    render_resource_list,
    render_resource_result,
    render_tool_result,
)
from .config import McpServerConfig
from .errors import KIND_CONNECT, KIND_TIMEOUT, KIND_TOOL, McpError, log_error

_SDK_HINT = 'install it with: pip install "mcp"'


def _import_sdk() -> SimpleNamespace:
    """Import the optional SDK, translating ImportError into an McpError."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:  # pragma: no cover - exercised via a fake module
        raise McpError(
            "The 'mcp' package is not installed, so MCP servers cannot start.",
            kind=KIND_CONNECT,
            hint=_SDK_HINT,
            cause=exc,
        ) from exc
    return SimpleNamespace(
        ClientSession=ClientSession,
        StdioServerParameters=StdioServerParameters,
        stdio_client=stdio_client,
        sse_client=_optional_attr("mcp.client.sse", "sse_client"),
        # Both the module and the function were renamed across the 1.x line:
        #   1.2–1.2x  mcp.client.streamable_http.streamablehttp_client  (3 streams)
        #   2.x       mcp.client.streamable_http.streamable_http_client
        streamable_http_client=_first_attr(
            (
                ("mcp.client.streamable_http", "streamable_http_client"),
                ("mcp.client.streamable_http", "streamablehttp_client"),
                ("mcp.client.streamablehttp", "streamable_http_client"),
                ("mcp.client.streamablehttp", "streamablehttp_client"),
            )
        ),
    )


def _optional_module(name: str):
    """Import an SDK submodule, or return ``None`` when it does not exist."""
    try:
        return __import__(name, fromlist=["_"])
    except ImportError:
        return None


def _optional_attr(module_name: str, attribute: str):
    module = _optional_module(module_name)
    return getattr(module, attribute, None) if module is not None else None


def _first_attr(candidates):
    """First attribute present among ``(module_name, attribute)`` pairs."""
    for module_name, attribute in candidates:
        value = _optional_attr(module_name, attribute)
        if value is not None:
            return value
    return None


def _accepts(factory, parameter: str) -> bool:
    """Whether a transport factory takes ``parameter`` (signature-based)."""
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return False
    return parameter in parameters


def _field(obj, *names, default=None):
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _pagination_params(cursor: str):
    """Build the v2 pagination object (``PaginatedRequestParams``) if present.

    Returns ``None`` when the SDK has no such model, so the caller can fall
    back to the v1 ``cursor=`` keyword.
    """
    types = _optional_module("mcp.types")
    model = getattr(types, "PaginatedRequestParams", None) if types else None
    if model is None:
        return None
    try:
        return model(cursor=cursor)
    except Exception:  # noqa: BLE001 - unexpected model shape; use v1 dialect
        return None


def _http_client_module(factory):
    """The httpx module a ``http_client=`` transport expects.

    mcp 2.x ships ``httpx2``; the 1.x line used ``httpx``.  The factory's own
    annotation is authoritative, because installing the wrong client raises a
    type error deep inside the transport; the imported modules are only a
    fallback for unannotated factories.
    """
    annotation = ""
    try:
        parameter = inspect.signature(factory).parameters["http_client"]
        annotation = str(parameter.annotation)
    except (KeyError, TypeError, ValueError):
        annotation = ""
    if "httpx2" in annotation:
        return _optional_module("httpx2")
    if "httpx" in annotation:
        return _optional_module("httpx")
    return _optional_module("httpx2") or _optional_module("httpx")


class McpServer:
    """A live connection to one MCP server (stdio, Streamable HTTP or SSE)."""

    def __init__(self, config: McpServerConfig):
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._session_cm = None
        self._transport_cm = None
        self._http_client_cm = None
        self._tools: list = []
        self.capabilities: dict[str, bool] = {}
        self._start_lock = threading.Lock()
        self._started = False

    @property
    def name(self) -> str:
        return self.config.name

    def supports(self, capability: str) -> bool:
        """Whether the server advertised ``resources`` / ``prompts``."""
        return bool(self.capabilities.get(capability))

    def start(self) -> None:
        """Connect and cache the tool list. Idempotent; raises ``McpError``."""
        with self._start_lock:
            if self._started:
                return
            self._start_loop()
            try:
                self._tools = self._run(self._connect(), self.config.timeout)
            except McpError as exc:
                self._cleanup_failed_start()
                # Errors raised inside the SDK wrapper do not know which
                # server they came from; the provider groups them by name.
                exc.server = exc.server or self.name
                raise
            except Exception as exc:  # noqa: BLE001 - any startup failure
                self._cleanup_failed_start()
                raise self._wrap(exc, KIND_CONNECT) from exc
            self._started = True

    @property
    def tools(self) -> list:
        """The server's tool list, connecting on first use."""
        if not self._started:
            self.start()
        return self._tools

    def _cleanup_failed_start(self) -> None:
        """Tear down a half-open connection (e.g. the handshake failed).

        Without this a stdio child process could outlive the failed attempt,
        and the transport's async generator could never be finalised once the
        loop is stopped.

        Called from ``start()`` while ``_start_lock`` is held, so it must not
        call ``start()`` (``_start_lock`` is not reentrant).
        """
        if (
            self._session_cm is not None
            or self._transport_cm is not None
            or self._http_client_cm is not None
        ):
            try:
                self._run(self._close_session(), timeout=15)
            except Exception:  # noqa: BLE001 - already failing; best effort
                pass
        self._session = None
        self._session_cm = None
        self._transport_cm = None
        self._http_client_cm = None
        self._tools = []
        self.capabilities = {}
        self._stop_loop()

    def close(self) -> None:
        """Best-effort teardown; safe to call twice and never raises."""
        with self._start_lock:
            if self._loop is None and not self._started:
                return
            self._started = False
            if (
                self._session_cm is not None
                or self._transport_cm is not None
                or self._http_client_cm is not None
            ):
                try:
                    self._run(self._close_session(), timeout=15)
                except Exception:  # noqa: BLE001 - teardown must not mask exit
                    pass
            self._session = None
            self._session_cm = None
            self._transport_cm = None
            self._http_client_cm = None
            self._tools = []
            self.capabilities = {}
            self._stop_loop()

    # ---------------------------------------------------------------- calls

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> str:
        """Call one tool and return tool-result content (never raises)."""
        args = dict(arguments or {})
        return self._invoke(
            tool_name,
            lambda: self._session.call_tool(tool_name, arguments=args),
            lambda result: render_tool_result(result, tool_name=tool_name),
        )

    def list_resources(self) -> str:
        """Synthetic tool: enumerate the server's resources."""
        return self._invoke(
            "list_resources",
            lambda: self._session.list_resources(),
            render_resource_list,
        )

    def read_resource(self, uri: str) -> str:
        """Synthetic tool: read one resource by URI."""
        return self._invoke(
            "read_resource",
            lambda: self._session.read_resource(uri),
            render_resource_result,
        )

    def list_prompts(self) -> str:
        """Synthetic tool: enumerate the server's prompts."""
        return self._invoke(
            "list_prompts",
            lambda: self._session.list_prompts(),
            render_prompt_list,
        )

    def get_prompt(self, name: str, arguments: dict | None = None) -> str:
        """Synthetic tool: render one prompt by name."""
        args = dict(arguments or {})
        return self._invoke(
            name,
            lambda: self._session.get_prompt(name, arguments=args),
            render_prompt_result,
        )

    def _invoke(self, label: str, call, render) -> str:
        """Run one request; failures become ``Error: ...`` tool content."""
        if not self._started:
            try:
                self.start()
            except McpError as exc:
                log_error(exc)
                return f"Error: {exc.message}"

        try:
            result = self._run(call(), self.config.timeout)
        except McpError as exc:
            log_error(exc)
            hint = f" ({exc.hint})" if exc.hint else ""
            return f"Error: {exc.message}{hint}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the model
            error = self._wrap(exc, KIND_TOOL, tool_name=label)
            log_error(error)
            return f"Error: {error.message}"

        try:
            return render(result)
        except Exception as exc:  # noqa: BLE001 - a bad payload is still a result
            error = self._wrap(exc, KIND_TOOL, tool_name=label)
            log_error(error)
            return f"Error: {error.message}"

    # ------------------------------------------------------------- internals

    def _start_loop(self) -> None:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=self._run_loop, args=(loop,), name=f"mcp-{self.name}", daemon=True
        )
        self._loop = loop
        self._thread = thread
        thread.start()

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    def _stop_loop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop, self._thread = None, None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _run(self, coro, timeout: float | None = None):
        loop = self._loop
        if loop is None:
            raise McpError(
                f"MCP server '{self.name}' is not connected.",
                server=self.name,
                kind=KIND_CONNECT,
            )
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise McpError(
                f"MCP server '{self.name}' did not answer within "
                f"{timeout if timeout else 0:.0f}s.",
                server=self.name,
                kind=KIND_TIMEOUT,
                hint=(
                    'raise "timeout" in mcp.json, or check the server process '
                    "is not waiting on input"
                ),
                cause=exc,
            ) from exc

    def _stdio_env(self) -> dict:
        env = dict(os.environ) if self.config.inherit_env else {}
        env.update(self.config.env)
        return env

    async def _connect(self) -> list:
        """Enter the transport + session and return the (paginated) tool list."""
        sdk = _import_sdk()
        self._transport_cm = await self._open_transport(sdk)
        streams = await self._transport_cm.__aenter__()
        # streamablehttp_client (v1) yields (read, write, get_session_id);
        # stdio_client, sse_client and v2 yield (read, write).
        if not isinstance(streams, (tuple, list)) or len(streams) < 2:
            raise McpError(
                f"MCP transport for '{self.name}' returned unexpected streams.",
                server=self.name,
                kind=KIND_CONNECT,
            )
        read, write = streams[0], streams[1]

        self._session_cm = sdk.ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        init_result = await self._session.initialize()
        self.capabilities = self._read_capabilities(init_result)
        return await self._list_all_tools()

    @staticmethod
    def _read_capabilities(init_result) -> dict[str, bool]:
        capabilities = _field(init_result, "capabilities", default=None)
        if capabilities is None:
            # Older servers may omit the field; assume the basics are there
            # and let the call itself fail if they are not.
            return {"resources": True, "prompts": True}
        return {
            name: _field(capabilities, name, default=None) is not None
            for name in ("resources", "prompts")
        }

    async def _open_transport(self, sdk: SimpleNamespace):
        """Pick and build the transport context manager for this server."""
        if self.config.transport == "stdio":
            params = sdk.StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
                env=self._stdio_env(),
            )
            return sdk.stdio_client(params)

        if self.config.transport == "sse":
            if sdk.sse_client is None:
                raise McpError(
                    f"MCP server '{self.name}' uses the SSE transport, which this "
                    "version of the 'mcp' package does not provide.",
                    server=self.name,
                    kind=KIND_CONNECT,
                    hint='use "transport": "http" (Streamable HTTP) instead',
                )
            return await self._with_headers(sdk.sse_client, self.config.url)

        if sdk.streamable_http_client is None:
            raise McpError(
                f"MCP server '{self.name}' uses the http transport, which this "
                "version of the 'mcp' package does not provide.",
                server=self.name,
                kind=KIND_CONNECT,
                hint=_SDK_HINT,
            )
        return await self._with_headers(sdk.streamable_http_client, self.config.url)

    async def _with_headers(self, factory, url: str):
        """Build an HTTP transport, passing auth headers in the SDK's dialect.

        v2 (``streamable_http_client``) takes a caller-owned
        ``httpx2.AsyncClient`` (1.x: ``httpx.AsyncClient``); older transports
        took a ``headers`` kwarg.  The factory signature decides which —
        guessing from ``TypeError`` would silently drop headers for factories
        that accept ``**kwargs``.
        """
        headers = dict(self.config.headers)
        if not headers:
            return factory(url)

        if _accepts(factory, "http_client"):
            sdk_httpx = _http_client_module(factory)
            if sdk_httpx is None:
                raise McpError(
                    f"MCP server '{self.name}' needs headers, but this 'mcp' "
                    "version provides no HTTP client to carry them.",
                    server=self.name,
                    kind=KIND_CONNECT,
                    hint=(
                        'install the http client "mcp" expects (httpx2 or httpx), '
                        'or remove "headers" for this server'
                    ),
                )

            # The header-carrying client is owned (and closed) by us.
            self._http_client_cm = sdk_httpx.AsyncClient(
                headers=headers,
                timeout=sdk_httpx.Timeout(30.0, read=self.config.timeout),
            )
            client = await self._http_client_cm.__aenter__()
            return factory(url, http_client=client)

        if _accepts(factory, "headers"):
            return factory(url, headers=headers)

        raise McpError(
            f"MCP server '{self.name}' could not pass authentication headers "
            "to the transport.",
            server=self.name,
            kind=KIND_CONNECT,
            hint=(
                'upgrade "mcp" so headers can be applied, or unset '
                '"headers" for this server'
            ),
        )

    async def _close_http_client(self) -> None:
        manager, self._http_client_cm = self._http_client_cm, None
        if manager is None:
            return
        try:
            await manager.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - teardown is best-effort
            pass

    async def _list_all_tools(self) -> list:
        tools: list = []
        cursor = None
        while True:
            result = await self._list_tools_page(cursor)
            tools.extend(getattr(result, "tools", None) or [])
            cursor = _field(result, "next_cursor", "nextCursor", default=None)
            if not cursor:
                return tools

    async def _list_tools_page(self, cursor: str | None):
        """One ``tools/list`` page, in whichever pagination dialect the SDK has.

        v1 accepted ``cursor=`` directly; v2 took a ``PaginatedRequestParams``
        through ``params=``.  Passing the wrong keyword is a ``TypeError`` that
        only shows up on servers with more than one page of tools.
        """
        if not cursor:
            return await self._session.list_tools()
        if _accepts(self._session.list_tools, "params"):
            params = _pagination_params(cursor)
            if params is not None:
                return await self._session.list_tools(params=params)
        return await self._session.list_tools(cursor=cursor)

    async def _close_session(self) -> None:
        # Reverse order of construction: session, transport, HTTP client.
        for manager in (self._session_cm, self._transport_cm):
            if manager is None:
                continue
            try:
                await manager.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - broken pipe on teardown is fine
                pass
        await self._close_http_client()

    def _wrap(self, exc: Exception, kind: str, tool_name: str = "") -> McpError:
        if isinstance(exc, McpError):
            return exc
        target = f"tool '{tool_name}' on MCP server" if tool_name else "MCP server"
        if kind == KIND_CONNECT:
            hint = (
                "check that the command exists on PATH and that the server "
                "starts when run manually"
                if self.config.transport == "stdio"
                else "check the url, the headers/token and that the server is reachable"
            )
        elif kind == KIND_TOOL:
            hint = "the server raised or returned an invalid response for this call"
        else:
            hint = None
        # Some SDK exceptions carry no message at all (mcp 1.x raises bare
        # transport errors), which would render as "... failed:".
        detail = str(exc).strip() or type(exc).__name__
        return McpError(
            f"{target} '{self.name}' failed: {detail}",
            server=self.name,
            kind=kind,
            hint=hint,
            cause=exc,
        )
