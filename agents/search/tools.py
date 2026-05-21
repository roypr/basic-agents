from utils.tools_library import get_current_date, web_search, request_get
from utils.file_utils import load_tool_definition

all_tools = load_tool_definition()

tools = [
    all_tools["get_current_date"],
    all_tools["web_search"],
    all_tools["request_get"]
]

TOOL_MAP = {
    "get_current_date" : get_current_date,
    "web_search": web_search,
    "request_get": request_get,
}
