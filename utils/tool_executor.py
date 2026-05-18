import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
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


def execute_tool_call(tc: dict, tool_map: dict, tools: list) -> tuple[dict, str, dict, str]:
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
    max_workers = min(len(tool_calls), 8) if tool_calls else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(partial(execute_tool_call, tool_map=tool_map, tools=tools), tool_calls))
