from utils.tools_library import (grep_search, get_all_files, read_lines,
                                 file_read, file_write, finish)
from utils.file_utils import load_tool_definition

all_tools = load_tool_definition()

tools = [
    all_tools["grep_search"],
    all_tools["get_all_files"],
    all_tools["read_lines"],
    all_tools["file_read"],
    all_tools["file_write"],
    all_tools["finish"]
]

TOOL_MAP = {
    "grep_search" : grep_search,
    "get_all_files": get_all_files,
    "read_lines": read_lines,
    "file_read": file_read,
    "file_write": file_write,
    "finish": finish,
}
