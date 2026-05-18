def validate_args(tool_name: str, args: dict, tools: list) -> None:
    tool_def = next((tool for tool in tools if tool["function"]["name"] == tool_name), None)
    if not tool_def:
        raise ValueError(f"Unknown tool: {tool_name}")

    required_params = tool_def["function"].get("parameters", {}).get("required", [])
    missing = [param for param in required_params if param not in args]
    if missing:
        raise ValueError(f"Missing required arguments for '{tool_name}': {', '.join(missing)}")
