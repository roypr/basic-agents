from utils.tools_library import (glob_search, grep_search, 
                                 file_write, file_read, file_edit, get_all_files, 
                                 read_lines, remove_lines, replace_lines, 
                                 web_search, request_get, finish)
from utils.file_utils import load_tool_definition

all_tools = load_tool_definition()

tools = [
    all_tools["glob_search"],
    all_tools["grep_search"],
    all_tools["web_search"],
    all_tools["request_get"],
    all_tools["get_all_files"],
    all_tools["read_lines"],
    all_tools["remove_lines"],
    all_tools["replace_lines"],
    all_tools["file_read"],
    all_tools["file_write"],
    all_tools["file_edit"],
    all_tools["finish"]
]

TOOL_MAP = {
    "glob_search" : glob_search,
    "grep_search" : grep_search,
    "web_search": web_search,
    "request_get": request_get,
    "get_all_files": get_all_files,
    "file_read": file_read,
    "file_write": file_write,
    "file_edit": file_edit,
    "read_lines": read_lines,
    "remove_lines" : remove_lines,
    "replace_lines": replace_lines,
    "finish": finish,
}
