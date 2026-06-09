import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Lock
from core.tool_registry import validate_args


class ToolExecutor:
    def __init__(self):
        self.tools: dict[str, callable] = {}

    def register_tool(self, name: str, tool_function: callable):
        self.tools[name] = tool_function

    def execute_tool(self, name: str, arguments: dict):
        if name not in self.tools:
            raise KeyError(f"Tool '{name}' is not registered")
        return self.tools[name](**arguments)


_executor: ThreadPoolExecutor | None = None
_executor_lock = Lock()


def shutdown_tool_executor():
    """Shutdown the active thread pool used for tool execution."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
            _executor = None


def execute_tool_call(
    tc: dict, tool_map: dict, tools: list
) -> tuple[dict, str, dict, str]:
    fn_name = tc["function"]["name"]
    raw_args = tc["function"]["arguments"]

    try:
        fn_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        fn_args = {}

    try:
        if tools and isinstance(tools[0], str):
            if fn_name not in tools:
                raise ValueError(f"Unknown tool: {fn_name}")
        else:
            validate_args(fn_name, fn_args, tools)
        result = tool_map[fn_name](**fn_args)
    except KeyError:
        result = f"Error: unknown tool '{fn_name}'"
    except Exception as e:
        result = f"Error: {e}"

    return tc, fn_name, fn_args, str(result)


def execute_tool_calls(tool_calls: list, tool_map: dict, tools: list) -> list:
    if not tool_calls:
        return []

    max_workers = min(len(tool_calls), 8)
    global _executor

    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
        _executor = ThreadPoolExecutor(max_workers=max_workers)
        executor = _executor

    # Safety timeout per tool call — ensures we never hang forever,
    # even if the underlying tool function (e.g. subprocess on Windows)
    # fails to terminate cleanly.
    PER_CALL_TIMEOUT = 300  # 5 minutes

    # Submit all tasks first so they run concurrently
    futures = {}
    for tc in tool_calls:
        f = executor.submit(execute_tool_call, tc, tool_map=tool_map, tools=tools)
        futures[id(tc)] = f

    results = []
    try:
        for tc in tool_calls:
            f = futures[id(tc)]
            try:
                result = f.result(timeout=PER_CALL_TIMEOUT)
                results.append(result)
            except FutureTimeoutError:
                fn_name = tc["function"]["name"]
                results.append(
                    (
                        tc,
                        fn_name,
                        {},
                        f"Error: tool call '{fn_name}' timed out after {PER_CALL_TIMEOUT}s",
                    )
                )
            except Exception as e:
                fn_name = tc["function"]["name"]
                results.append(
                    (tc, fn_name, {}, f"Error: tool call '{fn_name}' failed: {e}")
                )
    finally:
        with _executor_lock:
            if _executor is executor:
                executor.shutdown(wait=False)
                _executor = None

    return results
