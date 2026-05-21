from utils.file_utils import load_tool_definition
from utils.tools_library import (get_current_date, web_search, request_get, get_all_files, read_lines, 
                                 replace_lines, file_read, file_write, file_edit, file_delete, 
                                 glob_search, grep_search, run_command, finish)



all_tools = load_tool_definition()

tools = [
    all_tools["glob_search"],
    all_tools["grep_search"],
    all_tools["web_search"],
    all_tools["request_get"],
    all_tools["get_all_files"],
    all_tools["read_lines"],
    all_tools["replace_lines"],
    all_tools["file_read"],
    all_tools["file_write"],
    all_tools["file_edit"],
    all_tools["file_delete"],
    all_tools["run_command"],
    all_tools["finish"]
]

TOOL_MAP = {
    "get_current_date" : get_current_date,
    "glob_search" : glob_search,
    "grep_search" : grep_search,
    "web_search": web_search,
    "request_get": request_get,
    "get_all_files": get_all_files,
    "file_read": file_read,
    "file_write": file_write,
    "file_edit": file_edit,
    "file_delete": file_delete,
    "read_lines": read_lines,
    "replace_lines": replace_lines,
    "run_command": run_command,
    "finish": finish,
}
