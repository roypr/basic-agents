from datetime import datetime, timezone


def get_current_date() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("UTC: %Y-%m-%d %H:%M:%S")


tools = [
    {
        "type" : "function",
        "function": {
            "name": "get_current_date",
            "description": "Return the current UTC date and time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    }
]

TOOL_MAP = {
    "get_current_date": get_current_date,
}
