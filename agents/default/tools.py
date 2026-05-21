from utils.tools_library import get_current_date
from utils.file_utils import load_tool_definition

all_tools = load_tool_definition()

tools = [
    all_tools["get_current_date"]
]

TOOL_MAP = {
    "get_current_date": get_current_date,
}
